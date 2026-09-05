"""C8. Nothing else writes the schema, and the migrations reach everything.

`test_startup_ddl_covers_migrations.py` checks one direction: a migration that
adds a column must also appear in the `ALTER TABLE ... IF NOT EXISTS` list in
`main.py`, because for most of this app's life that list -- not alembic -- was
what actually ran against production.

**PR 52 reversed which one is the mechanism.** `alembic upgrade head` now runs
as Railway's `preDeployCommand`, proven by PR 54: `/health.schema_revision`
moved from `0020_solver_verdicts` to `0021_plan_cash_target` across that deploy,
and nothing in `app/` writes `alembic_version`. So C8 turns the boot-time
`create_all` off, and the direction that matters flips with it:

    before C8   every migration must be in the startup DDL   (the old test)
    after  C8   every startup DDL must be in the migrations  (this one)

Get that backwards and the failure is silent in the worst way: the app comes up,
every table it needs exists, and one column that only the startup list knew
about is missing from a database the repo can no longer reproduce.

**Measured before the flip, on postgres:16.** A database built by `alembic
upgrade head` alone and one built by `create_all` plus the 23-statement startup
list came out identical -- 272 columns, 72 indexes, 45 constraints, identical
defaults. The only difference was alembic's own `alembic_version` table. This
test is that measurement, made cheap enough to run on every commit.

Not financial advice.
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect

REPO = pathlib.Path(__file__).resolve().parent.parent
MAIN = REPO / "app" / "main.py"

# `ALTER TABLE <t> ADD COLUMN IF NOT EXISTS <c> ...`, tolerant of the string
# concatenation the list uses to stay inside the line limit.
_ADD = re.compile(
    r"ALTER TABLE\s+(?P<table>\w+)\s+ADD COLUMN IF NOT EXISTS\s+(?P<col>\w+)",
    re.IGNORECASE)
_DROP = re.compile(
    r"ALTER TABLE\s+(?P<table>\w+)\s+DROP COLUMN IF EXISTS\s+(?P<col>\w+)",
    re.IGNORECASE)


def _startup_ddl_source() -> str:
    """The DDL list as one flat string, concatenation collapsed.

    The list is written as adjacent string literals so each line fits, which
    means `"ALTER TABLE strategy_backtests ADD COLUMN IF NOT EXISTS " "last_error TEXT"`
    is one statement across two literals. Joining the quoted fragments back
    together is what lets one regex read all of them.
    """
    src = MAIN.read_text(encoding="utf-8")
    start = src.index("for ddl in (")
    end = src.index("):", start)
    body = src[start:end]
    # Drop comment lines, then splice adjacent literals: `" ...` after `... "`.
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    return re.sub(r'"\s*\n\s*"', "", body)


@pytest.fixture(scope="module")
def alembic_schema(tmp_path_factory):
    """A database built by the migrations and nothing else.

    SQLite on purpose. The comparison here is about which COLUMNS exist, and
    running it on a file the test owns keeps it in both gate passes rather than
    only the PostgreSQL one -- a guard that runs half the time guards half the
    time. The postgres-specific parts of the startup list (index creation, the
    `DOUBLE PRECISION` spellings) are about type and index, not existence, and
    the full postgres equivalence is recorded in STATUS.md under PR 57.
    """
    db = tmp_path_factory.mktemp("c8") / "head.db"
    url = f"sqlite+aiosqlite:///{db}"
    env = {**os.environ, "DATABASE_URL": url, "AUTO_CREATE_TABLES": "false"}
    run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                         cwd=str(REPO), env=env, capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, (
        "alembic upgrade head failed on an empty database, so there is no "
        f"schema to compare against:\n{run.stdout[-1500:]}\n{run.stderr[-3000:]}")

    insp = inspect(create_engine(f"sqlite:///{db}"))
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


def test_every_column_the_startup_ddl_adds_is_one_the_migrations_build(alembic_schema):
    """The invariant C8 rests on, stated as the thing that could go wrong.

    Each `ADD COLUMN IF NOT EXISTS` in `main.py` names a column that production
    needs. With the boot-time list switched off, the migrations are the only
    thing that can put it there. A column in the list and not in the migrations
    is a column that quietly stops existing on the next deploy to a fresh
    database.
    """
    src = _startup_ddl_source()
    dropped = {(m.group("table"), m.group("col")) for m in _DROP.finditer(src)}
    missing = []
    for m in _ADD.finditer(src):
        table, col = m.group("table"), m.group("col")
        if (table, col) in dropped:
            # 0019 drops `plans.strategy` in the same list that used to add it.
            continue
        if table not in alembic_schema:
            missing.append(f"{table}.{col} (no such table after alembic upgrade head)")
        elif col not in alembic_schema[table]:
            missing.append(f"{table}.{col}")

    assert not missing, (
        "app/main.py's startup DDL adds columns the migrations do not build:\n  "
        + "\n  ".join(missing)
        + "\n\nWith AUTO_CREATE_TABLES off (C8/PR 57) that list no longer runs "
          "in production, so each of these would be missing from any database "
          "built from the repo. Add the migration, do not re-enable the flag.")


def test_the_startup_list_is_not_empty_so_this_test_can_fail(alembic_schema):
    """A guard on the guard.

    The parser reads a literal block out of `main.py`. If that block is
    refactored, renamed or reformatted past recognition, both assertions above
    go vacuously green -- a test that passes because it found nothing to check
    is worse than no test, because it also reports success.
    """
    found = list(_ADD.finditer(_startup_ddl_source()))
    assert len(found) >= 5, (
        f"only {len(found)} ADD COLUMN statements parsed out of app/main.py. "
        "The DDL list has moved or changed shape; fix this parser rather than "
        "letting the coverage check pass on an empty set.")


def test_production_does_not_build_its_own_schema_by_default():
    """The flip itself. Off unless someone turns it on, deliberately.

    Off by DEFAULT rather than off by env var: an env var someone has to
    remember to set is the failure mode C8 removes, and this repo's ops board
    carries four-week-old items of exactly that shape.
    """
    from app.core.config import Settings

    assert Settings.model_fields["auto_create_tables"].default is False, (
        "AUTO_CREATE_TABLES defaults to on again. A fresh production "
        "environment would then build its schema from the models at boot and "
        "mask a migration that did not run.")


def test_health_says_whether_anything_else_is_writing_the_schema():
    """`schema_revision` alone cannot answer this.

    A revision at head while the app also runs `create_all` at boot cannot
    distinguish "the migrations built this" from "the models did, and the
    migrations came along". The deploy check reads both.
    """
    import inspect as _inspect

    from app.api.routes import health

    src = _inspect.getsource(health.health)
    assert "builds_own_schema" in src, (
        "/health no longer reports builds_own_schema, so nothing observable "
        "says whether the boot-time schema builder is on in production.")
