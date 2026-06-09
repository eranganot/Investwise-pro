"""plan preferred_depth (flavor)

Revision ID: 0005_plan_flavor
Revises: 0004_plan_roi_yield
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_plan_flavor"
down_revision = "0004_plan_roi_yield"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    if "preferred_depth" not in existing:
        op.add_column("plans", sa.Column("preferred_depth", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "preferred_depth")
