"""plans.halve_limit_pct + plans.on_track_pct -- the goal's two limits as settings (PR 104c)

Eran, 2026-10-07: "where did I set this limit of 1% maximum chance?" -- then
"add the 1% dial as part of PR104c. I want every decision to be controlled and
changeable through the app." Both numbers decided which goal cards he got and
lived only as constants (`frontier_service.HALVE_FLOOR_PCT`, `GOAL_CONFIDENCE`).

  halve_limit_pct  the crash-risk limit: the highest chance the book may halve at
                   some point for a protection setup to be suggested, in %.
                   NULL means 1.0 (his answer on 2026-10-05).
  on_track_pct     the goal is "on track" at or above this chance, in %.
                   NULL means 70.

ADD-ONLY and NULLABLE, so NULL keeps today's behaviour exactly. GUARDED like
0021-0026 (`0001_initial` runs create_all, so a fresh database already has the
columns) and MIRRORED into main.py's ADD COLUMN list, which
tests/test_startup_ddl_covers_migrations.py enforces. Ships ALONE, before any
code reads it (PR 47).

Revision ID: 0027_plan_goal_limits
Revises: 0026_plan_frontier_settings
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0027_plan_goal_limits"
down_revision = "0026_plan_frontier_settings"
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
    if "halve_limit_pct" not in have:
        op.add_column("plans", sa.Column("halve_limit_pct", sa.Float(), nullable=True))
    if "on_track_pct" not in have:
        op.add_column("plans", sa.Column("on_track_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    have = _columns(op.get_bind())
    if "on_track_pct" in have:
        op.drop_column("plans", "on_track_pct")
    if "halve_limit_pct" in have:
        op.drop_column("plans", "halve_limit_pct")
