"""C26 (owner, 2026-10-23): "once I choose the beat the market strategies (sleeves)
the goal is to get higher return than the s&p so a return of 3.7/yr would not be
good enough and I'll ignore the card..."

Live on eac1df4: the goal note and the "Set a target this book can reach" card both
offered 3.7%/yr (the return the book reaches at the 65% bar with the rules armed;
median 15.6%/yr, volatility 43%). Decided: compare to the S&P. The nightly solve
measures a US stock index fund (VTI, `us_equity`) on its own paths; no lever offers
a target below it, and the note says how the book compares.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs
from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import EST_KW, _prep
from tests.test_frontier_resize import _store_verdict

BM_ABOVE = {"id": "us_equity", "ticker": "VTI", "label": "a US stock index fund", "basis": "history, no rules, no cash",
            "amount_at_confidence": 52000.0, "roi_at_confidence_pct": 18.2}
BM_BELOW = {**BM_ABOVE, "amount_at_confidence": 30000.0, "roi_at_confidence_pct": 9.0}


def test_the_solve_measures_the_index_fund_on_the_same_paths():
    prep = _prep()
    v = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                      draws=0, light=True)
    bm = v["benchmark"]
    assert bm["id"] == "us_equity" and bm["ticker"] == "VTI"
    for k in ("p_goal_pct", "median", "p_halve_pct", "amount_at_confidence"):
        assert bm[k] is not None, k
    assert bm["book_amount_at_confidence_history"] == v["tail"]["before"]["amount_at_confidence"]
    again = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                          cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                          draws=0, light=True)
    assert again["benchmark"] == bm, "seeded: the same book gives the same comparison"


def test_no_index_column_means_no_comparison_not_a_guess():
    import numpy as np
    assert fs._benchmark_tail(np.zeros((10, 2)), ["lev", "CASH"], years=5, nav=1, target=2, monthly=0,
                              p_star=0.7, book_tail={}) is None
    assert fs.benchmark_line(None, {"roi_at_confidence_pct": 3.7}, star=65) == ""
    assert not fs.below_benchmark(None, {"roi_at_confidence_pct": 3.7})


def test_the_line_says_which_side_of_the_index_the_book_is():
    lv = {"roi_at_confidence_pct": 3.7}
    t = fs.benchmark_line(BM_ABOVE, lv, star=65)
    assert "3.7%/yr" in t and "18.2%/yr" in t and "VTI" in t and "not beating it" in t
    assert fs.below_benchmark(BM_ABOVE, lv)
    t = fs.benchmark_line(BM_BELOW, {"roi_at_confidence_pct": 12.0}, star=65)
    assert "ahead of" in t and not fs.below_benchmark(BM_BELOW, {"roi_at_confidence_pct": 12.0})


async def test_no_target_card_below_the_index(own_session):
    """The stored verdict's reachable target is 15%/yr; the index reaches 18.2%."""
    try:
        with TestClient(app) as c:
            c.post("/api/v1/recommendations/restore")
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur, benchmark=BM_ABOVE))
            goal = [x for x in _cards(c) if x.get("dimension") == "goal"]
            assert not [x for x in goal if (x.get("meta") or {}).get("lever") == "target"], \
                [x["title"] for x in goal]
            dep = [x for x in goal if (x.get("meta") or {}).get("lever") == "deposit"]
            assert dep and "not beating it" in dep[0]["why"] and "aim for 15%/yr" not in dep[0]["why"]
            # above the index, the target lever is offered as before, and the why says so
            await _store_verdict(own_session, _goal_verdict(cur, benchmark=BM_BELOW))
            goal = [x for x in _cards(c) if x.get("dimension") == "goal"]
            t = [x for x in goal if (x.get("meta") or {}).get("lever") == "target"]
            assert t and "ahead of" in t[0]["why"]
            c.post("/api/v1/recommendations/restore")
    finally:
        await _reset_plan(own_session)


async def test_the_goal_note_names_the_index_instead_of_a_lower_target(monkeypatch):
    from types import SimpleNamespace

    from app.services import book_optimizer as bo
    d = {"goal": {"roi_pct": 30, "deadline": "2031-12-31"},
         "levers": {"roi_at_confidence_pct": 3.7, "amount_at_confidence": 24316,
                    "extra_monthly_ils": None, "deadline_extra_years": None},
         "tail": {"before": {"extra_monthly_needed": 590.8}}, "benchmark": BM_ABOVE}

    async def _latest(session, user):
        return SimpleNamespace(detail=d)
    monkeypatch.setattr(fs, "latest", _latest)
    t = await bo._lever_sentence(None, None, {"on_track_pct": 65.0})
    assert "lower your target" not in t
    assert "18.2%/yr" in t and "not beating it" in t and "with your protective rules armed" in t


async def test_with_only_the_index_line_the_halving_limit_is_still_the_one_lever(monkeypatch):
    """PR 138c's swap must not fire on the index sentence alone (it names no lever)."""
    from app.services import book_optimizer as bo
    from app.services import sleeve_service as sv
    from types import SimpleNamespace
    d = {"goal": {"roi_pct": 30, "deadline": "2031-12-31"},
         "levers": {"roi_at_confidence_pct": 3.7, "amount_at_confidence": 24316,
                    "extra_monthly_ils": 500, "deadline_extra_years": None}, "benchmark": BM_ABOVE}

    async def _latest(session, user):
        return SimpleNamespace(detail=d)
    res = {"ok": True, "held": {}, "steps": [], "candidates_tried": 12, "on_track_pct": 65.0,
           "halve_limit_pct": 30.0, "after": {"p_goal_pct": 0.3},
           "before": {"p_goal_pct": 0.2, "p_goal_without_rules_pct": 21.3, "p_halve_without_rules_pct": 57.2}}

    async def _load(session, user):
        return res

    async def _none(session, user):
        return []
    monkeypatch.setattr(fs, "latest", _latest)
    monkeypatch.setattr(bo, "load", _load)
    monkeypatch.setattr(sv, "list_sleeves", _none)
    [c] = await bo.card(None, None)                    # a deposit lever: "one lever"
    assert "not beating it" in c["why"] and "add about 500 a month" in c["why"]
    assert "Changing that limit is one lever" in c["why"]
    d["levers"]["extra_monthly_ils"] = None
    d["tail"] = {"before": {"extra_monthly_needed": None}}
    [c] = await bo.card(None, None)
    assert "not beating it" in c["why"] and "What would close the gap" not in c["why"]
    assert "Changing that limit is the one lever left" in c["why"]
