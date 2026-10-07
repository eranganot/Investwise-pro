"""plans.thresholds -- the card thresholds as one settings object (PR 104d)

Eran, 2026-10-07: "I want every decision to be controlled and changeable
through the app." After the goal's two limits (0027), the rest of the numbers
that decide which cards appear and how big they are still lived as constants:
the smallest card (50 ILS tax / 100 ILS or 0.5% of the book), the smallest
funded trade (250 ILS), the cash hard floor (150 ILS), the rebalance band
(3 points), the take-profit slice (25%), the gain line (+25%), the smallest
goal-odds gain worth a card (5 points), the retune's guard levels (15/20%).

One nullable JSON object of OVERRIDES, not ten columns: a key that is absent
keeps today's default, so NULL keeps today's behaviour exactly and a threshold
added later needs no migration. ADD-ONLY, GUARDED like 0021-0027, MIRRORED into
main.py's ADD COLUMN list (tests/test_startup_ddl_covers_migrations.py). Ships
ALONE, before any code reads it (PR 47).

Revision ID: 0028_plan_thresholds
Revises: 0027_plan_goal_limits
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0028_plan_thresholds"
down_revision = "0027_plan_goal_limits"
branch_labels = None
depends_on = None


def _columns(bind) -> set:
    insp = sa.inspect(bind)
    if "plans" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("plans")}


def upgrade() -> None:
    have = _columns(op.get_bind())
    if not have:
        return
    if "thresholds" not in have:
        op.add_column("plans", sa.Column("thresholds", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "thresholds" in _columns(op.get_bind()):
        op.drop_column("plans", "thresholds")
