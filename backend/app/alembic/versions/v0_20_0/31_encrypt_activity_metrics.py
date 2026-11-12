"""31: encrypt the activities' metric columns with CipherStash

The 19 metric columns of ``activities`` become EQL v4 columns: CipherStash
ciphertext that PostgreSQL can sort without the plaintext.

- ``distance``, ``calories``, ``elevation_gain`` and ``average_hr`` are
  ``IntegerOrd``, and ``total_timer_time`` and ``pace`` are ``NumericOrd``:
  the activity list sorts by them.
- The other 13 are ``Integer`` or ``Numeric``: stored only.

SQL cannot add encrypted values, so the summaries and the gear totals add the
metrics up in Python from this revision on.

PostgreSQL has no cast from a number to an EQL type, so each column is emptied
and changed, and the rows are encrypted here in Python and written back, in
batches.

The database must already have EQL v4 installed. Migration 29 checks that.
Encrypting needs CipherStash credentials: the ``CS_*`` environment variables,
or a profile from ``stash auth login``.

``downgrade()`` decrypts the rows and restores the plaintext columns.

Revision ID: 5b9e3c7a2d41
Revises: 7d4f2a9c1b83
Create Date: 2026-11-12 00:00:00.000000

"""

import dataclasses
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated

import sqlalchemy as sa
import stackencrypt
from stackencrypt import Integer, IntegerOrd, Numeric, NumericOrd

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b9e3c7a2d41"
down_revision: str | None = "7d4f2a9c1b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A snapshot of the encrypted metric fields of the Activity model, so that the
# migration does not follow later edits to app code. The table name and the
# markers must match the model: they decide how each value is encrypted.
@stackencrypt.model("activities")
@dataclasses.dataclass
class _ActivityMetrics:
    distance: Annotated[int, IntegerOrd]
    total_elapsed_time: Annotated[Decimal, Numeric]
    total_timer_time: Annotated[Decimal, NumericOrd]
    elevation_gain: Annotated[int | None, IntegerOrd]
    elevation_loss: Annotated[int | None, Integer]
    pace: Annotated[Decimal | None, NumericOrd]
    average_speed: Annotated[Decimal | None, Numeric]
    max_speed: Annotated[Decimal | None, Numeric]
    average_power: Annotated[int | None, Integer]
    max_power: Annotated[int | None, Integer]
    normalized_power: Annotated[int | None, Integer]
    average_hr: Annotated[int | None, IntegerOrd]
    max_hr: Annotated[int | None, Integer]
    average_cad: Annotated[int | None, Integer]
    max_cad: Annotated[int | None, Integer]
    workout_feeling: Annotated[int | None, Integer]
    workout_rpe: Annotated[int | None, Integer]
    calories: Annotated[int | None, IntegerOrd]
    total_cycles: Annotated[int | None, Integer]


_INTEGER = "INTEGER"
_DECIMAL = "NUMERIC(20, 10)"

# Each encrypted column, its EQL type, its plaintext type, and whether it is
# NOT NULL.
_COLUMNS = (
    ("distance", "public.eql_v4_integer_ord", _INTEGER, True),
    ("total_elapsed_time", "public.eql_v4_numeric", _DECIMAL, True),
    ("total_timer_time", "public.eql_v4_numeric_ord", _DECIMAL, True),
    ("elevation_gain", "public.eql_v4_integer_ord", _INTEGER, False),
    ("elevation_loss", "public.eql_v4_integer", _INTEGER, False),
    ("pace", "public.eql_v4_numeric_ord", _DECIMAL, False),
    ("average_speed", "public.eql_v4_numeric", _DECIMAL, False),
    ("max_speed", "public.eql_v4_numeric", _DECIMAL, False),
    ("average_power", "public.eql_v4_integer", _INTEGER, False),
    ("max_power", "public.eql_v4_integer", _INTEGER, False),
    ("normalized_power", "public.eql_v4_integer", _INTEGER, False),
    ("average_hr", "public.eql_v4_integer_ord", _INTEGER, False),
    ("max_hr", "public.eql_v4_integer", _INTEGER, False),
    ("average_cad", "public.eql_v4_integer", _INTEGER, False),
    ("max_cad", "public.eql_v4_integer", _INTEGER, False),
    ("workout_feeling", "public.eql_v4_integer", _INTEGER, False),
    ("workout_rpe", "public.eql_v4_integer", _INTEGER, False),
    ("calories", "public.eql_v4_integer_ord", _INTEGER, False),
    ("total_cycles", "public.eql_v4_integer", _INTEGER, False),
)
_NAMES = [column for column, _, _, _ in _COLUMNS]

