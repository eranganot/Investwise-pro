"""Make the trade ledger writable.

`transactions` has existed since the first schema and has never had a row. Two
things stopped it being usable, and both are fixed here:

  * `position_id` was NOT NULL with ondelete=CASCADE. A full exit deletes the
    position -- taking the record of the sale with it. The ledger would have
    erased exactly the events it exists to remember.
  * There was nowhere to record the ILS value, the tax, or the cause, so a row
    could not be reconciled against NAV.

GUARDED, the way 0013-0016, 0019 and 0020 are, for the reason those all cite:
`0001_initial` runs `Base.metadata.create_all`, so on a fresh database every
table is built from **today's** models and already carries the columns the later
revisions add. This revision shipped without guards and was the single point
where the chain died:

    $ alembic upgrade head          # against an empty database
    sqlalchemy.exc.ProgrammingError: DuplicateColumnError:
      column "subject" of relation "transactions" already exists
    [SQL: ALTER TABLE transactions ADD COLUMN subject VARCHAR(255)]

Reproduced on postgres:16, 2026-09-03. Every other revision in the chain was
already guarded, so this was the only thing standing between the repo and a
schema that can be rebuilt from nothing.

Guarded PER STATEMENT rather than with one early return. The database this has
to survive is not only the empty one: production's schema was built by
`create_all` on boot, so "has this column but not that index" is a realistic
state and an all-or-nothing skip would step straight over the half that is
missing.

Revision ID: 0018_transaction_ledger
Revises: 0017_plan_applications
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_transaction_ledger"
down_revision = "0017_plan_applications"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
_TABLE = "transactions"

_COLUMNS = (
    ("subject", sa.String(255), {"nullable": True}),
    ("ticker", sa.String(24), {"nullable": True}),
    ("market", sa.String(16), {"nullable": True}),
    ("value_ils", MONEY, {"server_default": "0"}),
    ("tax_ils", MONEY, {"server_default": "0"}),
    ("source", sa.String(40), {"nullable": True}),
    ("note", sa.String(255), {"nullable": True}),
)

_INDEXES = (("ix_transactions_subject", "subject"), ("ix_transactions_ticker", "ticker"))


def _table_exists(insp) -> bool:
    return _TABLE in insp.get_table_names()


def _columns(insp) -> set:
    return {c["name"] for c in insp.get_columns(_TABLE)} if _table_exists(insp) else set()


def _indexes(insp) -> set:
    return {i["name"] for i in insp.get_indexes(_TABLE)} if _table_exists(insp) else set()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _table_exists(insp):
        # Nothing to alter. `0001_initial` creates it from the models, so this
        # only happens on a database that never ran that revision at all.
        return

    have = _columns(insp)
    for name, type_, kwargs in _COLUMNS:
        if name not in have:
            op.add_column(_TABLE, sa.Column(name, type_, **kwargs))

    present = _columns(sa.inspect(bind))
    have_ix = _indexes(sa.inspect(bind))
    for ix_name, col in _INDEXES:
        if ix_name not in have_ix and col in present:
            op.create_index(ix_name, _TABLE, [col])

    # The row must outlive the position it describes.
    #
    # Guarded too, and for a second reason this chain could not run: SQLite has
    # no `ALTER COLUMN`. Emitting it unconditionally killed the chain on SQLite
    # even once the duplicate columns were guarded --
    #
    #     sqlite3.OperationalError: near "ALTER": syntax error
    #     [SQL: ALTER TABLE transactions ALTER COLUMN position_id DROP NOT NULL]
    #
    # -- and on a fresh database the column is ALREADY nullable, because
    # `create_all` built it from a model that has said so since this revision
    # was written. So: ask first, and only rebuild the table where a rebuild is
    # actually needed.
    _pid = next((c for c in sa.inspect(bind).get_columns(_TABLE)
                 if c["name"] == "position_id"), None)
    if _pid is not None and not _pid.get("nullable", True):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(_TABLE) as batch:
                batch.alter_column("position_id", existing_type=sa.Uuid(), nullable=True)
        else:
            op.alter_column(_TABLE, "position_id", existing_type=sa.Uuid(), nullable=True)

    if bind.dialect.name == "postgresql":
        # The constraint name is generated, so find it rather than guess. On a
        # fresh database `create_all` has already built the foreign key from the
        # model, which is why this drops whatever is there before creating.
        name = bind.execute(sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'transactions'::regclass AND contype = 'f'"
        )).scalar()
        if name:
            op.drop_constraint(name, _TABLE, type_="foreignkey")
        op.create_foreign_key("fk_transactions_position", _TABLE, "positions",
                              ["position_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _table_exists(insp):
        return
    for ix_name, _col in _INDEXES:
        if ix_name in _indexes(sa.inspect(bind)):
            op.drop_index(ix_name, table_name=_TABLE)
    have = _columns(sa.inspect(bind))
    for name, _type, _kwargs in reversed(_COLUMNS):
        if name in have:
            op.drop_column(_TABLE, name)
