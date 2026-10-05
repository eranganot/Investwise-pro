"""PR 94 - which core serves the goal, priced from each core's own tickers."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_inputs as fi
from app.services import frontier_service as fs

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan
from tests.test_frontier_inputs import ASSUMPTIONS, GROW, _series
from tests.test_frontier_resize import _pos, _store_verdict

EST_KW = {"objective_target": GROW, "measurements": {}, "class_assumptions": ASSUMPTIONS, "cash_rate_pct": 3.0}
C6040 = {"id": "c6040", "name": "60/40", "weights": {"VTI": 0.6, "BND": 0.4}}
CEQ = {"id": "ceq", "name": "All equity", "weights": {"VTI": 1.0}}
CAW = {"id": "caw", "name": "All weather", "weights": {"VTI": 0.35, "BND": 0.4, "IAU": 0.15, "DBC": 0.1}}
CNV = {"id": "cnv", "name": "Single stock", "weights": {"NVDA": 1.0}}


def _solve(candidates=(C6040, CEQ, CAW, CNV), current="c6040", target=30_000, **kw):
    prep = fi.prepare(_series(n=800), sleeves=[])
    return fs.core_solve(prep, sleeves_pct={}, current_core_id=current, candidates=list(candidates),
                         cash_pct=0.0, est_kw=EST_KW, nav=10_000, target=target, years=5.0,
                         draws=10, **kw)


def test_every_core_is_priced_from_its_own_tickers_and_the_rest_are_listed():
    v = _solve()
    assert v["ok"] and "cnv" in v["not_modelled"]
    ids = {r["id"] for r in v["candidates"]}
    assert ids == {"c6040", "ceq", "caw"}
    for r in v["candidates"]:
        assert r["score_pct"] == min(r["model_p_pct"], r["tail_p_pct"])
    elig = [r for r in v["candidates"] if r["eligible"]]
    assert sum(r["win_share"] for r in elig) == pytest.approx(1.0)


def test_a_current_core_that_cannot_be_priced_is_said_not_guessed():
    v = _solve(current="cnv")
    assert v["outcome"] == fs.NOT_MEASURABLE and v["reason"] == "CURRENT_CORE_NOT_MODELLED"


def test_a_switch_needs_a_material_robust_win(monkeypatch):
    keep = _solve()
    if keep["outcome"] == fs.CORE_SWITCH:            # the data may already justify one
        assert keep["best"]["score_pct"] - keep["current"]["score_pct"] >= fs.MATERIAL_PROB_PTS
    monkeypatch.setattr(fs, "MATERIAL_PROB_PTS", 1000.0)
    assert _solve()["outcome"] == fs.CORE_KEEP      # never on less than the bar
    monkeypatch.setattr(fs, "MATERIAL_PROB_PTS", -1000.0)
    monkeypatch.setattr(fs, "CORE_MIN_WIN_SHARE", 0.0)
    v = _solve()
    if v["best"]["id"] != "c6040":
        assert v["outcome"] == fs.CORE_SWITCH
        assert v["best"]["model_p_pct"] >= v["current"]["model_p_pct"]
        assert v["best"]["tail_p_pct"] >= v["current"]["tail_p_pct"]


def test_no_core_may_push_the_book_further_past_the_kelly_cap():
    v = _solve(kelly_setting=0.25)
    cur = v["current"]
    for r in v["candidates"]:
        if r["id"] != "c6040":
            assert r["eligible"] == (r["fraction_of_kelly"] <= max(0.25, cur["fraction_of_kelly"]) + 1e-3)


async def test_accepting_a_core_switch_realigns_the_core_and_a_moved_core_refuses(own_session):
    try:
        with TestClient(app) as c:
            for tk in ("SOXL", "SMH", "VTI", "BND", "TQQQ", "QQQ", "SCHD", "IAU", "DBC"):
                c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NASDAQ"})
            c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
                _pos("VTI", 60, 100, 90), _pos("BND", 40, 100, 100, "Fixed Income")]})
            c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
            assert c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"}).status_code < 400
            row = {"model_p_pct": 20.0, "tail_p_pct": 15.0, "score_pct": 15.0, "win_share": 0.1,
                   "volatility_pct": 12.0, "p_halve_pct": 2.0, "fraction_of_kelly": 0.3}
            best = {**row, "model_p_pct": 30.0, "tail_p_pct": 26.0, "score_pct": 26.0, "win_share": 0.8,
                    "id": "bal_all_weather", "name": "All-Weather",
                    "weights": {"VTI": 0.35, "BND": 0.4, "IAU": 0.15, "DBC": 0.1}}
            cur = {**row, "id": "bal_6040", "name": "Classic 60/40", "weights": {"VTI": 0.6, "BND": 0.4}}
            v = _goal_verdict(0.0, sleeves=[], core_choice={
                "outcome": fs.CORE_SWITCH, "current": cur, "best": best, "candidates": [best, cur],
                "draws": 30, "not_modelled": []})
            await _store_verdict(own_session, v)
            card = next((x for x in _cards(c) if x["id"].startswith("rec_goal_core_")), None)
            assert card is not None, [x["title"] for x in _cards(c)]
            assert card["apply"] == {"kind": fs.CORE_CARD_KIND, "strategy_id": "bal_all_weather",
                                     "from_strategy_id": "bal_6040", "objective": "Grow"}
            assert "15% -> 26%" in card["title"]
            r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
            assert r.get("applied") == fs.CORE_CARD_KIND, r
            assert r["core_switched"] == {"from": "bal_6040", "to": "bal_all_weather"}
            held = {p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]}
            assert {"IAU", "DBC"} & held, held                 # the core moved toward its new basket
            assert not r.get("unrecorded")
            # the card is gone: the core it was solved for is no longer the core
            assert not [x for x in _cards(c) if x["id"].startswith("rec_goal_core_")]
    finally:
        await _reset_plan(own_session)


async def test_a_stale_core_switch_is_refused_untouched(own_session):
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        user = await ensure_superadmin(s)
        out = await fs.apply_core_switch(s, user, {"strategy_id": "bal_all_weather",
                                                  "from_strategy_id": "no_such_core", "objective": "Grow"})
    assert out["refused"] == "CORE_CHANGED"
