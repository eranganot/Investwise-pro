"""nav_snapshots — the only record of what the book was actually worth

Everything historical the app showed before this was a BACKFILL: today's
holdings priced back through their own past. That answers "what would this book
have done", not "what did my money do".

Past NAV cannot be recovered. `grep` for `Transaction(` across app/ outside the
models returns zero hits, so the trade ledger has never been written, and
whs_snapshots stores health scores rather than value and is never written
either. There is nothing to reconstruct from. History can only START — which is
why this migration is worth running the day it exists rather than the week it
becomes convenient.

`invested_ils` (contributions-to-date) sits on the same row as the value on
purpose. A NAV that rose because money arrived is not a return, and without the
deposit total recorded alongside, a later reader cannot tell the two apart. The
chaining arithmetic lives in services/nav_history.time_weighted.

Revision ID: 0016_nav_snapshots
Revises: 0015_backtest_benchmark_ticker
Create Date: 2026-08-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_nav_snapshots"
down_revision = "0015_backtest_benchmark_ticker"
branch_labels = None
depends_on = None

_TABLE = "nav_snapshots"


def upgrade() -> None:
    # Guarded the way 0013, 0014 and 0015 are, for the reason that broke the
    # chain before: 0001_initial runs `Base.metadata.create_all`, so on a fresh
    # database this table already exists by the time this revision runs, and a
    # migration that explodes on "already exists" is one nobody can run.
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("as_of", sa.String(length=10), nullable=False),
        sa.Column("nav_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("cash_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("invested_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="job"),
        sa.Column("engine_version", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # One row per user per day, so a job that runs twice UPDATES rather than
        # duplicating. A duplicated day would silently double-count a period in
        # the chained return.
        sa.UniqueConstraint("subject", "as_of", name="uq_nav_snapshots_subject_day"),
    )
    op.create_index("ix_nav_snapshots_subject", _TABLE, ["subject"])
    op.create_index("ix_nav_snapshots_as_of", _TABLE, ["as_of"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return
    op.drop_index("ix_nav_snapshots_as_of", table_name=_TABLE)
    op.drop_index("ix_nav_snapshots_subject", table_name=_TABLE)
    op.drop_table(_TABLE)
