"""Where the migrations run, and why it is no longer the start command.

PR 47 put `alembic upgrade head &&` in front of uvicorn. Against production it
did not complete, the `&&` did exactly what it was written to do, and every
endpoint returned 502 for two hours. Railway's card said "Deployment
successful" throughout, because that reflects build-and-deploy and not whether
anything answers. Reverted in PR 48; the three tests that pinned the migration
into the start command went with it, with a note that they come back **with the
fix, not before**. This is that fix.

**The root cause of PR 47 is still UNKNOWN**, and the deploy log that held the
answer is gone. What is known, on evidence gathered after the revert:

  * The migration **ran to completion**. `/health` returned
    `schema_revision: "0020_solver_verdicts"` afterwards, nothing in `app/`
    writes `alembic_version` (health.py only reads it), and the reverted boot
    logs `Ensuring database schema (auto_create_tables=True)` with no alembic
    line. This inverted the original diagnosis.
  * RULED OUT -- the shell chain. Re-run exactly as deployed against an empty
    database: `sh -c 'alembic upgrade head && echo ...'` exits 0 and continues.
  * RULED OUT -- `alembic/env.py` importing the app. It imports only
    `app.core.config`, `app.models.base` and `app.models`; no `app.main`, no
    scheduler, and it disposes its engine.
  * STILL OPEN -- alembic exited non-zero at interpreter teardown despite doing
    the work (an unraisable at shutdown would do it, and `&&` swallows the
    boot); or the process never exited at all, so the chain never advanced.

So the retry changes the MECHANISM rather than guessing at the cause, and every
surviving hypothesis is contained by the change:

  * `preDeployCommand` runs the migration as **its own step with its own log**.
    The missing artifact is exactly what ended the last investigation.
  * `preDeployTimeoutSeconds` turns "the process never exited" from a hang into
    a failed deploy at a known time.
  * `healthcheckPath` (PR 49) means a container that does not answer never
    takes traffic, so a repeat of PR 47 is a failed deploy with production
    still up rather than an outage.
  * The database is now stamped at `0020_solver_verdicts`, so
    `alembic upgrade head` is a **no-op** on this run. The first attempt is a
    test of the mechanism against a database that needs nothing done to it.

Rehearsed 2026-09-04 against a production-shaped postgres:16 -- schema built by
`create_all` plus the `main.py` ALTER list, then stamped at head, which is the
state PR 47 left production in. The exact pre-deploy command:

    $ sh -c 'alembic upgrade head && alembic current'
    0020_solver_verdicts (head)
    PRE-DEPLOY EXIT CODE=0

`alembic current` is chained after the upgrade so the log carries a positive
statement of where the database ended up, rather than only the absence of an
error.

What is kept from before: `/health.schema_revision`, which was ruled out as the
cause on the evidence (`/`, `/docs`, `/openapi.json` and `/health/ready` all
502'd, so no handler ran) and is how this attempt gets verified instead of
hoped at.

**Still true, and still the reason any of this was attempted:** production's
schema is maintained by `create_all` at boot plus a hand-maintained list of
`ALTER TABLE ... IF NOT EXISTS` statements in `main.py`, whose own comment
concedes *"this list IS the mechanism that keeps production's schema current"*.
That list exists because alembic could not be run against production from
outside -- Railway's `DATABASE_URL` names a private host that does not resolve
-- and it has already cost ten days of silently missing measurements when
migration 0015 was never mirrored into it.

Not financial advice.
"""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATE = "alembic upgrade head"
SERVE = "uvicorn app.main:app"


def _deploy() -> dict:
    # PR 119: the deploy is defined in .railway/railway.ts (railway.json retired).
    from tests._deploy_config import deploy
    return deploy()


def _pre_deploy() -> str:
    """`preDeployCommand` is a string or a single-item array, per the schema."""
    cmd = _deploy().get("preDeployCommand") or ""
    if isinstance(cmd, list):
        return " ".join(cmd)
    return cmd


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


def test_something_in_the_deploy_runs_the_migrations():
    """The test PR 48 removed, back in a shape production can survive.

    It was removed because it demanded a behaviour that took the site down --
    "a test that demands a behaviour production cannot survive is not a guard,
    it is a countdown". What it was protecting has not changed: without this,
    nothing runs alembic against production and the schema is maintained by a
    hand-edited DDL list that has silently missed a migration twice.
    """
    deploy = _deploy()
    where = [k for k in ("preDeployCommand", "startCommand")
             if MIGRATE in (json.dumps(deploy.get(k)) or "")]
    assert where, (
        f"nothing in the deploy runs {MIGRATE!r}. Production's schema is then "
        f"maintained by create_all plus the ALTER TABLE list in main.py, which "
        f"has already cost ten days of missing measurements.\n"
        f"  preDeployCommand: {deploy.get('preDeployCommand')!r}\n"
        f"  startCommand:     {deploy.get('startCommand')!r}")


def test_the_migration_is_a_pre_deploy_step_and_not_the_boot():
    """Where it runs is the whole lesson of PR 47.

    In the start command, a migration that fails or hangs is an app that never
    starts -- and Railway will happily retire the container that was working.
    As a pre-deploy step it is its own step with its own log and its own
    timeout, and a failure is a failed deploy.
    """
    start = _deploy().get("startCommand") or ""
    assert MIGRATE not in start, (
        f"the migration is back in the start command. That is PR 47, which "
        f"returned 502 on every endpoint for two hours:\n  {start}")
    assert MIGRATE in _pre_deploy(), (
        f"the migration is not in preDeployCommand either:\n  {_pre_deploy()!r}")
    assert SERVE in start, f"railway.json does not start the app: {start!r}"


def test_the_pre_deploy_step_cannot_hang_forever():
    """One of the two surviving hypotheses is that the process never exited.

    A timeout does not explain that, and does not need to: it converts it from
    an unbounded hang into a failed deploy at a known time, with production
    still served by the container that works.
    """
    timeout = _deploy().get("preDeployTimeoutSeconds")
    assert isinstance(timeout, int) and timeout > 0, (
        f"preDeployTimeoutSeconds is {timeout!r}. The migration that took "
        f"production down may never have exited; without a timeout that is a "
        f"deploy which hangs rather than one which fails.")


def test_the_start_command_and_the_dockerfile_agree_about_serving():
    """Railway's startCommand overrides CMD, so they drift silently.

    This one survived the revert and is the reason it was cheap. Note what it
    does NOT assert any more: the Dockerfile does not migrate, and is not meant
    to. `preDeployCommand` is Railway-side, so `docker run` starts the server
    against whatever schema it is pointed at -- which is correct for a local
    container and is why the divergence is stated here rather than left to be
    rediscovered.
    """
    start = _deploy()["startCommand"]
    cmd = _dockerfile_cmd()
    assert SERVE in start, f"railway.json does not start the app: {start!r}"
    assert SERVE in cmd, f"the Dockerfile CMD does not start the app: {cmd!r}"
    assert MIGRATE not in cmd, (
        f"the Dockerfile CMD migrates. The deploy does that in its pre-deploy "
        f"step; doing it here too means every `docker run` mutates whatever "
        f"database it is pointed at:\n  {cmd}")


def test_health_reports_which_revision_the_database_is_on():
    """`None` is the honest answer, and the one production used to give.

    Kept through the revert, and now the field that says whether this attempt
    worked: it should read a revision, not null, once the pre-deploy step has
    run against production for the first time.
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
