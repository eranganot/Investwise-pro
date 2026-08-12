"""plan_sleeves — a strategy sleeve is a row, not a column pair

``plans.strategy`` + ``plans.strategy_sleeve_pct`` can hold exactly one
strategy, so applying a second one overwrote the first. This table lets the book
run a core plus any number of sleeves, each with its own share and its own rule.

The core stays the IMPLICIT REMAINDER in this release: sleeves sum to <= 100 and
whatever is left over is objective-managed exactly as it is today. ``is_core``
is created but unused, reserved so that switching to an explicit core row later
is a behaviour change rather than a second migration.

The old ``plans`` columns are deliberately NOT dropped here. They stay readable
for one release so a deploy that rolls back does not lose anyone's applied
strategy, and so the backfill can be re-derived if this table is ever rebuilt.

No data is moved by this migration. Backfill happens at startup instead --
generating UUID primary keys and reading ``users.email`` in cross-dialect SQL is
worse than doing it in Python, and this deploy has twice failed to run alembic
by hand, so the startup path is the one that reliably executes.

Revision ID: 0014_plan_sleeves
Revises: 0013_rule_strategy_id
Create Date: 2026-08-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_plan_sleeves"
down_revision = "0013_rule_strategy_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded the way 0012 is: create_all may already have built this table on a
    # deploy where auto_create_tables is on, and a migration that explodes on
    # "already exists" is a migration nobody can run.
    if "plan_sleeves" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "plan_sleeves",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("strategy_id", sa.String(length=40), nullable=False),
        sa.Column("sleeve_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_core", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("subject", "strategy_id",
                            name="uq_plan_sleeves_subject_strategy"),
    )
    op.create_index("ix_plan_sleeves_subject", "plan_sleeves", ["subject"])
    op.create_index("ix_plan_sleeves_strategy_id", "plan_sleeves", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_sleeves_strategy_id", table_name="plan_sleeves")
    op.drop_index("ix_plan_sleeves_subject", table_name="plan_sleeves")
    op.drop_table("plan_sleeves")
