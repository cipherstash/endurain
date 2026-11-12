"""30: encrypt the activities' text columns with CipherStash

``activities.name``, ``city``, ``town``, ``country``, ``description`` and
``private_notes`` become EQL v4 columns: CipherStash ciphertext that
PostgreSQL can search without the plaintext.

- ``name``, ``city``, ``town`` and ``country`` are ``TextSearch``: searched by
  match and sorted.
- ``description`` and ``private_notes`` are ``Text``: stored only.

``timezone`` stays plaintext, because the summaries pass it to PostgreSQL's
``timezone()`` function.

PostgreSQL has no cast from text to an EQL type, so each column is emptied and
changed, and the rows are encrypted here in Python and written back. The rows
are encrypted in batches, so a large table does not make one large key request.

The database must already have EQL v4 installed. Migration 29 checks that.
Encrypting needs CipherStash credentials: the ``CS_*`` environment variables,
or a profile from ``stash auth login``.

``downgrade()`` decrypts the rows and restores the plaintext columns.

Revision ID: 7d4f2a9c1b83
Revises: 3e8b1c5f9a26
Create Date: 2026-11-12 00:00:00.000000

"""

import dataclasses
from collections.abc import Sequence
from typing import Annotated

import sqlalchemy as sa
import stackencrypt
from stackencrypt import Text, TextSearch

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d4f2a9c1b83"
down_revision: str | None = "3e8b1c5f9a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A snapshot of the encrypted fields of the Activity model, so that the
# migration does not follow later edits to app code. The table name and the
# markers must match the model: they decide how each value is encrypted.
@stackencrypt.model("activities")
@dataclasses.dataclass
class _ActivityText:
    name: Annotated[str | None, TextSearch]
    description: Annotated[str | None, Text]
    private_notes: Annotated[str | None, Text]
    city: Annotated[str | None, TextSearch]
    town: Annotated[str | None, TextSearch]
    country: Annotated[str | None, TextSearch]


# Each encrypted column, its EQL type, and its plaintext type for downgrade().
_COLUMNS = (
    ("name", "public.eql_v4_text_search", "VARCHAR(250)"),
    ("description", "public.eql_v4_text", "VARCHAR(2500)"),
    ("private_notes", "public.eql_v4_text", "VARCHAR(2500)"),
    ("city", "public.eql_v4_text_search", "VARCHAR(250)"),
    ("town", "public.eql_v4_text_search", "VARCHAR(250)"),
    ("country", "public.eql_v4_text_search", "VARCHAR(250)"),
)
_NAMES = [column for column, _, _ in _COLUMNS]

# The indexes the SDK adds to the model for each TextSearch column: equality,
# order, and match. The names and definitions must match the model's, or
# autogenerate finds a difference.
_SEARCH_COLUMNS = ("name", "city", "town", "country")
_INDEXES = (("eq", "eq_term", "hash"), ("ope", "ord_term", "btree"), ("match", "match_term", "gin"))

# Rows encrypted or decrypted in one call.
_BATCH = 1000


def _batches(rows: list[dict]) -> list[list[dict]]:
    return [rows[start : start + _BATCH] for start in range(0, len(rows), _BATCH)]


def upgrade() -> None:
    """Encrypt the activities' text columns."""
    conn = op.get_bind()
    columns = ", ".join(_NAMES)
    rows = [dict(row) for row in conn.execute(sa.text(f"SELECT id, {columns} FROM activities")).mappings()]  # noqa: S608 - built from the fixed column names above
    cipher = stackencrypt.Cipher(_ActivityText)
    encrypted = [row for batch in _batches(rows) for row in cipher.encrypt_rows(batch)]

    for column, eql_type, _ in _COLUMNS:
        op.execute(f"ALTER TABLE activities ALTER COLUMN {column} TYPE {eql_type} USING NULL")

    if encrypted:
        assignments = ", ".join(f"{column} = CAST(:{column} AS jsonb)::{eql_type}" for column, eql_type, _ in _COLUMNS)
        statement = sa.text(f"UPDATE activities SET {assignments} WHERE id = :id")  # noqa: S608 - built from the fixed column names above
        for batch in _batches(encrypted):
            conn.execute(statement, batch)

    for column in _SEARCH_COLUMNS:
        for kind, function, method in _INDEXES:
            op.create_index(
                f"ix_activities_{column}_{kind}",
                "activities",
                [sa.text(f"eql_v4.{function}({column})")],
                postgresql_using=method,
            )


def downgrade() -> None:
    """Decrypt the activities' text columns back to plaintext."""
    conn = op.get_bind()
    columns = ", ".join(f"{column}::jsonb::text AS {column}" for column in _NAMES)
    rows = [dict(row) for row in conn.execute(sa.text(f"SELECT id, {columns} FROM activities")).mappings()]  # noqa: S608 - built from the fixed column names above
    # A NULL has no value to decrypt, and every row in one call must hold the
    # same fields. So rows are grouped by which fields they have.
    groups: dict[tuple[str, ...], list[dict]] = {}
    for row in rows:
        present = tuple(column for column in _NAMES if row[column] is not None)
        groups.setdefault(present, []).append({key: value for key, value in row.items() if value is not None})
    cipher = stackencrypt.Cipher(_ActivityText)
    # A row with no encrypted value is written back as all NULL, with no call.
    decrypted = [
        {**dict.fromkeys(_NAMES), **row}
        for present, group in groups.items()
        for batch in _batches(group)
        for row in (cipher.decrypt_rows(batch) if present else batch)
    ]

    for column in _SEARCH_COLUMNS:
        for kind, _, _ in _INDEXES:
            op.drop_index(f"ix_activities_{column}_{kind}", table_name="activities")
    for column, _, plaintext_type in _COLUMNS:
        op.execute(f"ALTER TABLE activities ALTER COLUMN {column} TYPE {plaintext_type} USING NULL")

    if decrypted:
        assignments = ", ".join(f"{column} = :{column}" for column in _NAMES)
        statement = sa.text(f"UPDATE activities SET {assignments} WHERE id = :id")  # noqa: S608 - built from the fixed column names above
        for batch in _batches(decrypted):
            conn.execute(statement, batch)
