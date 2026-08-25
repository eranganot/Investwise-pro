"""A migration that adds a column and is not in the startup DDL has not shipped.

This app does not run `alembic upgrade head` against production. The comment in
`main.py` says why and says it was tried twice: once against the sqlite URL in
`.env`, once against a Railway private host that does not resolve from outside
their network. So the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` list in
`main.py` is not a convenience next to the migrations -- **it is the mechanism**.
The migrations are the record; that list is what actually runs.

It has now failed twice in the same table:

  * Migration 0011 added `last_error` / `last_error_at`. The deploy went out
    first, every SELECT raised `UndefinedColumnError`, and that took out
    /strategies, /strategies/backtests and the backtest refresh outright.
  * Migration 0015 added `benchmark_ticker` on 2026-08-15 and was never mirrored
    at all. This time nothing looked broken, because the fix for the first
    incident was to make `get_many` swallow the error and return `{}` -- so the
    app degraded silently to "no strategy has ever been measured" and stayed
    that way for ten days, with the reason sitting unread in
    `backtest_store_error`.

The second is the worse failure, and graceful degradation is what hid it. This
test is the thing that would have caught either one, on the commit that
introduced it.
"""
import re
from pathlib import Path

MIGRATIONS = Path("alembic/versions")
MAIN = Path("app/main.py")

# `op.add_column("table", sa.Column("name", ...))`, tolerant of line breaks.
_ADD_COLUMN = re.compile(
    r"""add_column\(\s*["'](?P<table>\w+)["']\s*,\s*sa\.Column\(\s*["'](?P<col>\w+)["']""",
    re.VERBOSE | re.DOTALL)
# The same, for migrations that name the column in a module constant first.
_QUOTED = r"[\"']"
_CONST_TABLE = re.compile(r"^_TABLE\s*=\s*" + _QUOTED + r"(\w+)" + _QUOTED, re.MULTILINE)
_CONST_COL = re.compile(r"^_COLUMN\s*=\s*" + _QUOTED + r"(\w+)" + _QUOTED, re.MULTILINE)
_ADD_COLUMN_CONST = re.compile(
    r"add_column\(\s*_TABLE\s*,\s*sa\.Column\(\s*_COLUMN\b")
_CREATE_TABLE = re.compile(r"""create_table\(\s*["'](?P<table>\w+)["']""")


def _migration_columns() -> dict[str, set[str]]:
    """{table: {column, ...}} added by migrations to PRE-EXISTING tables.

    A column introduced in the same migration that CREATES the table is not
    interesting: `Base.metadata.create_all` builds missing tables in full. Only
    a column added to a table that already exists needs the ALTER, because
    create_all never touches a table it finds.
    """
    out: dict[str, set[str]] = {}
    created_here: set[str] = set()
    for path in sorted(MIGRATIONS.glob("0*.py")):
        src = path.read_text(encoding="utf-8")
        created_here |= {m.group("table") for m in _CREATE_TABLE.finditer(src)}
        for m in _ADD_COLUMN.finditer(src):
            out.setdefault(m.group("table"), set()).add(m.group("col"))
        if _ADD_COLUMN_CONST.search(src):
            t, c = _CONST_TABLE.search(src), _CONST_COL.search(src)
            if t and c:
                out.setdefault(t.group(1), set()).add(c.group(1))
    # 0001_initial runs create_all, so every table exists from the start; a
    # later create_table is a genuinely new table and create_all handles it.
    return out


def _ddl_columns() -> dict[str, set[str]]:
    """{table: {column, ...}} the startup DDL adds."""
    src = MAIN.read_text(encoding="utf-8")
    # The statements are split across adjacent string literals for line length,
    # so join them before matching rather than trying to match across the seam.
    joined = re.sub(r'"\s*\n\s*"', "", src)
    out: dict[str, set[str]] = {}
    for m in re.finditer(
            r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)", joined):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def test_every_migration_added_column_is_in_the_startup_ddl():
    mig, ddl = _migration_columns(), _ddl_columns()
    assert mig, "no add_column calls parsed -- the regex has drifted from the migrations"

    missing = []
    for table, cols in sorted(mig.items()):
        for col in sorted(cols - ddl.get(table, set())):
            missing.append(f"{table}.{col}")

    assert not missing, (
        "these columns exist in a migration but NOT in main.py's startup DDL, "
        "so production will not have them:\n  " + "\n  ".join(missing)
        + "\n\nThis app does not run alembic against production -- the DDL list "
          "is what actually runs. Add an ALTER TABLE ... ADD COLUMN IF NOT "
          "EXISTS line for each.")


def test_the_column_that_caused_this_is_covered():
    """Named explicitly, so the regression has a name and not just a rule.

    `strategy_backtests.benchmark_ticker` is why every measured backtest was
    missing from production for ten days.
    """
    ddl = _ddl_columns()
    assert "benchmark_ticker" in ddl.get("strategy_backtests", set())
    assert "last_error" in ddl.get("strategy_backtests", set())


def test_the_ddl_parser_actually_finds_things():
    """A test built on a regex that silently matches nothing proves nothing."""
    ddl = _ddl_columns()
    assert ddl.get("transactions"), "the PR 3 ledger columns should be here"
    assert "strategy_id" in ddl.get("trading_rules", set())
