"""trading_rules.params -- a second setting for the advanced rules (PR 80)

Eran, 2026-09-29, decision 1 of the advanced-rules plan: "a small add-only
database migration". Three of the four new rule types need more than one
number: a profit-protect trail has an activation gain AND a trail; a drawdown
guard remembers that it fired and what it held back. `level` stays the rule's
one headline number; `params` holds the rest.

ADD-ONLY and NULLABLE: null means "the house defaults". GUARDED like 0021-0024
(`0001_initial` runs create_all, so a fresh database already has the column),
and MIRRORED into main.py's ADD COLUMN list, which
tests/test_startup_ddl_covers_migrations.py enforces.

Revision ID: 0025_rule_params
Revises: 0024_plan_cash_target_ils
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_rule_params"
down_revision = "0024_plan_cash_target_ils"
branch_labels = None
depends_on = None

_TABLE = "trading_rules"
_COLUMN = "params"


def _columns(bind) -> set:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    have = _columns(op.get_bind())
    if not have:
        return
    if _COLUMN not in have:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        op.drop_column(_TABLE, _COLUMN)
