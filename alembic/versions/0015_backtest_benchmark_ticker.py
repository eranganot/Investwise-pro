"""strategy_backtests.benchmark_ticker — an excess is only true against one benchmark

`metrics.excess_cagr_pct` is measured against `settings.benchmark_ticker`, but
the row never recorded which ticker that was. Change the setting and every
stored excess keeps rendering, now under a label it was not measured against —
a wrong number rather than a stale one.

Recording it lets `_is_stale` invalidate on a benchmark change, the same way it
already invalidates on an engine-version change.

Rows written before engine a4 carry an empty string. That is deliberately NOT
treated as a mismatch: the a3 -> a4 bump already marks them stale, and guessing
a benchmark for them would invent the very fact this column exists to record.

Revision ID: 0015_backtest_benchmark_ticker
Revises: 0014_plan_sleeves
Create Date: 2026-08-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_backtest_benchmark_ticker"
down_revision = "0014_plan_sleeves"
branch_labels = None
depends_on = None

_TABLE = "strategy_backtests"
_COLUMN = "benchmark_ticker"


def _has_column(bind) -> bool:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return True          # nothing to add to; treat as done
    return any(c["name"] == _COLUMN for c in insp.get_columns(_TABLE))


def upgrade() -> None:
    # Guarded the way 0013 and 0014 are, for the reason that broke the chain
    # before: 0001_initial runs `Base.metadata.create_all`, so on a fresh
    # database the table is built from TODAY's models and already has this
    # column by the time this revision runs. Without the guard `alembic upgrade
    # head` dies here on "duplicate column" and never reaches the ones after it.
    bind = op.get_bind()
    if _has_column(bind):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=16),
                                    nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in insp.get_table_names() and any(
            c["name"] == _COLUMN for c in insp.get_columns(_TABLE)):
        op.drop_column(_TABLE, _COLUMN)
