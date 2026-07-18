"""'Mark as done' must not be a synonym for 'Ignore'.

Reported live: tapping "Mark as done" filed the card in the ignored list, so the
two buttons were indistinguishable. Done means handled (90-day window, its own
bucket); Ignore means not now (7-day window, restorable separately).
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 400, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14},
        {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
         "spot_price": 90, "listing_price": 90, "quantity": 10, "cost_basis": 100,
         "expected_return_pct": 5, "volatility_pct": 10},
    ]})


def _ids(c):
    return [r["id"] for r in c.get("/api/v1/recommendations").json()["recommendations"]]


def _advisory_ids(c):
    """Advisory cards keep being generated after they're marked done/ignored, so
    they're the ones whose suppression counters are observable. An actionable card
    that's been applied simply stops firing -- nothing left to count."""
    return [r["id"] for r in c.get("/api/v1/recommendations").json()["recommendations"]
            if not r["actionable"]]


def test_accept_counts_as_done_not_ignored():
    with TestClient(m.app) as c:
        _seed(c)
        ids = _advisory_ids(c)
        if not ids:
            return
        rid = ids[0]
        c.post(f"/api/v1/recommendations/{rid}/accept")
        r = c.get("/api/v1/recommendations").json()
        assert rid not in [x["id"] for x in r["recommendations"]]   # gone from Today
        assert r["dismissed_count"] == 0        # the bug: accept used to land it here
        # Prove it went into the *completed* bucket, not ignored. Asserting via the
        # bucket (not completed_count) is robust to whether the card would have
        # regenerated -- a background reprice can change the card set between calls,
        # and completed_count only counts cards that still regenerate.
        restored = c.post("/api/v1/recommendations/restore-completed").json()
        assert restored["restored"] >= 1


def test_ignore_counts_as_ignored_not_done():
    with TestClient(m.app) as c:
        _seed(c)
        ids = _advisory_ids(c)
        if not ids:
            return
        rid = ids[0]
        c.post(f"/api/v1/recommendations/{rid}/dismiss")
        r = c.get("/api/v1/recommendations").json()
        assert rid not in [x["id"] for x in r["recommendations"]]   # gone from Today
        assert r["completed_count"] == 0        # ignore must not land in "done"
        # Prove it's in the *ignored* bucket via the restore count (robust to
        # whether the card would regenerate; a background reprice can change the set).
        assert c.post("/api/v1/recommendations/restore").json()["restored"] >= 1


def test_restoring_ignored_does_not_resurrect_completed():
    with TestClient(m.app) as c:
        _seed(c)
        ids = _advisory_ids(c)
        if len(ids) < 2:
            return
        c.post(f"/api/v1/recommendations/{ids[0]}/accept")     # done
        c.post(f"/api/v1/recommendations/{ids[1]}/dismiss")    # ignored
        c.post("/api/v1/recommendations/restore")              # restore ignored only
        r = c.get("/api/v1/recommendations").json()
        live = [x["id"] for x in r["recommendations"]]
        assert ids[1] in live               # the ignored one is back
        assert ids[0] not in live           # the completed one stays gone
        # ...and it's genuinely in the completed bucket, not just filtered out.
        assert c.post("/api/v1/recommendations/restore-completed").json()["restored"] >= 1


def test_completed_can_be_restored_on_demand():
    with TestClient(m.app) as c:
        _seed(c)
        ids = _advisory_ids(c)
        if not ids:
            return
        c.post(f"/api/v1/recommendations/{ids[0]}/accept")
        out = c.post("/api/v1/recommendations/restore-completed").json()
        assert out["ok"] and out["restored"] >= 1
        r = c.get("/api/v1/recommendations").json()
        assert r["completed_count"] == 0
