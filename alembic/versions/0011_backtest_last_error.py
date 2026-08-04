"""strategy_backtests.last_error / last_error_at

A failed refresh used to overwrite the row, wiping the metrics it could no
longer reproduce. Then one provider hiccup abstained all seven strategies at
once and left nothing behind -- ten years of computed history gone to a minute
of downtime. "No longer measurable" and "the feed was down" are different
failures and only the first justifies losing the numbers, so the failure is now
recorded alongside the last good measurement instead of replacing it.

Revision ID: 0011_backtest_last_error
Revises: 0010_strategy_signal_state
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_backtest_last_error"
down_revision = "0010_strategy_signal_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("strategy_backtests")}
    if "last_error" not in cols:
        op.add_column("strategy_backtests",
                      sa.Column("last_error", sa.String(length=255),
                                nullable=False, server_default=""))
    if "last_error_at" not in cols:
        op.add_column("strategy_backtests",
                      sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("strategy_backtests", "last_error_at")
    op.drop_column("strategy_backtests", "last_error")
