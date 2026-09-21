"""plans.cash_target_ils + cash_redeploy_at_ils -- cash rules in shekels

Eran, 2026-09-21: "the card should appear if I have more than 1500, not by % of
my portfolio." Chosen: the card appears above 1,500 and invests down to the
150 hard floor -- so the target (what is kept) and the trigger (when the card
shows) are two columns. A percentage target grows with the book; a reserve of a fixed
size does not. NULL means "use cash_target_pct"; setting either clears the
other (plan_service.upsert_plan). Interpreted only in
funding_service.cash_floor_pct / cash_floor_ils and book_target.

GUARDED like 0021/0022 (0001_initial runs create_all, so a fresh database
already has the column) and MIRRORED into main.py's ADD COLUMN list, which
tests/test_startup_ddl_covers_migrations.py enforces.

Revision ID: 0024_plan_cash_target_ils
Revises: 0023_standing_orders
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0024_plan_cash_target_ils"
down_revision = "0023_standing_orders"
branch_labels = None
depends_on = None

def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    if "cash_target_ils" not in cols:
        op.add_column("plans", sa.Column("cash_target_ils", sa.Numeric(18, 4), nullable=True))
    if "cash_redeploy_at_ils" not in cols:
        op.add_column("plans", sa.Column("cash_redeploy_at_ils", sa.Numeric(18, 4),
                                         nullable=True))


def downgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plans")}
    for col in ("cash_redeploy_at_ils", "cash_target_ils"):
        if col in cols:
            op.drop_column("plans", col)
