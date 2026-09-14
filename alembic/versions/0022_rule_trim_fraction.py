"""trading_rules.trim_fraction — a take-profit sells a slice, not the position

`execution_plan` returned `sell_position` for a fired `take_profit` -- a full
exit -- while the notes the app writes on its own take-profit rules say,
verbatim: *"Takes some off the table if it climbs ~10% more."* Live on this book
on 2026-09-14, on QQQ at 791.02 and on USO at 168.17. A card may not claim a
change it does not produce, and this one claimed the opposite of what it did.

The fix makes the firing a partial trim and re-arms the rule one rung higher, so
a winner is sold in slices at successively higher prices. This column is the
per-rule size of that slice.

A FRACTION, not a percentage, unlike the `_pct` columns on `plans`: every reader
of it multiplies a share count directly (`qty * frac`), and a column whose only
consumer would divide it by 100 first is a unit waiting to be forgotten.

NULLABLE, and null is not zero. Null means "use the house default"
(`rules_service.TAKE_PROFIT_FRACTION`, 0.25); zero would mean a rule that sells
nothing, which is a rule that does not exist. `take_profit_fraction` clamps to
(0, 1] and falls back to the default for anything outside it, so a bad row
degrades to the house size rather than to a no-op or a liquidation.

GUARDED, the way 0013-0016 and 0018-0021 are, for the reason all of them cite:
`0001_initial` runs `Base.metadata.create_all`, so on a fresh database this
column already exists, built from today's model, before this revision runs.

MIRRORED INTO `main.py`. Until the pre-deploy migration step lands, the
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` list in `main.py` is the mechanism
that actually reaches production, and this exact step has been missed twice:
0011 took out three endpoints, and 0015 degraded silently for ten days.
`tests/test_startup_ddl_covers_migrations.py` fails if the mirror is absent.

Revision ID: 0022_rule_trim_fraction
Revises: 0021_plan_cash_target
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_rule_trim_fraction"
down_revision = "0021_plan_cash_target"
branch_labels = None
depends_on = None

_TABLE = "trading_rules"
_COLUMN = "trim_fraction"


def _columns(bind) -> set:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _columns(bind)
    if not have:
        # No `trading_rules` table at all: `0001_initial` builds it from the
        # models, so this only happens on a database that never ran it.
        return
    if _COLUMN not in have:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        op.drop_column(_TABLE, _COLUMN)
