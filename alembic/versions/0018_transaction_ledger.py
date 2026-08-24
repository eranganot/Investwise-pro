"""Make the trade ledger writable.

`transactions` has existed since the first schema and has never had a row. Two
things stopped it being usable, and both are fixed here:

  * `position_id` was NOT NULL with ondelete=CASCADE. A full exit deletes the
    position -- taking the record of the sale with it. The ledger would have
    erased exactly the events it exists to remember.
  * There was nowhere to record the ILS value, the tax, or the cause, so a row
    could not be reconciled against NAV.

Revision ID: 0018_transaction_ledger
Revises: 0017_plan_applications
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_transaction_ledger"
down_revision = "0017_plan_applications"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)


def upgrade() -> None:
    op.add_column("transactions", sa.Column("subject", sa.String(255), nullable=True))
    op.add_column("transactions", sa.Column("ticker", sa.String(24), nullable=True))
    op.add_column("transactions", sa.Column("market", sa.String(16), nullable=True))
    op.add_column("transactions", sa.Column("value_ils", MONEY, server_default="0"))
    op.add_column("transactions", sa.Column("tax_ils", MONEY, server_default="0"))
    op.add_column("transactions", sa.Column("source", sa.String(40), nullable=True))
    op.add_column("transactions", sa.Column("note", sa.String(255), nullable=True))
    op.create_index("ix_transactions_subject", "transactions", ["subject"])
    op.create_index("ix_transactions_ticker", "transactions", ["ticker"])

    # The row must outlive the position it describes.
    op.alter_column("transactions", "position_id", existing_type=sa.Uuid(),
                    nullable=True)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The constraint name is generated, so find it rather than guess.
        name = bind.execute(sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'transactions'::regclass AND contype = 'f'"
        )).scalar()
        if name:
            op.drop_constraint(name, "transactions", type_="foreignkey")
        op.create_foreign_key("fk_transactions_position", "transactions", "positions",
                              ["position_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for col in ("note", "source", "tax_ils", "value_ils", "market", "ticker", "subject"):
        op.drop_column("transactions", col)
    op.drop_index("ix_transactions_ticker", table_name="transactions")
    op.drop_index("ix_transactions_subject", table_name="transactions")
