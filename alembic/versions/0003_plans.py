"""planning table

Revision ID: 0003_plans
Revises: 0002_auth_tables
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_plans"
down_revision = "0002_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "plans" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("objective", sa.String(16), nullable=False),
        sa.Column("risk_tolerance", sa.String(8), nullable=False),
        sa.Column("horizon_years", sa.Integer(), nullable=False),
        sa.Column("target_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("target_date", sa.String(16), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_plans_user_id", "plans", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_table("plans")
