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
    # Guarded the way 0012 is, and for a reason that breaks the whole chain
    # without it: 0001_initial runs `Base.metadata.create_all`, so on a fresh
    # database every table is built from TODAY's models and already has this
    # column by the time this revision runs. `alembic upgrade head` therefore
    # died here with "duplicate column name: strategy_id" and never reached
    # anything after it -- which is part of why hand-running alembic against
    # this deploy has failed, and why the startup self-heal in main.py exists.
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trading_rules")}
    if "strategy_id" not in cols:
        op.add_column("trading_rules",
                      sa.Column("strategy_id", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("trading_rules", "strategy_id")
