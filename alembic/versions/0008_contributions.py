"""contributions ledger (external money in/out)

"What you put in" was derived from the current book -- the sum of every
position's cost_basis, FX-converted at today's rate -- so it moved whenever the
shekel moved, whenever a sale replaced an original basis with net-of-CGT
proceeds, and whenever a fee swap re-stamped basis at the live price. Reported
live: 20,000 deposited, 20,790 displayed. Only a deposit or a withdrawal may
change that figure, so those events now get their own ledger.

Revision ID: 0008_contributions
Revises: 0007_plan_strategy
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_contributions"
down_revision = "0007_plan_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "contributions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "contributions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False, index=True),
        # Signed: withdrawals stored negative so the ledger sums directly.
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False,
                  server_default="0"),
        sa.Column("kind", sa.String(length=16), nullable=False,
                  server_default="deposit"),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_contributions_subject", "contributions", ["subject"])
    op.create_index("ix_contributions_occurred_at", "contributions", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("contributions")
