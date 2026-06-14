"""broker_connections (Phase 3.1 brokerage sync scaffold)

Revision ID: 0006_broker_connections
Revises: 0005_plan_flavor
Create Date: 2026-06-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_broker_connections"
down_revision = "0005_plan_flavor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "broker_connections" in insp.get_table_names():
        return
    op.create_table(
        "broker_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=True),
        sa.Column("institution", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CONNECTED"),
        sa.Column("credential_ref", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("broker_connections")
