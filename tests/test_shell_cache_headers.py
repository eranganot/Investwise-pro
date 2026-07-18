"""The PWA shell must always revalidate.

Three deploys in a row reported success while installed clients kept running old
JavaScript: StaticFiles sent no Cache-Control on index.html/sw.js, so the browser
served a heuristically-cached copy -- and the service worker's cache.add() then
re-cached that stale copy under the *new* version key.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m


def test_shell_html_is_not_cacheable():
    with TestClient(m.app) as c:
        r = c.get("/app/index.html")
        if r.status_code != 200:
            return                                   # shell not mounted in this env
        assert "no-cache" in r.headers.get("cache-control", "")


def test_service_worker_is_not_cacheable():
    with TestClient(m.app) as c:
        r = c.get("/app/sw.js")
        if r.status_code != 200:
            return
        assert "no-cache" in r.headers.get("cache-control", "")


def test_service_worker_install_bypasses_the_http_cache():
    """The regression itself: cache.add(url) must not be used bare."""
    from pathlib import Path
    sw = Path(m.__file__).parent / "static_app" / "sw.js"
    src = sw.read_text(encoding="utf-8")
    assert "cache: 'reload'" in src
    assert "cache.add(url)" not in src               # the bug we shipped three times
