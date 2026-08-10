"""trading_rules.strategy_id — which strategy a strategy_signal rule follows

An entry/exit rule is a subscription to one strategy's signal, and the strategy
is pinned at arm time. Without pinning, changing the applied strategy would
silently repoint every armed rule at a different set of trades while the user
still saw "TQQQ entry".

Revision ID: 0013_rule_strategy_id
Revises: 0012_plan_strategy_sleeve
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_rule_strategy_id"
down_revision = "0012_plan_strategy_sleeve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trading_rules",
                  sa.Column("strategy_id", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("trading_rules", "strategy_id")
