"""PR 120: the goal has ONE chance, and every screen quotes it.

Found by the owner on 2026-10-11: Today's health card said "35.3% chance of
₪82,692 in 5.2 years" while the Plan tab said 16.3% (31% once the guard was
paused). Today quoted the projection chart's own run -- one lognormal over the
whole book, no protective rules -- and the Plan tab the goal model with the
rules as they are. PR 99 promised the same figure everywhere it appears.

The readers pinned here: Today's On track (half the health score), the
projection payload the Today "Chance of hitting your goal" row reads, and the
assistant / digest context.
"""
from fastapi.testclient import TestClient

from app.main import app

from tests.test_frontier_goal import _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict

HEADLINE = {"basis": "rules", "p_goal_pct": 31.0, "without_rules_pct": 30.1}


def _verdict(cur):
    return _goal_verdict(cur, before={"goal_probability_pct": 38.1, "median_at_deadline": 58379,
                                      "growth_cagr_pct": 17.93, "volatility_pct": 50.32,
                                      "fraction_of_kelly": 0.97, "log_growth": 0.165},
                         market={"ok": True, "state": "risk_on", "recent_weight": 0.4},
                         headline=HEADLINE)


async def test_today_plan_and_the_assistant_quote_one_chance(own_session):
    from app.services import context_service as cs
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _verdict(cur))
            proj = c.get("/api/v1/plan/projection").json()
            assert proj["goal_headline"]["p_goal_pct"] == 31.0, proj.get("goal_headline")
            # The chart's own run is a different number (or none at all here).
            chart = proj.get("probability_meets_target")
            assert chart is None or round(chart * 100, 1) != 31.0

            h = c.get("/api/v1/health-check").json()
            # On HEAD: the chart's no-rules number.
            assert h["on_track"] == 31 and h["on_track_basis"] == "goal_model", h
            assert "31% chance" in h["on_track_detail"] and "protective rules" in h["on_track_detail"]
            assert "30.1% without them" in h["on_track_detail"]
        async with own_session() as s:
            ctx = await cs.gather(s, await ensure_superadmin(s))
        assert ctx["projection"]["probability_meets_target"] == 0.31, ctx["projection"]
        assert "protective rules as they are" in ctx["projection"]["probability_basis"]
    finally:
        await _reset_plan(own_session)


def test_without_a_goal_verdict_the_rough_figure_says_what_it_is(monkeypatch):
    """No fresh verdict: the projection's no-rules figure, labelled as such."""
    import app.api.routes.plan as plan_routes

    async def _proj(**_kw):
        return {"probability_meets_target": 0.353, "target_amount": 82692.0, "years": 5.2,
                "goal_headline": None, "with_contributions": {}}

    monkeypatch.setattr(plan_routes, "goal_projection", _proj)
    with TestClient(app) as c:
        _seed(c, 10)
        h = c.get("/api/v1/health-check").json()
    assert h["on_track"] == 35 and h["on_track_basis"] == "no_deposits", h
    # On HEAD: the same figure with nothing saying the rules were left out.
    assert "rough estimate: protective rules not simulated" in h["on_track_detail"], h


def test_the_today_chance_row_reads_the_goal_headline():
    """Static: the "Chance of hitting your goal" row prefers goal_headline."""
    import pathlib
    html = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    i = html.index("Chance of hitting your goal")
    window = html[max(0, i - 600):i]
    assert "goal_headline" in window, "the Today chance row must quote the goal's one chance"
