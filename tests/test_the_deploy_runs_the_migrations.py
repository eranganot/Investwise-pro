"""What the deploy does about migrations, and what it does not.

PR 47 put `alembic upgrade head &&` in front of uvicorn so the migrations would
run in the one place the database is reachable from. Against production it did
not complete, and the `&&` did exactly what it was written to do: the app never
started. **Every endpoint returned 502 for two hours.** Railway's card said
"Deployment successful", because that reflects build-and-deploy and not whether
anything answers.

Reverted in PR 48. The three assertions that pinned the migration into the start
command went with it -- a test that demands a behaviour production cannot survive
is not a guard, it is a countdown. They come back with the fix, not before.

What is kept: `/health.schema_revision`. It was ruled out as the cause on the
evidence -- `/`, `/docs`, `/openapi.json` and `/health/ready` all returned 502
too, so the process was not listening at all and no request handler ran. And it
is the field that will show whether the next attempt actually worked, so
removing it would leave the retry as blind as the first try.

**Still true, and still the reason any of this was attempted:** nothing runs the
migrations against production. Its schema is maintained by `create_all` at boot
plus a hand-maintained list of `ALTER TABLE ... IF NOT EXISTS` statements in
`main.py`, whose own comment concedes *"this list IS the mechanism that keeps
production's schema current"*. That list exists because alembic could not be run
against production from outside -- Railway's `DATABASE_URL` names a private host
that does not resolve -- and it has already cost ten days of silently missing
measurements when migration 0015 was never mirrored into it.

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
    """The CMD as a plain command line.

    Docker's exec form is a JSON array -- `CMD ["uvicorn", "app.main:app", ...]`
    -- so the words are there but the SPACES are not, and a naive substring
    match for "uvicorn app.main:app" fails on a file that is perfectly correct.
    This test caught itself doing that during the PR 47 revert.
    """
    for line in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if not line.startswith("CMD "):
            continue
        body = line[len("CMD "):].strip()
        if body.startswith("["):
            try:
                return " ".join(json.loads(body))
            except json.JSONDecodeError:
                return body
        return body
    return ""


def test_the_start_command_and_the_dockerfile_agree():
    """Railway's startCommand overrides CMD, so they drift silently.

    This one survives the revert and is the reason it was cheap: whatever the
    boot does, `docker run` has to do the same, or nobody can reproduce a deploy
    locally. PR 47 was reverted in both files together because of this test.
    """
    start = _railway_start_command()
    cmd = _dockerfile_cmd()
    assert SERVE in start, f"railway.json does not start the app: {start!r}"
    assert SERVE in cmd, f"the Dockerfile CMD does not start the app: {cmd!r}"
    assert (MIGRATE in start) == (MIGRATE in cmd), (
        f"one of them migrates and the other does not, so a container started "
        f"outside Railway behaves differently from the deploy:\n"
        f"  railway.json: {start}\n  Dockerfile:   {cmd}")


def test_if_the_boot_migrates_it_must_stop_on_failure():
    """Conditional on purpose: it guards the shape, not the decision.

    If a future PR puts the migration back, it has to be chained with `&&` --
    starting an app against a schema a migration failed to apply is worse than
    not starting. That was never the part that broke; the migration itself was.
    """
    start = _railway_start_command()
    if MIGRATE not in start:
        return
    between = start[start.index(MIGRATE) + len(MIGRATE):start.index(SERVE)]
    assert "&&" in between, (
        f"the migration and the server are chained with something other than "
        f"'&&':\n  {start}")


def test_health_reports_which_revision_the_database_is_on():
    """`None` is the honest answer, and the one production gives.

    Kept through the revert. It was ruled out as the cause of the outage -- every
    endpoint 502'd, so no handler ran -- and it is how the next attempt gets
    verified instead of hoped at.
    """
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
