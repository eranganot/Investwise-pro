"""W0-5 (owner, 2026-10-12: "yes, you can delete them").

`investwise.db` (a Phase-2 SQLite snapshot with no alembic_version) and
`frontend/` (a lone package-lock.json) were unused. The CORS default allowed
credentialed requests from http://localhost:5173, a dev server that does not
exist: the app is served from its own origin (/app).
"""
from fastapi.testclient import TestClient


def test_the_unused_files_are_gone():
    """Tracked files only: a local, git-ignored frontend/node_modules is not in the repo."""
    import subprocess
    out = subprocess.run(["git", "ls-files", "investwise.db", "frontend"], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "", out


def test_no_cross_origin_access_by_default():
    from app.core.config import Settings
    assert Settings.model_fields["frontend_origin"].default == ""


def test_a_ghost_origin_gets_no_cors_headers():
    from app.core.config import get_settings
    from app.main import app
    if get_settings().frontend_origin:
        return                       # an explicitly configured origin is the operator's choice
    with TestClient(app) as c:
        r = c.options("/api/v1/plan", headers={"Origin": "http://localhost:5173",
                                               "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
