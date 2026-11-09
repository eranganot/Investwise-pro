"""Nothing asked the new container whether it answers before retiring the old one.

PR 47 shipped a start command production could not survive. Every endpoint
returned 502 for two hours, and Railway's card said **"Deployment successful"**
the whole time, because that reflects build-and-deploy and not whether anything
answers. The ship script's own 12-minute `/health` wait was the only thing that
told the truth, and by then the healthy container was already gone.

Railway has the gate and this service was not using it. From their healthcheck
reference:

    "Railway will query the endpoint until it receives a successful response
     (any 2xx status code). Only then will the new deployment be made active
     and the previous deployment inactive."

    "...the deployment will be marked as failed after the timeout expires"

So with `healthcheckPath` set, PR 47 would have failed its own deploy and left
the working container serving. The outage was not only a bad start command; it
was a bad start command shipped through a pipeline with no gate. This is the
gate. `healthcheckPath` and `healthcheckTimeout` are both real keys under
`deploy` in railway.schema.json, checked against the live schema 2026-09-04.

The third test below is the point of the other two. A probe that answers 200
whatever is wrong is decoration, and `/health` is exactly that by design --
`schema_revision()` swallows its own database error on purpose, because a
liveness probe that fails on a side query is worse than the thing it reports.
So the configured path has to be one that goes RED when the app is broken, and
that is asserted here rather than assumed.

Not financial advice.
"""
import os
import pathlib

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

REPO = pathlib.Path(__file__).resolve().parent.parent


def _deploy() -> dict:
    # PR 119: the deploy is defined in .railway/railway.ts (railway.json retired).
    from tests._deploy_config import deploy
    return deploy()


def test_the_deploy_declares_a_healthcheck():
    """Without this key Railway promotes the new container unconditionally."""
    deploy = _deploy()
    path = deploy.get("healthcheckPath")
    assert path, (
        "railway.json declares no healthcheckPath, so a container that never "
        "serves a single request still replaces the one that was working -- "
        "which is how a bad start command became a two-hour outage instead of "
        "a failed deploy")
    assert path.startswith("/"), path

    timeout = deploy.get("healthcheckTimeout")
    assert isinstance(timeout, int) and timeout > 0, (
        f"healthcheckTimeout must say how long to wait before calling the "
        f"deploy failed: {timeout!r}")


def test_the_healthcheck_path_is_a_route_this_app_serves():
    """A path with no route 404s, and a 404 is not a 2xx: every deploy fails.

    The opposite failure to the one above, and just as quiet -- it would look
    like the app was broken rather than like the config was.
    """
    import app.main as m

    path = _deploy()["healthcheckPath"]
    from tests._routes import flat_paths
    routes = set(flat_paths(m.app.routes))
    assert path in routes, (
        f"railway.json health-checks {path!r}, which this app does not serve. "
        f"Every deploy would time out and fail.\n"
        f"  candidates: {sorted(p for p in routes if p and 'health' in p)}")


def test_the_healthcheck_goes_red_when_the_app_is_broken():
    """The gate has to be able to say no.

    `/health` cannot: `schema_revision()` catches its own database failure and
    returns None, deliberately, so /health answers 200 with no database at all.
    Health-checking that path would restore the exact situation this is meant
    to fix -- a green light that means nothing.
    """
    from fastapi.testclient import TestClient

    import app.main as m
    from app.api.routes import health as health_mod

    path = _deploy()["healthcheckPath"]

    with TestClient(m.app) as c:
        assert c.get(path).status_code == 200, (
            "the healthcheck path does not pass on a working app")

        class _Dead:
            def connect(self):
                raise OSError("connection refused")

        original = health_mod.engine
        health_mod.engine = _Dead()
        try:
            broken = c.get(path)
        finally:
            health_mod.engine = original

    assert broken.status_code >= 400, (
        f"{path} answered {broken.status_code} with the database unreachable. "
        f"A probe that cannot fail cannot gate anything -- point "
        f"healthcheckPath at /health/ready, which checks the database, rather "
        f"than /health, which is documented not to.")
