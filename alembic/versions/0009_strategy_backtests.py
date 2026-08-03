"""strategy_backtests (measured strategy results, precomputed nightly)

Backtesting a strategy pulls ten years of daily closes for every ticker it
references and simulates the rule over them. Doing that inside the /strategies
request would put a network fan-out on a page load and make the strategy list
fail whenever a price provider is down. The nightly job writes here; the route
reads here.

Revision ID: 0009_strategy_backtests
Revises: 0008_contributions
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_strategy_backtests"
down_revision = "0008_contributions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "strategy_backtests" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "strategy_backtests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("engine_version", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Why a run produced no number, rather than leaving a stale one in place.
        sa.Column("reason", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("detail", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("robustness", sa.JSON(), nullable=True),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("period_start", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("period_end", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("observations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_backtests_strategy_id", "strategy_backtests", ["strategy_id"])
    op.create_index("ix_strategy_backtests_computed_at", "strategy_backtests", ["computed_at"])


def downgrade() -> None:
    op.drop_table("strategy_backtests")
