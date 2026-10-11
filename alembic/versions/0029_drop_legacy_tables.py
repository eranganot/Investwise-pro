"""Drop the legacy tables (plan v3 PR 114b, D76 stage 3 part 2)

The spec-era pipeline and scaffolds, none of which the app has read since PR
112-113 deleted their code and PR 114a removed their models:
`user_actions`, `decision_items`, `decision_feeds` (the decision feed and its
learning loop), `broker_connections` (the mock broker scaffold), `tax_profiles`
and `buckets` (never read), and `positions.bucket_id` (the FK into `buckets`).

Production before this ran (read-only, 2026-10-11): decision_feeds 1,
decision_items 3 (one call to the deleted /decision-feed/generate), every other
table 0, and no position with a bucket_id. Those 4 rows are dropped.

ORDER, FOR A DROP: code first, schema second. D17 ("a migration ships alone,
add-only; nothing reads it until the next PR") is written for ADDS. For a drop
the mirror holds: PR 114a stopped mapping these (the live `Position` model mapped
`bucket_id`, so dropping the column first would have broken every positions
query until the next deploy), and this ships alone after it.

GUARDED: `0001_initial` builds from the CURRENT ORM metadata, so a database
created from nothing after PR 114a never had these tables. Every drop checks
first. Children before parents (items -> feeds, actions -> items).

Revision ID: 0029_drop_legacy_tables
Revises: 0028_plan_thresholds
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_drop_legacy_tables"
down_revision = "0028_plan_thresholds"
branch_labels = None
depends_on = None

_TABLES = ("user_actions", "decision_items", "decision_feeds", "broker_connections", "tax_profiles")


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _position_columns(bind) -> set:
    insp = sa.inspect(bind)
    if "positions" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("positions")}


def upgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)
    for t in _TABLES:
        if t in have:
            op.drop_table(t)
    if "bucket_id" in _position_columns(bind):
        # batch mode: SQLite rebuilds the table; Postgres drops the column and
        # the FK constraint that depends on it.
        with op.batch_alter_table("positions") as batch:
            batch.drop_column("bucket_id")
    if "buckets" in _tables(bind):
        op.drop_table("buckets")


def downgrade() -> None:
    """Recreate the tables EMPTY, with the columns they had (the rows are gone)."""
    bind = op.get_bind()
    have = _tables(bind)
    def stamps():
        return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now())]

    if "buckets" not in have:
        op.create_table(
            "buckets",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("objective", sa.String(50), nullable=False, server_default="Growth"),
            *stamps())
    if "bucket_id" not in _position_columns(bind):
        with op.batch_alter_table("positions") as batch:
            # With its FK, as production had it -- so an up/down/up test
            # exercises dropping a column that a constraint depends on.
            batch.add_column(sa.Column("bucket_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key("fk_positions_bucket_id_buckets", "buckets",
                                     ["bucket_id"], ["id"], ondelete="SET NULL")
    if "tax_profiles" not in have:
        op.create_table(
            "tax_profiles",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), index=True),
            sa.Column("surtax_status", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("loss_carry_forward", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("trapped_profit_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("data", sa.JSON(), nullable=True),
            *stamps())
    if "broker_connections" not in have:
        op.create_table(
            "broker_connections",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("external_account_id", sa.String(128), nullable=True),
            sa.Column("institution", sa.String(128), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="CONNECTED"),
            sa.Column("credential_ref", sa.String(255), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            *stamps())
    if "decision_feeds" not in have:
        op.create_table(
            "decision_feeds",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
            sa.Column("horizon", sa.String(16), nullable=False, server_default="month"),
            sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            *stamps())
    if "decision_items" not in have:
        op.create_table(
            "decision_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("feed_id", sa.Uuid(), sa.ForeignKey("decision_feeds.id", ondelete="CASCADE"), index=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("action_type", sa.String(16), nullable=False),
            sa.Column("trigger", sa.Text(), nullable=True),
            sa.Column("execution_plan", sa.Text(), nullable=True),
            sa.Column("impact_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("urgency", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("complexity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("time_sensitivity", sa.String(16), nullable=False, server_default="Monitor"),
            sa.Column("veto_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("risk_critique", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            *stamps())
    if "user_actions" not in have:
        op.create_table(
            "user_actions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
            sa.Column("decision_item_id", sa.Uuid(), sa.ForeignKey("decision_items.id", ondelete="CASCADE"),
                      index=True),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("acted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("note", sa.Text(), nullable=True),
            *stamps())
