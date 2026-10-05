"""plans.kelly_fraction + plans.return_confidence -- the frontier's two dials (PR 90)

Eran, 2026-10-05, on the frontier card: "0.5 is the default but I want to be
able to change it", and on return confidence: shrunk by default, "I want to add
blended as an option to change". Both are settings the frontier card (PR 91)
reads; neither is read by anything in this PR.

  kelly_fraction      the share of full Kelly the plan aims for, 0.25-1.0.
                      NULL means the objective's default (Grow 0.5,
                      Balanced 1/3, Preserve/Income 0.25).
  return_confidence   how far the sleeves' measured backtests are trusted:
                      'shrunk' (Black-Litterman, evidence-weighted),
                      'blended', or 'measured'. NULL means 'shrunk'.

ADD-ONLY and NULLABLE, so NULL keeps today's behaviour exactly. GUARDED like
0021-0025 (`0001_initial` runs create_all, so a fresh database already has the
columns) and MIRRORED into main.py's ADD COLUMN list, which
tests/test_startup_ddl_covers_migrations.py enforces. Ships ALONE, before any
code reads it (PR 47).

Revision ID: 0026_plan_frontier_settings
Revises: 0025_rule_params
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0026_plan_frontier_settings"
down_revision = "0025_rule_params"
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
    if "kelly_fraction" not in have:
        op.add_column("plans", sa.Column("kelly_fraction", sa.Float(), nullable=True))
    if "return_confidence" not in have:
        op.add_column("plans", sa.Column("return_confidence", sa.String(length=12), nullable=True))


def downgrade() -> None:
    have = _columns(op.get_bind())
    if "return_confidence" in have:
        op.drop_column("plans", "return_confidence")
    if "kelly_fraction" in have:
        op.drop_column("plans", "kelly_fraction")