# The order index the SDK adds to the model for each sortable column. The
# names and definitions must match the model's, or autogenerate finds a
# difference.
_SORTED = ("distance", "total_timer_time", "elevation_gain", "pace", "average_hr", "calories")

# Rows encrypted or decrypted in one call.
_BATCH = 1000


def _batches(rows: list[dict]) -> list[list[dict]]:
    return [rows[start : start + _BATCH] for start in range(0, len(rows), _BATCH)]


def upgrade() -> None:
    """Encrypt the activities' metric columns."""
    conn = op.get_bind()
    columns = ", ".join(_NAMES)
    select = sa.text(f"SELECT id, {columns} FROM activities")  # noqa: S608 - built from the fixed column names above
    rows = [dict(row) for row in conn.execute(select).mappings()]
    cipher = stackencrypt.Cipher(_ActivityMetrics)
    encrypted = [row for batch in _batches(rows) for row in cipher.encrypt_rows(batch)]

    for column, eql_type, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("activities", column, nullable=True)
        op.execute(f"ALTER TABLE activities ALTER COLUMN {column} TYPE {eql_type} USING NULL")

    if encrypted:
        assignments = ", ".join(
            f"{column} = CAST(:{column} AS jsonb)::{eql_type}" for column, eql_type, _, _ in _COLUMNS
        )
        update = sa.text(f"UPDATE activities SET {assignments} WHERE id = :id")  # noqa: S608 - built from the fixed column names above
        for batch in _batches(encrypted):
            conn.execute(update, batch)

    for column, _, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("activities", column, nullable=False)
    for column in _SORTED:
        op.create_index(
            f"ix_activities_{column}_ope",
            "activities",
            [sa.text(f"eql_v4.ord_term({column})")],
            postgresql_using="btree",
        )


def downgrade() -> None:
    """Decrypt the activities' metric columns back to plaintext."""
    conn = op.get_bind()
    columns = ", ".join(f"{column}::jsonb::text AS {column}" for column in _NAMES)
    select = sa.text(f"SELECT id, {columns} FROM activities")  # noqa: S608 - built from the fixed column names above
    rows = [dict(row) for row in conn.execute(select).mappings()]
    # A NULL has no value to decrypt, and every row in one call must hold the
    # same fields. So rows are grouped by which fields they have.
    groups: dict[tuple[str, ...], list[dict]] = {}
    for row in rows:
        present = tuple(column for column in _NAMES if row[column] is not None)
        groups.setdefault(present, []).append({key: value for key, value in row.items() if value is not None})
    cipher = stackencrypt.Cipher(_ActivityMetrics)
    decrypted = [
        {**dict.fromkeys(_NAMES), **row}
        for group in groups.values()
        for batch in _batches(group)
        for row in cipher.decrypt_rows(batch)
    ]

    for column in _SORTED:
        op.drop_index(f"ix_activities_{column}_ope", table_name="activities")
    for column, _, plaintext_type, not_null in _COLUMNS:
        if not_null:
            op.alter_column("activities", column, nullable=True)
        op.execute(f"ALTER TABLE activities ALTER COLUMN {column} TYPE {plaintext_type} USING NULL")

    if decrypted:
        assignments = ", ".join(f"{column} = :{column}" for column in _NAMES)
        update = sa.text(f"UPDATE activities SET {assignments} WHERE id = :id")  # noqa: S608 - built from the fixed column names above
        for batch in _batches(decrypted):
            conn.execute(update, batch)

    for column, _, _, not_null in _COLUMNS:
        if not_null:
            op.alter_column("activities", column, nullable=False)
