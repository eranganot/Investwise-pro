"""C29 (owner, 2026-10-24): "the chances to beat the market (SPY) with my current portfolio
is only 4.2% ... I would expect a recommendation card that reviews my portfolio and says
what I need to add/remove/edit"; answered "one card, ranked options".

Live on 808b000 the optimiser ranked by goal chance (0.1-1.2% for every book), so nothing
passed `min_step` and Today said only "No sleeve change we can measure lifts your goal
chance". It now ranks by the amount reached at the bar, rules as armed, and the card holds
the book against SPY with every measured change best first, each with its own number.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import json  # noqa: E402
import uuid  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import book_optimizer as bo  # noqa: E402
from tests.test_pr136a_book_optimizer import _book as _plan_book  # noqa: E402
from tests.test_pr136b_optimizer_card import VOL, _book, _cards, _store  # noqa: E402

BM = {"ticker": "SPY", "label": "the S&P 500", "amount_at_confidence": 30313.0, "roi_at_confidence_pct": 8.0}
BEFORE = {"p_goal_pct": 0.3, "p_halve_pct": 0.8, "amount_at_confidence": 25025.0, "roi_at_confidence_pct": 4.2,
          "benchmark": BM}


def _row(amount, roi, halve=0.8, p=0.3):
    return {"p_goal_pct": p, "p_halve_pct": halve, "amount_at_confidence": amount, "roi_at_confidence_pct": roi}


def _single(kind, sleeve, book, amount, roi, *, halve=0.8, material=True, ok=True):
    return {"kind": kind, "sleeve": sleeve, "book": book, "basis": "amount", "gain": amount - 25025.0,
            "material": material, "within_halving": ok, "before": _row(25025.0, 4.2), "after": _row(amount, roi, halve)}


# --------------------------------------------------------------------------- #
# the review (pure)
# --------------------------------------------------------------------------- #
def test_the_review_ranks_every_measured_change_by_what_it_adds_at_the_bar():
    singles = [_single("add", "div_dbmf", {VOL: 40.0, "div_dbmf": 15.0}, 26000.0, 5.0),
               _single("remove", VOL, {}, 29500.0, 7.4),
               _single("add", "div_kmlm", {VOL: 40.0, "div_kmlm": 10.0}, 25100.0, 4.3, material=False),
               _single("add", "div_2x_gold_trend", {VOL: 40.0, "div_2x_gold_trend": 10.0}, 31000.0, 8.5, ok=False),
               _single("remove_all", None, {}, 29500.0, 7.4)]
    prot = {"material": True, "changes": {"widen": [{"rule_id": "r", "from_level": 10.0, "to_level": 20.0}],
                                          "pause_rule_ids": [], "guard": None, "guard_settings": None},
            "current_amount_at_confidence": 25025.0, "best_amount_at_confidence": 27000.0,
            "best_p_goal_pct": 0.5, "best_p_halve_pct": 0.9, "best_roi_at_confidence_pct": 5.6}
    rv = bo._review(singles, [], {VOL: 40.0}, {VOL: 40.0}, {**BEFORE, "ok": True, "protection": prot}, None, None)
    assert [(o["kind"], o.get("move"), o.get("sleeve")) for o in rv["options"]] == [
        ("sleeve", "remove", VOL), ("protection", None, None), ("sleeve", "add", "div_dbmf")], \
        "best first; under 2% is not a change; over the halving limit is not offered; all-core is said, not offered"
    assert rv["benchmark"] == BM and rv["all_core"]["amount_at_confidence"] == 29500.0
    assert rv["options"][1]["gain_ils"] == 1975.0


async def test_the_optimiser_ranks_by_the_amount_and_measures_the_all_core_book(own_session, monkeypatch):
    """Goal chance flat at 0.3% for every book (as live); the amount separates them."""
    from app.services import frontier_service as fs
    from app.services.feed_service import ensure_superadmin
    adds = {"div_dbmf": 3000.0, "div_gold_trend": 900.0, "div_kmlm": 100.0}

    async def fake(session, user, books, *, extra_sleeves=None, nominal_nav=None, full=False):
        out = []
        for b in books:
            amt = 25000.0 + sum(adds.get(s, 0.0) for s in b) + (2000.0 if not b else 0.0)
            out.append({"ok": True, "p_goal_pct": 0.3, "p_halve_pct": 1.0, "median": 1.0, "growth_cagr_pct": 1.0,
                        "amount_at_confidence": amt, "roi_at_confidence_pct": amt / 6000.0, "benchmark": BM,
                        "proposed": {}})
        return {"ok": True, "scores": out}

    monkeypatch.setattr(fs, "score_books", fake)
    await _plan_book(own_session, {VOL: 40.0, "div_gold_trend": 10.0})
    async with own_session() as s:
        out = await bo.optimize(s, await ensure_superadmin(s))
    assert out["steps"] and out["steps"][0]["sleeve"] == "div_dbmf" and out["steps"][0]["gain"] == 3000.0
    assert "div_kmlm" not in [x["sleeve"] for x in out["steps"]], "100 is under 2% of the amount"
    rv = out["review"]
    assert rv["before"]["amount_at_confidence"] == 25900.0 and rv["benchmark"] == BM
    assert rv["all_core"] is not None and rv["all_core"]["amount_at_confidence"] == 27000.0
    assert rv["options"][0]["sleeve"] == "div_dbmf"


# --------------------------------------------------------------------------- #
# the card
# --------------------------------------------------------------------------- #
def _result(held, options, *, all_core=None, before=BEFORE, protection_core=None):
    return {"ok": True, "as_of": "2026-10-24T05:10:00+00:00", "starter": False, "held": held, "best": held,
            "halve_limit_pct": 30.0, "on_track_pct": 65.0, "min_step_pts": 0.5, "candidates_tried": 13,
            "steps": [], "before": {"p_goal_pct": 0.3, "p_halve_pct": 0.8}, "after": None,
            "protection": None, "core": protection_core,
            "review": {"before": {k: before.get(k) for k in bo._ROW_KEYS}, "benchmark": before.get("benchmark"),
                       "options": options, "measured": 13, "all_core": all_core, "amount_step_pct": 2.0}}


OPT_ADD = {"kind": "sleeve", "move": "add", "sleeve": "div_dbmf", "book": {VOL: 25.0, "div_dbmf": 15.0},
           "gain_ils": 6000.0, **_row(31025.0, 8.4, 0.6)}
OPT_REMOVE = {"kind": "sleeve", "move": "remove", "sleeve": VOL, "book": {}, "gain_ils": 2000.0, **_row(27025.0, 5.9)}


async def test_the_card_holds_the_book_against_spy_and_ranks_the_changes(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, [OPT_ADD, OPT_REMOVE]))
        [card] = _cards(c)
        assert card["title"] == "Your book vs the S&P 500: 4.2%/yr vs 8.0%/yr at your 65% bar"
        assert "₪25,025 (4.2%/yr)" in card["why"] and "the S&P 500 (SPY)" in card["why"]
        ap = card["apply"]
        assert ap["kind"] == "book_review" and ap["recommended"] == "o1"
        assert [x["label"] for x in ap["ranked"]] == ["Add Managed Futures (DBMF) at 15%",
                                                      f"Remove the {bo._name(VOL)} sleeve"]
        assert ap["ranked"][0]["beats_benchmark"] and not ap["ranked"][1]["beats_benchmark"]
        assert "₪25,025 -> ₪31,025 at the bar (4.2%/yr -> 8.4%/yr)" in ap["ranked"][0]["detail"]
        assert ap["options"]["o1"] == {"kind": "sleeve_move", "held": {VOL: 40.0}, "book": {VOL: 25.0, "div_dbmf": 15.0}}
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
        assert r.status_code == 400 or r.json().get("needs_choice"), r.text
        before = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept?choice=o1")
        assert r.status_code == 200, r.text
        sizes = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        after = {p["ticker"]: p["quantity"] for p in c.get("/api/v1/portfolio").json()["positions"]}
    assert sizes == {VOL: 25.0, "div_dbmf": 15.0} and after == before, "a sleeve change sells nothing"


async def test_nothing_that_adds_enough_says_so_with_the_all_core_number(own_session):
    with TestClient(m.app) as c:
        _book(c)
        await _store(own_session, _result({VOL: 40.0}, [], all_core={**_row(29500.0, 7.4), "gain_ils": 4475.0}))
        [card] = _cards(c)
    assert (card.get("apply") or {}).get("kind", "none") == "none"
    assert card["title"].startswith("Your book vs the S&P 500: 4.2%/yr vs 8.0%/yr")
    assert "With no sleeves at all (all core) the book measures about ₪29,500 (7.4%/yr)" in card["why"]
    assert "the sleeves do not earn their risk against the S&P 500" in card["why"]


async def test_above_the_index_and_on_track_with_nothing_to_gain_is_no_card(own_session):
    with TestClient(m.app) as c:
        _book(c)
        r = _result({VOL: 40.0}, [], before={**BEFORE, "roi_at_confidence_pct": 9.0})
        r["before"]["p_goal_pct"] = 80.0
        await _store(own_session, r)
        assert _cards(c) == []


async def test_a_protection_option_is_dropped_once_its_rule_moved(own_session):
    from app.models.tables import TradingRule
    from app.services.feed_service import ensure_superadmin
    with TestClient(m.app) as c:
        _book(c)
        async with own_session() as s:
            u = await ensure_superadmin(s)
            rule = TradingRule(id=uuid.uuid4(), subject=u.email, ticker="TQQQ", rule_type="trailing_stop",
                               mode="pct", level=20.0, active=True)
            s.add(rule)
            await s.commit()
            rid = str(rule.id)
        prot = {"kind": "protection", "gain_ils": 3000.0, **_row(28025.0, 6.4),
                "changes": {"widen": [{"rule_id": rid, "ticker": "TQQQ", "rule_type": "trailing_stop", "mode": "pct",
                                       "from_level": 20.0, "to_level": 40.0, "factor": 2.0}],
                            "pause_rule_ids": [], "guard": None, "guard_settings": None}}
        await _store(own_session, _result({VOL: 40.0}, [prot]))
        [card] = _cards(c)
        assert card["apply"]["ranked"][0]["label"] == "Protection: widen 1 stop (none is removed)"
        assert card["apply"]["options"]["o1"]["kind"] == "frontier_rules"
        async with own_session() as s:
            row = await s.get(TradingRule, uuid.UUID(rid))
            row.level = 25.0
            await s.commit()
        [card] = _cards(c)
    assert (card.get("apply") or {}).get("kind", "none") == "none", "measured at 20; it is 25 now"


async def test_a_stored_result_from_before_c29_keeps_the_old_card(own_session):
    with TestClient(m.app) as c:
        _book(c)
        r = _result({VOL: 40.0}, [])
        del r["review"]
        r["before"].update({"p_goal_without_rules_pct": 25.8, "p_halve_without_rules_pct": 66.7})
        await _store(own_session, r)
        [card] = _cards(c)
    assert card["title"].startswith("No sleeve change we can measure")


def test_the_option_choice_passes_the_route():
    import re

    import app.api.routes.recommendations as rr
    src = open(rr.__file__, encoding="utf-8").read()
    pat = re.search(r'pattern="(\^\(sell[^"]+)"', src).group(1)
    assert re.match(pat, "o1") and re.match(pat, "o5") and not re.match(pat, "o10")
    assert json.dumps(pat)


async def test_a_real_run_stores_the_amount_the_benchmark_and_the_review(own_session):
    from tests.test_frontier_goal import _reset_plan, _seed
    try:
        with TestClient(m.app) as c:
            _seed(c, 30)
            c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
            assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
            r = c.post("/api/v1/plan/optimizer/refresh").json()
        assert r.get("ok") and r.get("stored"), r
        rv = r["review"]
        assert rv["before"]["amount_at_confidence"] and rv["before"]["amount_at_confidence"] > 0
        assert rv["measured"] >= 1
        for o in rv["options"]:
            assert o["gain_ils"] > 0 and o["amount_at_confidence"] > rv["before"]["amount_at_confidence"]
    finally:
        await _reset_plan(own_session)
