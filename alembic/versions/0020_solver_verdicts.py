"""solver_verdicts — the solver's last answer, written down so something can read it

T5 asks the war-room agents to treat the target solver's verdict as a
constraint. They could not, and the obstacle was not design: nothing stored a
verdict anywhere. `GET /plan/target` recomputed from scratch on every request —
a coarse-then-fine grid sweep, each point one simulation of ten years of daily
closes — while the war room already costs 6,726 ms on `/recommendations` and
every other agent comes in under 50 ms. Calling the solver from inside it would
have put a multi-second sweep on the slowest path in the app.

So the solver writes and the war room reads. The columns are exactly what a
CONSTRAINT needs to decide and to explain itself; everything else lands in
`detail`. Denormalising the whole result would make this table a second copy of
something recomputed anyway, which is the failure migration 0019 removed one
layer down.

`floor_breaches_ceiling` earns its own column rather than living in the JSON
because it is the one field that changes the ADVICE: it means the book breaches
its ceiling at zero sleeve, so no sleeve size repairs it, including none at all.
A reader that has to dig through JSON to find that will eventually not bother.

Revision ID: 0020_solver_verdicts
Revises: 0019_drop_plan_strategy_columns
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0020_solver_verdicts"
down_revision = "0019_drop_plan_strategy_columns"
branch_labels = None
depends_on = None

_TABLE = "solver_verdicts"

JSONB_OR_JSON = JSONB().with_variant(sa.JSON(), "sqlite")


def _exists(bind) -> bool:
    return _TABLE in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    # Guarded the same way 0013-0015 and 0019 are, and for the reason that has
    # broken this chain before: `0001_initial` runs `Base.metadata.create_all`,
    # so on a fresh database this table is already built from the current model
    # by the time this migration runs. Creating what exists is an error.
    bind = op.get_bind()
    if _exists(bind):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("solver_version", sa.String(length=16), server_default=""),
        sa.Column("outcome", sa.String(length=32), server_default=""),
        sa.Column("target_excess_pct", sa.Float(), nullable=True),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("solved_total_sleeve_pct", sa.Float(), nullable=True),
        sa.Column("floor_breaches_ceiling", sa.Boolean(), server_default=sa.false()),
        sa.Column("binding_constraint", sa.String(length=32), server_default=""),
        sa.Column("source", sa.String(length=16), server_default="on_demand"),
        sa.Column("detail", JSONB_OR_JSON, nullable=True),
    )
    # Read pattern is always "the newest row for this subject".
    op.create_index("ix_solver_verdicts_subject", _TABLE, ["subject"])
    op.create_index("ix_solver_verdicts_outcome", _TABLE, ["outcome"])


def downgrade() -> None:
    bind = op.get_bind()
    if _exists(bind):
        op.drop_index("ix_solver_verdicts_outcome", table_name=_TABLE)
        op.drop_index("ix_solver_verdicts_subject", table_name=_TABLE)
        op.drop_table(_TABLE)
