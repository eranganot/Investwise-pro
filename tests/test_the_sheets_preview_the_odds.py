"""PR 104b: the Edit-goal and Edit-rules sheets preview the goal's odds before Save.

`POST /plan/frontier/preview` runs the same model and rules replay as the
nightly solve on the goal / rules AS THEY WOULD BE, and stores nothing.
"""
from fastapi.testclient import TestClient

from app.main import app
from tests.test_frontier_goal import _reset_plan, _seed


def _seed_goal(c):
    """A "30% on what you put in" goal needs something put in: one deposit."""
    _seed(c, 10)
    c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set",
                                                    "occurred_at": "2026-08-03T00:00:00Z"})


def _verdict_count(c):
    f = c.get("/api/v1/plan/frontier").json()
    return f.get("computed_at")


async def test_preview_answers_for_the_goal_as_it_would_be_and_stores_nothing(own_session):
    try:
        with TestClient(app) as c:
            _seed_goal(c)
            before = _verdict_count(c)
            now = c.post("/api/v1/plan/frontier/preview", json={}).json()
            assert now["ok"], now
            later = c.post("/api/v1/plan/frontier/preview", json={"target_date": "2036"}).json()
            lower = c.post("/api/v1/plan/frontier/preview", json={"target_roi_pct": 5}).json()
            more = c.post("/api/v1/plan/frontier/preview", json={"monthly_ils": 500}).json()
            assert later["ok"] and lower["ok"] and more["ok"]
            # The goal formula itself: a later deadline and a deposit RAISE a
            # "30% on every shekel" goal; a lower ROI lowers it.
            assert later["goal"]["target"] > now["goal"]["target"]
            assert more["goal"]["target"] > now["goal"]["target"] and more["goal"]["monthly"] == 500
            assert lower["goal"]["target"] < now["goal"]["target"]
            assert lower["headline"]["p_goal_pct"] >= now["headline"]["p_goal_pct"]
            assert _verdict_count(c) == before, "a preview must not store a verdict"
    finally:
        await _reset_plan(own_session)


async def test_preview_with_rules_as_they_would_be(own_session):
    try:
        with TestClient(app) as c:
            _seed_goal(c)
            r = c.post("/api/v1/rules", json={"ticker": "QQQ", "rule_type": "trailing_stop", "mode": "pct",
                                              "level": 10.0}).json()
            rid = (r.get("rule") or r).get("id")
            armed = c.post("/api/v1/plan/frontier/preview", json={}).json()
            every = {x["id"]: False for x in c.get("/api/v1/rules").json()["rules"]}
            paused = c.post("/api/v1/plan/frontier/preview", json={"armed": every}).json()
            assert armed["ok"] and paused["ok"]
            assert armed["headline"]["basis"] == "rules"
            # with every rule paused there is no replay: the headline falls back
            assert paused["headline"]["basis"] == "model_and_history"
            # and the rule itself is untouched
            rules = c.get("/api/v1/rules").json()["rules"]
            assert next(x for x in rules if x["id"] == rid)["active"] is True
            c.delete(f"/api/v1/rules/{rid}")
    finally:
        await _reset_plan(own_session)
