"""PR 91 - the frontier's two dials and the user's own ROI target.

Eran, 2026-10-05: the Kelly fraction "is the default but I want to be able to
change it"; return confidence defaults to shrunk "BUT I want to add blended as
an option"; and the cards must say what they do to the target he set.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.engines import frontier as fr
from app.main import app
from app.services import frontier_inputs as fi
from app.services import frontier_service as fs

from tests.test_frontier_resize import EST_KW, _prep


def _est(confidence, measured=True):
    prep = _prep()
    meas = {"lev": ({"cagr_pct": 60.0, "volatility_pct": 85.0, "observations": 1190}, None)} if measured else {}
    return prep, fi.estimate(prep["ils"], prep["assets"], **{**EST_KW, "measurements": meas},
                             confidence=confidence)


# --------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------

def test_measured_uses_the_backtest_and_blended_sits_halfway():
    prep, sh = _est("shrunk")
    _, bl = _est("blended")
    _, me = _est("measured")
    i = [a["id"] for a in prep["assets"]].index("lev")
    q = fr.arithmetic_drift(60.0, 85.0)
    assert me["mu"][i] == pytest.approx(q)
    assert bl["mu"][i] == pytest.approx(0.5 * (sh["mu"][i] + q))
    assert sh["mu"][i] < bl["mu"][i] < me["mu"][i]
    # Every setting keeps the shrunk figure alongside, so a screen can show both.
    assert me["mu_shrunk"][i] == pytest.approx(sh["mu"][i])


def test_assets_without_a_view_do_not_move_with_the_dial():
    prep, sh = _est("shrunk")
    _, me = _est("measured")
    ids = [a["id"] for a in prep["assets"]]
    for a in ("us_equity", "fixed_income", "calm"):
        assert me["mu"][ids.index(a)] == pytest.approx(sh["mu"][ids.index(a)])


def test_an_unknown_confidence_falls_back_to_shrunk():
    assert fi.confidence_of(None) == fi.confidence_of("yolo") == "shrunk"
    assert fi.confidence_of(" Blended ") == "blended"


# --------------------------------------------------------------------------
# Kelly
# --------------------------------------------------------------------------

def test_the_fraction_is_clamped_to_the_offered_range():
    assert fr.kelly_fraction_for("Grow") == 0.5
    assert fr.kelly_fraction_for("Grow", 0.75) == 0.75
    assert fr.kelly_fraction_for("Grow", 3.0) == fr.KELLY_MAX
    assert fr.kelly_fraction_for("Grow", 0.01) == fr.KELLY_MIN


def _solve(**kw):
    return fs.resize_solve(_prep(), sleeves_pct={"lev": 40.0, "calm": 3.0},
                           core_basket={"VTI": 0.6, "BND": 0.4}, cash_pct=4.0, objective="Grow",
                           est_kw=EST_KW, draws=30, **kw)


def test_a_bolder_fraction_proposes_a_smaller_trim():
    half = next(s for s in _solve()["sleeves"] if s["id"] == "lev")
    full = _solve(kelly_fraction=1.0)
    lev = next(s for s in full["sleeves"] if s["id"] == "lev")
    assert full["kelly_fraction"] == 1.0 and full["kelly_source"] == "plan"
    assert lev["point_pct"] >= half["point_pct"]


# --------------------------------------------------------------------------
# the target
# --------------------------------------------------------------------------

def test_every_confidence_is_scored_and_the_ceiling_is_a_ceiling():
    v = _solve(target_roi_pct=30.0)
    assert set(v["scenarios"]) == set(fi.CONFIDENCE_LEVELS)
    for s in v["scenarios"].values():
        assert s["max_growth_pct"] >= s["before_growth_pct"] - 1e-6
        assert s["max_growth_pct"] >= s["after_growth_pct"] - 1e-6
    t = v["target"]
    assert t["roi_pct"] == 30.0 and t["confidence"] == "shrunk"
    assert t["reachable"] == (v["scenarios"]["shrunk"]["max_growth_pct"] >= 30.0)


def test_a_stable_but_small_resize_does_not_earn_a_card(monkeypatch):
    strong = _solve()
    assert strong["outcome"] == fs.RESIZE and strong["materiality"]["material"]
    monkeypatch.setattr(fs, "MATERIAL_GAP_CLOSE", 2.0)
    monkeypatch.setattr(fs, "MATERIAL_VOL_DROP_PCT", 1000.0)
    weak = _solve()
    assert weak["outcome"] == fs.IMMATERIAL and weak["materiality"]["material"] is False


def test_no_target_means_no_target_block():
    assert _solve()["target"] is None


V = {"target": {"roi_pct": 30.0, "confidence": "shrunk", "reachable": False, "before_meets": False,
                "after_meets": False, "measured_drops_below": True},
     "scenarios": {"shrunk": {"before_growth_pct": 10.1, "after_growth_pct": 9.6, "max_growth_pct": 10.8},
                   "blended": {"before_growth_pct": 24.0, "after_growth_pct": 13.4, "max_growth_pct": 30.0},
                   "measured": {"before_growth_pct": 39.6, "after_growth_pct": 17.3, "max_growth_pct": 66.0}}}


def test_the_card_says_an_unreachable_target_out_loud():
    txt = fs._target_text(V)
    assert "30%/yr" in txt and "not reachable" in txt and "10.8" in txt


def test_the_card_says_when_the_backtests_would_disagree():
    txt = fs._target_text(V)
    assert "39.6" in txt and "17.3" in txt and "below it" in txt
    assert len(fs._scenario_lines(V)) == 3


# --------------------------------------------------------------------------
# the plan API
# --------------------------------------------------------------------------

def test_put_plan_validates_and_get_plan_reports_the_effective_dials():
    with TestClient(app) as c:
        assert c.put("/api/v1/plan", json={"kelly_fraction": 1.5}).status_code == 422
        assert c.put("/api/v1/plan", json={"kelly_fraction": 0.1}).status_code == 422
        assert c.put("/api/v1/plan", json={"return_confidence": "yolo"}).status_code == 422
        r = c.put("/api/v1/plan", json={"objective": "Grow", "kelly_fraction": 0.75,
                                        "return_confidence": "blended"})
        assert r.status_code == 200
        f = c.get("/api/v1/plan").json()["frontier"]
        assert f["kelly_fraction"] == 0.75 and f["kelly_source"] == "plan"
        assert f["return_confidence"] == "blended" and f["kelly_default"] == 0.5
        assert f["kelly_range"] == [0.25, 1.0]
        c.put("/api/v1/plan", json={"kelly_fraction": 0.5, "return_confidence": "shrunk"})


# --------------------------------------------------------------------------
# the cold cache
# --------------------------------------------------------------------------

async def test_a_feed_that_refuses_twice_is_unavailable_not_unmeasurable(own_session, monkeypatch):
    from app.services.feed_service import ensure_superadmin
    calls = []

    async def fake_ctx(session, user):
        calls.append(1)
        return {"ok": True, "sleeves": [], "objective": "Grow", "series": {}, "est_kw": EST_KW,
                "unavailable": ["VTI", "BND"], "no_history": []}
    monkeypatch.setattr(fi, "load_context", fake_ctx)
    monkeypatch.setattr(fs, "RETRY_AFTER_S", 0.0)
    async with own_session() as s:
        user = await ensure_superadmin(s)
        out = await fs.compute_for(s, user, draws=2)
    assert len(calls) == 2                               # it waited and asked again
    assert out["outcome"] == fs.UNAVAILABLE and out["unavailable"] == ["VTI", "BND"]


async def test_changing_a_dial_retires_the_card_until_it_is_re_solved(own_session):
    from tests.test_frontier_resize import _pos, _store_verdict

    def frontier_cards(c):
        j = c.get("/api/v1/recommendations").json()
        return [x for x in j.get("recommendations", []) + j.get("more_recommendations", [])
                if (x.get("apply") or {}).get("kind") == fs.CARD_KIND]

    with TestClient(app) as c:
        for tk in ("SOXL", "SMH", "VTI", "BND", "TQQQ", "QQQ", "SCHD"):
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NASDAQ"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("SOXL", 40, 100, 60), _pos("SMH", 10, 100, 80),
            _pos("VTI", 30, 100, 90), _pos("BND", 20, 100, 100, "Fixed Income")]})
        c.put("/api/v1/plan", json={"objective": "Grow", "kelly_fraction": 0.5, "return_confidence": "shrunk"})
        c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=40")
        cur = float({s["strategy_id"]: s["sleeve_pct"]
                     for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}["btm_trend_soxl"])
        await _store_verdict(own_session, {
            "ok": True, "outcome": fs.RESIZE, "objective": "Grow", "kelly_target": 0.5,
            "kelly_fraction": 0.5, "confidence": "shrunk",
            "sleeves": [{"id": "btm_trend_soxl", "current_pct": cur, "proposed_pct": cur / 4,
                         "point_pct": cur / 4, "band_pct": [cur / 5, cur / 3], "act": True}],
            "core_basket": {"VTI": 0.6, "BND": 0.4}, "cash_pct": 0.0,
            "core_pct_before": 100 - cur, "core_pct_after": 100 - cur / 4,
            "before": {"fraction_of_kelly": 1.1, "volatility_pct": 40.0, "growth_cagr_pct": 10.0},
            "after": {"fraction_of_kelly": 0.5, "volatility_pct": 20.0, "growth_cagr_pct": 9.5},
            "draws": 100, "window": {"sessions": 2215}, "version": fs.FRONTIER_TAG})
        assert frontier_cards(c)                         # matches the dials: shown
        c.put("/api/v1/plan", json={"kelly_fraction": 0.75})
        assert frontier_cards(c) == []                   # solved for 0.5x: retired
        c.put("/api/v1/plan", json={"kelly_fraction": 0.5, "return_confidence": "blended"})
        assert frontier_cards(c) == []                   # solved on shrunk: retired
        c.put("/api/v1/plan", json={"return_confidence": "shrunk"})
        assert frontier_cards(c)                         # back to the dials it was solved for


def test_inputs_payload_carries_the_confidence_and_the_shrunk_figure():
    from tests.test_frontier_inputs import ASSUMPTIONS, GROW, MEASURED, SLEEVE, _series
    out = fi.build_inputs(_series(), sleeves=[SLEEVE], objective_target=GROW,
                          measurements={"lev": MEASURED}, class_assumptions=ASSUMPTIONS,
                          cash_rate_pct=3.0, confidence="measured")
    assert out["confidence"] == "measured"
    lev = next(a for a in out["assets"] if a["id"] == "lev")
    assert lev["drift"] > lev["shrunk_drift"]
    assert np.isfinite(lev["shrunk_drift"])
