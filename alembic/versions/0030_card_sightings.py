"""card_sightings -- when each Today card was first and last seen (C34)

Production kept no record of which cards appeared on Today, so a card that lived
under the 30-minute push check could disappear unseen, and nobody could count how
often (BACKLOG C34). One row per (subject, card id): first_seen, last_seen and how
many builds saw it. Small on purpose: no card body, no amounts.

ADD-ONLY and GUARDED like 0017: `0001_initial` builds from the current ORM
metadata, so on a fresh database this table already exists when this runs.
Ships ALONE (D17): nothing reads or writes it until the next PR.

Revision ID: 0030_card_sightings
Revises: 0029_drop_legacy_tables
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0030_card_sightings"
down_revision = "0029_drop_legacy_tables"
branch_labels = None
depends_on = None

_TABLE = "card_sightings"


def upgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("card_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("subject", "card_id", name="uq_card_sightings_subject_card"),
    )
    op.create_index("ix_card_sightings_subject", _TABLE, ["subject"])


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_card_sightings_subject", table_name=_TABLE)
    op.drop_table(_TABLE)
