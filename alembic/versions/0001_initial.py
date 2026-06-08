"""initial baseline - create all tables from the ORM metadata

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-08
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app import models  # noqa: F401  register all tables
    from app.models.base import Base
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app import models  # noqa: F401
    from app.models.base import Base
    Base.metadata.drop_all(bind=op.get_bind())
