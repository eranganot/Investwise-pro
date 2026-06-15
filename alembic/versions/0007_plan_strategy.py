"""plan.strategy (strategy selection)

Revision ID: 0007_plan_strategy
Revises: 0006_broker_connections
Create Date: 2026-06-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_plan_strategy"
down_revision = "0006_broker_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    if "strategy" not in existing:
        op.add_column("plans", sa.Column("strategy", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "strategy")
