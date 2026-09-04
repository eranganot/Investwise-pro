"""Nothing ran the migrations, and nothing could.

`railway.json` started uvicorn and only uvicorn. The Dockerfile did the same.
So on every deploy since the first one, the only thing that shaped production's
schema was `create_all` at boot -- plus a hand-maintained list of
`ALTER TABLE ... IF NOT EXISTS` statements in `main.py`, whose own comment
concedes the position:

    "This list IS the mechanism that keeps production's schema current, so a
     migration that adds a column and does not appear here has not really
     shipped."

That list exists because **alembic could not be run against production at all**.
The comment records both attempts: once against the sqlite URL in a local
`.env`, once on DNS, because Railway's `DATABASE_URL` names a private host that
does not resolve from outside their network. The cost is recorded too --
`strategy_backtests.benchmark_ticker` was added by migration 0015 on 2026-08-15,
never mirrored into the list, and every read of that table failed for **ten
days** while the app degraded silently to "no strategy has ever been measured".

The database is reachable from exactly one place: inside the deploy. So that is
where the migrations now run, ahead of uvicorn, with `&&` so a failed migration
stops the boot instead of starting an app against a schema it does not have.

This is only safe because PR 46 made the chain runnable from nothing and proved
it idempotent -- production has never been stamped, so the first run of this
executes 0001 through 0020 against a database that `create_all` already built.

`/health` now reports `schema_revision`, which is what makes the next step
checkable rather than hopeful: `AUTO_CREATE_TABLES` and that DDL list stay in
place until production says out loud which revision it is on.

Not financial advice.
"""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATE = "alembic upgrade head"
SERVE = "uvicorn app.main:app"


def _railway_start_command() -> str:
    cfg = json.loads((REPO / "railway.json").read_text(encoding="utf-8"))
    return cfg["deploy"]["startCommand"]


def _dockerfile_cmd() -> str:
    for line in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("CMD "):
            return line
    return ""


def test_the_deploy_migrates_before_it_serves():
    """And in that order: a schema is not something to fix after boot."""
    cmd = _railway_start_command()
    assert MIGRATE in cmd, (
        f"railway.json starts the app without running the migrations, so the only "
        f"thing shaping production's schema is create_all at boot:\n  {cmd}")
    assert SERVE in cmd, f"railway.json no longer starts the app: {cmd!r}"
    assert cmd.index(MIGRATE) < cmd.index(SERVE), (
        f"the migrations run after the app starts serving:\n  {cmd}")


def test_the_migration_must_stop_the_boot_if_it_fails():
    """`&&`, not `;`. A failed migration is not something to serve through."""
    cmd = _railway_start_command()
    between = cmd[cmd.index(MIGRATE) + len(MIGRATE):cmd.index(SERVE)]
    assert "&&" in between, (
        f"the migration and the server are chained with something other than "
        f"'&&', so the app would start against a schema the migration failed to "
        f"apply:\n  {cmd}")


def test_the_dockerfile_says_the_same_thing():
    """Railway's startCommand overrides CMD, so they drift silently.

    `docker run` is how anyone reproduces a deploy locally; if it skips the
    migrations it is not reproducing this one.
    """
    cmd = _dockerfile_cmd()
    assert MIGRATE in cmd, (
        f"the Dockerfile CMD does not run the migrations, so a container started "
        f"outside Railway behaves differently from the deploy:\n  {cmd}")
    assert SERVE in cmd, f"the Dockerfile CMD no longer starts the app: {cmd!r}"


def test_health_reports_which_revision_the_database_is_on():
    """`None` is the honest answer production has been giving all along."""
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient

    import app.main as m

    with TestClient(m.app) as c:
        body = c.get("/health").json()

    assert "schema_revision" in body, (
        "there is no way to ask production which revision it is stamped at, "
        "which is how a migration went ten days without anyone noticing it had "
        "not run")
    rev = body["schema_revision"]
    assert rev is None or isinstance(rev, str), rev
