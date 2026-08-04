"""plans.strategy_sleeve_pct (how much of the book a strategy governs)

A rule-based strategy is a sleeve, not a portfolio. Without this column the only
expressible choice was all-or-nothing: applying "trend-filtered 3x Nasdaq" put
the entire book into TQQQ, because the model basket reads as 100% TQQQ in
isolation. Nobody runs a leveraged strategy that way.

Revision ID: 0012_plan_strategy_sleeve
Revises: 0011_backtest_last_error
Create Date: 2026-08-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_plan_strategy_sleeve"
down_revision = "0011_backtest_last_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    if "strategy_sleeve_pct" not in cols:
        op.add_column("plans",
                      sa.Column("strategy_sleeve_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "strategy_sleeve_pct")
