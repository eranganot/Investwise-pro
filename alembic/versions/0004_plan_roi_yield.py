"""plan ROI/Yield targets

Revision ID: 0004_plan_roi_yield
Revises: 0003_plans
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_plan_roi_yield"
down_revision = "0003_plans"
branch_labels = None
depends_on = None

_COLS = [
    ("target_roi_pct", sa.Float()),
    ("target_roi_period", sa.String(12)),
    ("target_yield_pct", sa.Float()),
    ("target_yield_period", sa.String(12)),
]


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    for name, typ in _COLS:
        if name not in existing:
            op.add_column("plans", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    for name, _ in _COLS:
        op.drop_column("plans", name)
