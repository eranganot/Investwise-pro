"""PR 98 - one return basis: the Plan screen, the projection and the risk report quote the goal model."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs

from tests.test_frontier_goal import _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict


def _verdict(cur):
    return _goal_verdict(cur, before={"goal_probability_pct": 38.1, "median_at_deadline": 58379,
                                      "growth_cagr_pct": 17.93, "volatility_pct": 50.32,
                                      "fraction_of_kelly": 0.97, "log_growth": 0.165},
                         market={"ok": True, "state": "risk_on", "recent_weight": 0.4})


async def test_book_expectation_is_the_current_goal_verdicts_numbers(own_session):
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            async with own_session() as s:
                user = await ensure_superadmin(s)
                before = await fs.book_expectation(s, user)
            await _store_verdict(own_session, _verdict(cur))
            async with own_session() as s:
                user = await ensure_superadmin(s)
                fe = await fs.book_expectation(s, user)
            assert fe == {"expected_return_pct": 17.93, "volatility_pct": 50.32, "basis": "frontier",
                          "confidence": "blended", "regime": "risk_on", "recent_weight": 0.4,
                          "age_hours": fe["age_hours"], "goal_probability_pct": 38.1}
            # a verdict about another plan is not this plan's number
            c.put("/api/v1/plan", json={"target_roi_pct": 25})
            async with own_session() as s:
                user = await ensure_superadmin(s)
                assert await fs.book_expectation(s, user) is None
            assert before is None or before["basis"] == "frontier"
    finally:
        await _reset_plan(own_session)


async def test_the_plan_screen_and_the_projection_quote_the_goal_model(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            plain = c.get("/api/v1/plan").json()
            assert plain["assumption_basis"] != "frontier"           # no verdict: measured/static, as before
            await _store_verdict(own_session, _verdict(cur))
            p = c.get("/api/v1/plan").json()
            assert p["portfolio_expected_roi_pct"] == 17.93
            assert p["portfolio_volatility_pct"] == 50.32
            assert p["assumption_basis"] == "frontier"
            assert p["expectation"]["confidence"] == "blended"
            assert "portfolio_expected_roi_pct_measured" in p
            proj = c.get("/api/v1/plan/projection").json()
            text = " ".join(proj.get("assumptions") or [])
            assert "~17.93%/yr compound return" in text and "your goal model" in text
    finally:
        await _reset_plan(own_session)


def test_the_basis_sentence_names_the_estimate_and_the_market():
    from app.api.routes.plan import _frontier_basis_text
    t = _frontier_basis_text({"confidence": "blended", "recent_weight": 0.7, "regime": "risk_off"})
    assert "blended estimate" in t and "70%" in t and "risk_off" in t and "same numbers as your goal cards" in t
