"""strategy_signal_state (what the active rule said to hold, last session)

A trend or swing rule emits a target every session, and almost every session it
is the same as yesterday's. Only a change is news, so the previous state has to
persist -- otherwise the daily job either says nothing useful or notifies the
user daily that the rule still wants what it wanted yesterday.

Revision ID: 0010_strategy_signal_state
Revises: 0009_strategy_backtests
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_strategy_signal_state"
down_revision = "0009_strategy_backtests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "strategy_signal_state" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "strategy_signal_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        # ticker -> weight, so a change is detected by comparing what would be
        # held rather than by diffing prose that may be reworded later.
        sa.Column("target", sa.JSON(), nullable=True),
        sa.Column("previous_target", sa.JSON(), nullable=True),
        sa.Column("as_of", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("flipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_signal_state_subject", "strategy_signal_state", ["subject"])
    op.create_index("ix_strategy_signal_state_strategy_id", "strategy_signal_state", ["strategy_id"])


def downgrade() -> None:
    op.drop_table("strategy_signal_state")
