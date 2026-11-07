"""Risk dials > Investment flavor: a value saves, and "No preference" (sent as 0) clears it."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_flavor_saves_and_no_preference_clears_it():
    with TestClient(app) as c:
        r = c.put("/api/v1/plan", json={"objective": "Grow", "preferred_depth": 3})
        assert r.status_code < 400 and r.json()["preferred_depth"] == 3
        assert c.get("/api/v1/plan").json()["preferred_depth"] == 3
        r = c.put("/api/v1/plan", json={"preferred_depth": 0})
        assert r.status_code < 400 and r.json()["preferred_depth"] is None
        assert c.get("/api/v1/plan").json()["preferred_depth"] is None
        assert c.put("/api/v1/plan", json={"preferred_depth": 9}).status_code == 422


def test_the_dials_sheet_sends_zero_for_no_preference_and_shows_the_flavor():
    from pathlib import Path
    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert 'b.preferred_depth=dp===""?0:+dp' in html
    assert 'flavor ${' in html
