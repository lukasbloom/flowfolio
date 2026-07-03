"""instrument delete cascade — add ON DELETE CASCADE to instrument-owned child FKs

Deleting an instrument that has cached price_quote
rows (or any other instrument-owned child) used to fail with a FOREIGN KEY
violation, surfacing as a 500. This migration adds ON DELETE CASCADE to the four
instrument_id FKs that are instrument-OWNED caches/config/attachments:

  - price_quote        (cached quotes)
  - apy_config         (yield config)
  - holding_tag        (tag attachments)
  - concentration_mute (mute flag)

The transaction.instrument_id FK is intentionally LEFT WITHOUT ondelete — a
referenced instrument that still has transactions must be BLOCKED (409) by the
delete endpoint, never cascaded (FIFO lots, realized gains, and cost basis all
hang off transactions). That BLOCK is enforced in app code, not the FK.

SQLite cannot ALTER a foreign key in place, so each table is recreated via
batch_alter_table(recreate="always"), dropping and recreating the instrument_id
FK with (down) or without (downgrade) ON DELETE CASCADE. Money/text columns are
untouched — only FK ondelete behavior changes.

Revision ID: 01a_instrument_delete_cascade
Revises: baseline
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op

revision = "01a_instrument_delete_cascade"
down_revision = "baseline"
branch_labels = None
depends_on = None


# (table, fk_constraint_name) for the four instrument-owned child tables.
_CHILD_TABLES = (
    ("price_quote", "fk_price_quote_instrument_id"),
    ("apy_config", "fk_apy_config_instrument_id"),
    ("holding_tag", "fk_holding_tag_instrument_id"),
    ("concentration_mute", "fk_concentration_mute_instrument_id"),
)


def upgrade() -> None:
    for table, fk_name in _CHILD_TABLES:
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.create_foreign_key(
                fk_name,
                "instrument",
                ["instrument_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    for table, fk_name in _CHILD_TABLES:
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.create_foreign_key(
                fk_name,
                "instrument",
                ["instrument_id"],
                ["id"],
            )
