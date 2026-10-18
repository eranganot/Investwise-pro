"""PR 136b (wave 5): "the best book we can measure for your goal" -- the card.

* a better book (by `material_odds_pts`) -> actionable; Accept sets the sleeves, sells nothing;
* an empty book -> the starter book;
* nothing material while under the on-track bar -> one plain card saying so, with the
  best measured and the price of the halving limit (the chance without the rules, and
  its chance of halving) -- the one lever left, which is the owner's.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import book_optimizer as bo  # noqa: E402

VOL = "btm_vol_target_tqqq"


def _result(held, best, steps, before, after, starter=False):
    return {"ok": True, "as_of": "2026-10-18T05:10:00+00:00", "starter": starter, "held": held, "best": best,
            "halve_limit_pct": 50.0, "on_track_pct": 70.0, "min_step_pts": 0.5, "candidates_tried": 24,
            "steps": steps, "before": before, "after": after, "protection": None, "core": None}


async def _store(own_session, r):
    from app.models.tables import KVSetting
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        u = await ensure_superadmin(s)
        s.add(KVSetting(key=bo.KV_PREFIX + u.email, value=json.dumps(r)))
        await s.commit()


def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


def _book(c, pct=40):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("TQQQ", 4000.0), _pos("VTI", 3000.0), _pos("BND", 3000.0, "Fixed Income")]})
    assert c.post(f"/api/v1/strategies/{VOL}/apply?sleeve_pct={pct}").json()["ok"]


def _cards(c):
    """Optimiser cards on Today -- an actionable one is a card; the ceiling note, which
    asks nothing of you, is a heads-up (PR 102) carrying the full card underneath."""
    j = c.get("/api/v1/recommendations").json()
    cards = j["recommendations"] + j.get("more_recommendations", [])
    cards += [h["card"] for h in j.get("headsups", []) if h.get("card")]
    return [r for r in cards if (r.get("meta") or {}).get("optimizer")]


BEFORE = {"p_goal_pct": 9.0, "p_halve_pct": 0.5, "p_goal_without_rules_pct": 25.8, "p_halve_without_rules_pct": 66.7}


async def test_a_better_book_is_an_actionable_card_and_accept_sells_nothing(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, {VOL: 25.0, "div_dbmf": 15.0},
                                          [{"kind": "add", "sleeve": "div_dbmf", "goal_pts": 6.0, "book": {}}],
                                          BEFORE, {"p_goal_pct": 15.0, "p_halve_pct": 0.4}))
        cards = _cards(c)
        assert len(cards) == 1, cards
        card = cards[0]
        assert card["title"] == "The best book we can measure for your goal: 9% -> 15%"
        assert "add Managed Futures (DBMF) (+6)" in card["why"] and "Nothing is bought or sold" in card["impact"]
        before = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
        assert r.status_code == 200, r.text
        sizes = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        after = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
    assert sizes == {VOL: 25.0, "div_dbmf": 15.0} and after == before


async def test_nothing_material_under_the_bar_says_so_with_the_price_of_the_limit(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, {VOL: 40.0}, [], BEFORE, {"p_goal_pct": 9.0, "p_halve_pct": 0.5}))
        cards = _cards(c)
    assert len(cards) == 1 and (cards[0].get("apply") or {}).get("kind", "none") == "none", cards
    why = cards[0]["why"]
    assert "tried 24 changes" in why and "on-track bar is 70%" in why
    assert "Without your protective rules the chance is 25.8%, with a 66.7% chance of the book halving" in why
    assert "your halving limit is 50%" in why


async def test_a_small_gain_is_not_a_card_to_act_on(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, {VOL: 25.0, "div_dbmf": 15.0},
                                          [{"kind": "add", "sleeve": "div_dbmf", "goal_pts": 1.2, "book": {}}],
                                          BEFORE, {"p_goal_pct": 10.2, "p_halve_pct": 0.4}))
        cards = _cards(c)
    assert len(cards) == 1 and (cards[0].get("apply") or {}).get("kind", "none") == "none" and "ceiling" in cards[0]["meta"]


async def test_no_card_when_the_result_is_about_another_book(own_session):
    with TestClient(m.app) as c:
        _book(c, pct=35)
        await _store(own_session, _result({VOL: 40.0}, {VOL: 25.0, "div_dbmf": 15.0},
                                          [{"kind": "add", "sleeve": "div_dbmf", "goal_pts": 6.0, "book": {}}],
                                          BEFORE, {"p_goal_pct": 15.0, "p_halve_pct": 0.4}))
        assert _cards(c) == []


async def test_on_track_and_nothing_to_gain_means_no_card(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, {VOL: 40.0}, [],
                                          {**BEFORE, "p_goal_pct": 80.0}, {"p_goal_pct": 80.0}))
        assert _cards(c) == []


async def test_the_starter_card_reaches_today_for_an_empty_book(own_session):
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
        await _store(own_session, _result({}, {"div_dbmf": 15.0},
                                          [{"kind": "add", "sleeve": "div_dbmf", "goal_pts": 2.0, "book": {}}],
                                          {"p_goal_pct": 6.0, "p_halve_pct": 30.0},
                                          {"p_goal_pct": 8.0, "p_halve_pct": 20.0}, starter=True))
        cards = _cards(c)
    assert [x["title"] for x in cards] == ["Your starter book: goal chance 8%"]


async def test_an_empty_book_gets_the_starter_card(own_session):
    from app.services.feed_service import ensure_superadmin
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
    await _store(own_session, _result({}, {"div_dbmf": 15.0, "div_gold_trend": 10.0},
                                      [{"kind": "add", "sleeve": "div_dbmf", "goal_pts": 2.0, "book": {}},
                                       {"kind": "add", "sleeve": "div_gold_trend", "goal_pts": 1.0, "book": {}}],
                                      {"p_goal_pct": 6.0, "p_halve_pct": 30.0}, {"p_goal_pct": 9.0, "p_halve_pct": 20.0},
                                      starter=True))
    async with own_session() as s:
        cards = await bo.card(s, await ensure_superadmin(s))
    assert len(cards) == 1 and cards[0]["title"] == "Your starter book: goal chance 9%"
    assert cards[0]["apply"] == {"kind": "sleeve_move", "held": {}, "book": {"div_dbmf": 15.0, "div_gold_trend": 10.0}}
    async with own_session() as s:
        out = await __import__("app.services.sleeve_moves", fromlist=["x"]).apply_move(
            s, await ensure_superadmin(s), cards[0]["apply"])
    assert out["ok"], out
