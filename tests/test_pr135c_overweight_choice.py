"""PR 135c (wave 4): an overweight sleeve is a choice -- trim it back, or raise the
sleeve to what you hold -- with the goal model's recommendation.

The plan's v1 ask (owner, 2026-10-14): "make sure that the recommendation cards should
appear also in overweight" -> "option 3 (trim or raise the size) with a recommendation
that is based on the agent's analysis". One card per position, as before.

The rule, fixed before any card showed it: recommend RAISE only when raising does not
lower the goal's chance and does not raise the chance of halving by `material_odds_pts`
or more; otherwise TRIM (also when it could not be measured).

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import sleeve_moves as sm  # noqa: E402

SEMIS = "btm_trend_soxl"
VOL = "btm_vol_target_tqqq"


# --------------------------------------------------------------------------- #
# the rule
# --------------------------------------------------------------------------- #
def test_raise_only_when_it_costs_no_goal_chance_and_little_crash_risk():
    assert sm.raise_recommendation({"ok": True, "goal_pts": 1.5, "halve_pts": 2.0}, 5.0)[0] == "raise"
    assert sm.raise_recommendation({"ok": True, "goal_pts": 0.0, "halve_pts": None}, 5.0)[0] == "raise"
    rec, why = sm.raise_recommendation({"ok": True, "goal_pts": -0.5, "halve_pts": 0.0}, 5.0)
    assert rec == "trim" and "cost 0.5 points" in why
    rec, why = sm.raise_recommendation({"ok": True, "goal_pts": 2.0, "halve_pts": 6.0}, 5.0)
    assert rec == "trim" and "halving" in why
    rec, why = sm.raise_recommendation(None, 5.0)
    assert rec == "trim" and "could not be measured" in why


def test_an_overweight_sleeve_gets_a_raise_move_and_it_never_becomes_its_own_card():
    moves = sm.candidate_moves({SEMIS: 10.0}, {}, {SEMIS: 27.34})
    raise_ = [x for x in moves if x["kind"] == "raise"]
    assert raise_ and raise_[0]["book"] == {SEMIS: 27.3}
    stored = {"moves": [{**raise_[0], "ok": True, "goal_pts": 9.0, "halve_pts": -9.0,
                         "after": {"p_goal_pct": 30, "p_halve_pct": 50}}]}
    assert sm.select(stored, 5.0) == []


# --------------------------------------------------------------------------- #
# one window per strategy (production, 2026-10-18: 8.5% -> 7.7% with all loaded)
# --------------------------------------------------------------------------- #
async def test_each_added_strategy_is_scored_on_its_own_window(own_session, monkeypatch):
    from app.services import frontier_service as fs
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    calls = []

    async def fake(session, user, books, *, extra_sleeves=None):
        calls.append(tuple(extra_sleeves or ()))
        base = 8.5 if not extra_sleeves else 7.7          # a young fund shortens the window
        return {"ok": True, "scores": [{"ok": True, "p_goal_pct": base + (0 if i == 0 else 1.0),
                                        "p_halve_pct": 1.0, "median": 1.0, "growth_cagr_pct": 1.0}
                                       for i, _ in enumerate(books)]}

    monkeypatch.setattr(fs, "score_books", fake)
    async with own_session() as s:
        u = await ensure_superadmin(s)
        await sv.add_or_resize(s, u, VOL, 40.0)
        await s.commit()
        out = await sm.compute(s, u)
    assert out["ok"] and out["before"]["p_goal_pct"] == 8.5, "the headline is the plain solve"
    assert all(len(c) <= 1 for c in calls), "never two added strategies in one solve"
    assert () in calls
    for mv in out["moves"]:
        assert mv["goal_pts"] == 1.0, "each delta is measured against its own window's baseline"
        assert mv["before"]["p_goal_pct"] == (7.7 if mv["kind"] == "hedge" else 8.5)


# --------------------------------------------------------------------------- #
# on a book
# --------------------------------------------------------------------------- #
def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


async def _store(own_session, stored):
    from app.models.tables import KVSetting
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        u = await ensure_superadmin(s)
        s.add(KVSetting(key=sm.KV_PREFIX + u.email, value=json.dumps(stored)))
        await s.commit()


def _scored(goal_pts, halve_pts):
    return {"ok": True, "as_of": "2026-10-18T04:40:00+00:00", "held": {SEMIS: 10.0},
            "before": {"p_goal_pct": 20.0, "p_halve_pct": 60.0},
            "moves": [{"kind": "raise", "from": SEMIS, "to": SEMIS, "pct": 30.0, "ok": True,
                       "book": {SEMIS: 30.0}, "goal_pts": goal_pts, "halve_pts": halve_pts,
                       "after": {"p_goal_pct": 20 + goal_pts, "p_halve_pct": 60 + halve_pts}}]}


def _book(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("SOXL", 3000.0), _pos("VTI", 4000.0), _pos("BND", 3000.0, "Fixed Income")]})
    assert c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=10").json()["ok"]


def _soxl_card(c):
    recs = c.get("/api/v1/recommendations").json()
    return next((r for r in recs["recommendations"] + recs.get("more_recommendations", [])
                 if (r.get("meta") or {}).get("ticker") == "SOXL" and r["id"].startswith("rec_")
                 and (r.get("apply") or {}).get("kind") in ("trim", "overweight_choice")), None)


async def test_the_overweight_card_offers_both_and_recommends_raise_when_it_helps(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _scored(1.0, 0.5))
        card = _soxl_card(c)
    assert card and card["apply"]["kind"] == "overweight_choice", card
    ap = card["apply"]
    assert set(ap["options"]) == {"trim", "raise"} and ap["options"]["trim"]["kind"] == "trim"
    assert ap["recommended"] == "raise" and ap["raise_to_pct"] == 30.0 and ap["trim_to_pct"] == 10.0
    assert "raise the sleeve to 30%" in card["action"]


async def test_trim_is_recommended_when_raising_costs_goal_chance(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _scored(-2.0, 1.0))
        card = _soxl_card(c)
    assert card["apply"]["recommended"] == "trim" and "cost 2 points" in card["apply"]["recommended_why"]


async def test_raise_sets_the_size_and_sells_nothing(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _scored(1.0, 0.5))
        card = _soxl_card(c)
        before = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept?choice=raise")
        assert r.status_code == 200, r.text
        sizes = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        after = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
    assert sizes == {SEMIS: 30.0}
    assert after == before, "raising the size must not trade"


async def test_trim_sells_the_excess(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _scored(-2.0, 1.0))
        card = _soxl_card(c)
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept?choice=trim")
        assert r.status_code == 200, r.text
        soxl = sum(p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"] if p["ticker"] == "SOXL")
        sizes = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
    assert soxl < 100.0 and sizes == {SEMIS: 10.0}


async def test_accept_without_an_answer_is_refused(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _scored(1.0, 0.5))
        card = _soxl_card(c)
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
    assert r.status_code == 400 and "needs a choice" in r.text


async def test_without_a_scored_raise_it_is_the_plain_trim_card(own_session):
    with TestClient(m.app) as c:
        _book(c)
        card = _soxl_card(c)
    assert card and card["apply"]["kind"] == "trim"
