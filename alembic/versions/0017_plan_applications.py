"""plan_applications - what the app changed on the book, and what it replaced

Phase A is the first write in the T line. `before_state` is the whole reason
this table exists: an application that cannot be reversed is one the user has to
be certain about before pressing, and nobody is. Storing snapshots rather than
diffs is deliberate -- a diff needs a base to be meaningful, and the base is
exactly what a later reader will not have.

Nothing in this table is a trade. No path that writes it places an order.

Revision ID: 0017_plan_applications
Revises: 0016_nav_snapshots
Create Date: 2026-08-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_plan_applications"
down_revision = "0016_nav_snapshots"
branch_labels = None
depends_on = None

_TABLE = "plan_applications"
_JSON = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # Guarded the way 0013-0016 are: 0001_initial runs
    # `Base.metadata.create_all`, so on a fresh database this table already
    # exists by the time this revision runs, and a migration that explodes on
    # "already exists" is one nobody can run.
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False,
                  server_default="apply"),
        sa.Column("before_state", _JSON, nullable=False, server_default="{}"),
        sa.Column("after_state", _JSON, nullable=False, server_default="{}"),
        sa.Column("context", _JSON, nullable=False, server_default="{}"),
        sa.Column("allocated_pct_after", sa.Float(), nullable=False,
                  server_default="0"),
        sa.Column("apply_version", sa.String(length=16), nullable=False,
                  server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # undo_last reads the newest apply row for one subject, so the index that
    # matters is (subject, created_at) rather than subject alone.
    op.create_index("ix_plan_applications_subject_created", _TABLE,
                    ["subject", "created_at"])


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_plan_applications_subject_created", table_name=_TABLE)
    op.drop_table(_TABLE)
