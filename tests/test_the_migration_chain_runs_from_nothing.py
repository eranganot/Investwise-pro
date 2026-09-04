"""`alembic upgrade head` has to work on a database that does not exist yet.

Reproduced on postgres:16, 2026-09-03, against an empty database:

    sqlalchemy.exc.ProgrammingError: DuplicateColumnError:
      column "subject" of relation "transactions" already exists
    [SQL: ALTER TABLE transactions ADD COLUMN subject VARCHAR(255)]

The cause is written down in this repo four times already, in the comments of
0013, 0014, 0015, 0016, 0019 and 0020: **`0001_initial` runs
`Base.metadata.create_all`**, so a fresh database is built from *today's* models
and already carries every column the later revisions add. Each of those
revisions guards itself. `0018_transaction_ledger` shipped without a guard and
was the single point where the chain died -- one unguarded revision is enough,
because alembic stops there and never reaches the ones after it.

`AUTO_CREATE_TABLES=true` in production is what made this survivable and also
what made it invisible: the app builds its own schema on boot, so nobody ran the
migrations and nobody found out they could not run. The config flip is the next
PR, and it is only safe once this one is live.

A test rather than a note, because "the migrations work" is not something a
reader can check by reading. This runs the real `alembic upgrade head` in a
subprocess against a scratch SQLite file -- the same command a deploy would run,
with nothing stubbed.

Not financial advice.
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def _alembic(args, db_url, timeout=300):
    env = {**os.environ, "DATABASE_URL": db_url, "AUTO_CREATE_TABLES": "false"}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=str(REPO), env=env, capture_output=True,
                          text=True, timeout=timeout)


def test_the_migration_chain_runs_from_nothing(tmp_path):
    """From an empty file to head, with no hand-holding."""
    db = tmp_path / "chain.db"
    url = f"sqlite+aiosqlite:///{db}"

    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, (
        "alembic upgrade head failed on an EMPTY database. A schema that cannot "
        "be built from nothing is a deploy that cannot be repeated:\n"
        f"--- stdout ---\n{up.stdout[-2000:]}\n--- stderr ---\n{up.stderr[-4000:]}")

    cur = _alembic(["current"], url)
    assert cur.returncode == 0, cur.stderr[-2000:]
    assert "(head)" in cur.stdout, (
        f"the chain ran but did not land on head: {cur.stdout.strip()!r}")


def test_the_chain_is_idempotent(tmp_path):
    """Running it twice is what a redeploy does.

    The guards are per statement, not one early return, because the database
    this has to survive is not only the empty one: production's schema was built
    by `create_all` on boot, so "has this column but not that index" is a real
    state and a whole-revision skip would step over the half that is missing.
    """
    db = tmp_path / "twice.db"
    url = f"sqlite+aiosqlite:///{db}"

    first = _alembic(["upgrade", "head"], url)
    assert first.returncode == 0, first.stderr[-3000:]

    again = _alembic(["upgrade", "head"], url)
    assert again.returncode == 0, (
        "the second `alembic upgrade head` failed; a migration that only works "
        f"once is one nobody can rerun:\n{again.stderr[-3000:]}")
