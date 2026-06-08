"""auth tables: credentials + revoked_tokens

Revision ID: 0002_auth_tables
Revises: 0001_initial
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_auth_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "credentials" not in existing:
        op.create_table(
            "credentials",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_credentials_email", "credentials", ["email"], unique=True)
    if "revoked_tokens" not in existing:
        op.create_table(
            "revoked_tokens",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("jti", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_revoked_tokens_jti", "revoked_tokens", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_table("revoked_tokens")
    op.drop_table("credentials")
