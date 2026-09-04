"""plans.cash_target_pct — dry powder the book is managed to, rather than a residue

The book targeted 0% cash by never mentioning cash, and held 2.92% of it.
`/api/v1/mix` on 2026-09-04 reported a Cash drift of +0.0292 against a target
that has no Cash key at all, and `rebalance_required: false` alongside it: a
class the target never modelled cannot drift, so ILS 618.65 — more than twice
`MIN_TRADE_ILS` — sat there with nothing having an opinion about it.

This column makes the reserve a decision. 7.5 means 7.5% of NAV, held on
purpose so there is something to buy an opportunity with.

A PERCENTAGE, not a fraction, because every other `_pct` column on this table
is one: `target_roi_pct` is 30.0 for 30%. `book_target` divides by 100 exactly
once, so no other reader has to know.

NULLABLE, and null is not zero. Null means nobody has been asked; zero means
someone chose none. `book_target` manages both as no reserve, but a UI that
cannot tell them apart will keep asking, or worse, will stop.

GUARDED, the way 0013-0016 and 0018-0020 are, for the reason all of them cite:
`0001_initial` runs `Base.metadata.create_all`, so on a fresh database this
column already exists, built from today's model, before this revision runs.

MIRRORED INTO `main.py`. That is not a nicety here — until the pre-deploy
migration step lands, the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` list in
`main.py` is the mechanism that actually reaches production, and this exact
step has been missed twice: 0011 took out three endpoints, and 0015 degraded
silently for ten days. `tests/test_startup_ddl_covers_migrations.py` fails if
the mirror is absent.

Revision ID: 0021_plan_cash_target
Revises: 0020_solver_verdicts
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_plan_cash_target"
down_revision = "0020_solver_verdicts"
branch_labels = None
depends_on = None

_TABLE = "plans"
_COLUMN = "cash_target_pct"


def _columns(bind) -> set:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _columns(bind)
    if not have:
        # No `plans` table at all: `0001_initial` builds it from the models, so
        # this only happens on a database that never ran that revision.
        return
    if _COLUMN not in have:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        op.drop_column(_TABLE, _COLUMN)
