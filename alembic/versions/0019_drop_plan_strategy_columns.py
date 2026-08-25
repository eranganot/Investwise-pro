"""drop plans.strategy / plans.strategy_sleeve_pct — one sleeve was never enough

`plans.strategy` + `plans.strategy_sleeve_pct` can express exactly ONE sleeve.
`plan_sleeves` (migration 0014) replaced them with N rows, and C1-C4 moved every
reader across. What was left behind was not a spare copy but a SECOND ANSWER,
and nothing kept the two in step.

Measured on the live book the day this was written:

    plans.strategy            = btm_swing_dip @ 15.0%     <- stale
    plan_sleeves              = btm_swing_dip @ 38.4%
                                btm_trend_soxl @ 25.6%

The columns were 23 points out of date and still being consulted as a fallback
by the tax harvester's sleeve-exclusion set, the strategy-signal service and the
sleeve-drift card. Each of those fallbacks existed to make the protection "never
weaker than it was in C1"; by the end they made it weaker, because they would
have computed from 15% a book that actually ran 38.4%.

The one-shot backfills that populated `plan_sleeves` from these columns go with
them. Both were guarded by their own KVSetting key and both have been spent on
every live deploy -- production logs "0 sleeve(s) created" on every boot. Those
keys are deliberately left in the KV store: they are the record that the
migration happened.

NOT REVERSIBLE IN ANY MEANINGFUL SENSE. `downgrade` recreates the columns, but
it cannot recreate their contents, and the contents were wrong. A book that
needs them back should be read out of `plan_sleeves`, which is the point.

Revision ID: 0019_drop_plan_strategy_columns
Revises: 0018_transaction_ledger
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_drop_plan_strategy_columns"
down_revision = "0018_transaction_ledger"
branch_labels = None
depends_on = None

_TABLE = "plans"
_COLUMNS = ("strategy", "strategy_sleeve_pct")


def _present(bind, column: str) -> bool:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(_TABLE))


def upgrade() -> None:
    # Guarded the same way 0013, 0014 and 0015 are, for the reason that broke
    # the chain before: 0001_initial runs `Base.metadata.create_all`, so on a
    # fresh database the table is built from the CURRENT model -- which no
    # longer has these columns at all. Dropping what was never created is an
    # error on some backends, so ask first.
    bind = op.get_bind()
    for col in _COLUMNS:
        if _present(bind, col):
            op.drop_column(_TABLE, col)


def downgrade() -> None:
    # Shape only. The data is not recoverable and was not worth recovering --
    # see the module docstring.
    bind = op.get_bind()
    if not _present(bind, "strategy"):
        op.add_column(_TABLE, sa.Column("strategy", sa.String(length=40), nullable=True))
    if not _present(bind, "strategy_sleeve_pct"):
        op.add_column(_TABLE, sa.Column("strategy_sleeve_pct", sa.Float(), nullable=True))
