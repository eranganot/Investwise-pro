"""standing_orders -- a monthly deposit into cash that records itself

Eran, 2026-09-20: "I want to allow and create repetitive contribution (standing
order per monthly) to my cash." Chosen behaviour: on the day, the app credits
the amount to cash on its own and sends a push; the order can be edited or
paused.

Each run writes an ordinary `contributions` row, so the deposit is visible to
everything that already distinguishes money-in from return (the gain figure,
`nav_history`'s flow detector, `contribution_rate`), and credits the cash row in
the same commit that advances `next_run_on`.

GUARDED like 0016, for the reason it gives: `0001_initial` runs
`Base.metadata.create_all`, so on a fresh database this table already exists
before this revision runs. A new TABLE needs no `main.py` DDL mirror --
`create_all` builds missing tables; only added columns need the list.

Revision ID: 0023_standing_orders
Revises: 0022_rule_trim_fraction
Create Date: 2026-09-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0023_standing_orders"
down_revision = "0022_rule_trim_fraction"
branch_labels = None
depends_on = None

_TABLE = "standing_orders"


def upgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_on", sa.String(length=10), nullable=False),
        sa.Column("last_run_on", sa.String(length=10), nullable=True),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_standing_orders_subject", _TABLE, ["subject"])
    op.create_index("ix_standing_orders_next_run_on", _TABLE, ["next_run_on"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return
    op.drop_index("ix_standing_orders_next_run_on", table_name=_TABLE)
    op.drop_index("ix_standing_orders_subject", table_name=_TABLE)
    op.drop_table(_TABLE)
