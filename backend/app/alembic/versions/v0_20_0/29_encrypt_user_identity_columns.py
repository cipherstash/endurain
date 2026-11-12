"""29: encrypt the users' identity columns with CipherStash

``users.name``, ``users.username``, ``users.email`` and ``users.city`` become
EQL v4 columns: CipherStash ciphertext that PostgreSQL can search without the
plaintext.

- ``email`` is ``TextEq``: searched by equality.
- ``username`` is ``TextSearch``: searched by equality and by match, and sorted.
- ``name`` and ``city`` are ``Text``: stored only.

The rows already in the table are plaintext, at least the admin user that
v0.1.4 inserts. PostgreSQL has no cast from text to an EQL type, so each column
is emptied and changed, and the rows are encrypted here in Python and written
back. The unique indexes move from the columns to their equality terms,
because an encrypted value has new random ciphertext each time.

The database must already have EQL v4 installed (the ``eql_v4`` schema and the
``public.eql_v4_*`` types). The migration stops with an error if it is not.
Encrypting needs CipherStash credentials: the ``CS_*`` environment variables,
or a profile from ``stash auth login``.

``downgrade()`` decrypts the rows and restores the plaintext columns and their
unique indexes.

Revision ID: 3e8b1c5f9a26
Revises: c91ac2dad0f0
Create Date: 2026-11-11 00:00:00.000000

"""

import dataclasses
from collections.abc import Sequence
from typing import Annotated

import sqlalchemy as sa
import stackencrypt
from stackencrypt import Text, TextEq, TextSearch

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e8b1c5f9a26"
down_revision: str | None = "c91ac2dad0f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A snapshot of the encrypted fields of the Users model, so that the migration
# does not follow later edits to app code. The table name and the markers must
# match the model: they decide how each value is encrypted.
@stackencrypt.model("users")
@dataclasses.dataclass
class _UserIdentity:
    name: Annotated[str, Text]
    username: Annotated[str, TextSearch]
    email: Annotated[str, TextEq]
    city: Annotated[str | None, Text]


# Each encrypted column, its EQL type, and whether it is NOT NULL.
_COLUMNS = (
    ("name", "public.eql_v4_text", True),
    ("username", "public.eql_v4_text_search", True),
    ("email", "public.eql_v4_text_eq", True),
    ("city", "public.eql_v4_text", False),
)


def upgrade() -> None:
    """Encrypt the users' name, username, email and city."""
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT to_regtype('public.eql_v4_text_search')")).scalar() is None:
        raise RuntimeError(
            "EQL v4 is not installed in this database. Install the EQL v4 SQL bundle "
            "(cipherstash-encrypt-v4.sql) first, then run the migration again."
        )

    rows = [dict(row) for row in conn.execute(sa.text("SELECT id, name, username, email, city FROM users")).mappings()]
    # One batch of key requests for every row. No request is made for no rows.
    encrypted = stackencrypt.Cipher(_UserIdentity).encrypt_rows(rows)

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    for column, eql_type, not_null in _COLUMNS:
        if not_null:
            op.alter_column("users", column, nullable=True)
        op.execute(f"ALTER TABLE users ALTER COLUMN {column} TYPE {eql_type} USING NULL")

    if encrypted:
        conn.execute(
            sa.text(
                "UPDATE users SET"
                " name = CAST(:name AS jsonb)::public.eql_v4_text,"
                " username = CAST(:username AS jsonb)::public.eql_v4_text_search,"
                " email = CAST(:email AS jsonb)::public.eql_v4_text_eq,"
                " city = CAST(:city AS jsonb)::public.eql_v4_text"
                " WHERE id = :id"
            ),
            encrypted,
        )

    for column, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("users", column, nullable=False)
    # The equality terms are the same for equal plaintexts, so these unique
    # indexes refuse a duplicate email or username, and serve equality search.
    op.create_index("ix_users_email", "users", [sa.text("eql_v4.eq_term(email)")], unique=True)
    op.create_index("ix_users_username", "users", [sa.text("eql_v4.eq_term(username)")], unique=True)
    # The username's order and match indexes, for sorting and match search.
    op.create_index("ix_users_username_ope", "users", [sa.text("eql_v4.ord_term(username)")])
    op.create_index(
        "ix_users_username_match", "users", [sa.text("eql_v4.match_term(username)")], postgresql_using="gin"
    )


def downgrade() -> None:
    """Decrypt the users' name, username, email and city back to plaintext."""
    conn = op.get_bind()
    rows = [
        dict(row)
        for row in conn.execute(
            sa.text(
                "SELECT id, name::jsonb::text AS name, username::jsonb::text AS username,"
                " email::jsonb::text AS email, city::jsonb::text AS city FROM users"
            )
        ).mappings()
    ]
    # A NULL city has no value to decrypt, so rows are grouped by whether
    # they have one: every row in one call must hold the same fields.
    with_city = [row for row in rows if row["city"] is not None]
    without_city = [{key: value for key, value in row.items() if key != "city"} for row in rows if row["city"] is None]
    cipher = stackencrypt.Cipher(_UserIdentity)
    decrypted = [*cipher.decrypt_rows(with_city), *cipher.decrypt_rows(without_city)]

    op.drop_index("ix_users_username_match", table_name="users")
    op.drop_index("ix_users_username_ope", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    for column, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("users", column, nullable=True)
        op.execute(f"ALTER TABLE users ALTER COLUMN {column} TYPE VARCHAR(250) USING NULL")

    if decrypted:
        conn.execute(
            sa.text("UPDATE users SET name = :name, username = :username, email = :email, city = :city WHERE id = :id"),
            [{"city": None, **row} for row in decrypted],
        )

    for column, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("users", column, nullable=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
