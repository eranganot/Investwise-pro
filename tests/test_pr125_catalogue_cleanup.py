"""PR 125 -- the Beat the Market catalogue clean-up (plan of 2026-10-13, PR 1).

Three things, each measured before it was changed:

* **A drift band for a multi-leg sleeve.** `btm_vol_target_tqqq` measured 162
  "trades a year" although its target moved only 10.6 times a year. The
  simulator re-trued any drift of 1% or more, and a 3x leg beside its 1x base
  drifts that far on most days (~70 re-trues a year for drift alone). The
  `rebalance_band` the plan meant to widen was not the cause: 0.5 traded MORE
  (201.7). A per-spec `drift_band` (default 0.01, so every other caller is
  unchanged) fixed it: 0.10 measured 24.7 trades a year at 32.27%/yr against
  32.03%/yr, worst drop 63.1% against 63.25%.
* **Measured tiers.** Channel Breakout (-61.3%) read "High" while Trend Nasdaq
  (-62.4%) read "Very high". A tier now comes from the measured worst drop.
* **Retiring without deleting (D87).** About ten paths resolve a sleeve's
  strategy through `strategy_catalog.get`; deleting an entry a book still runs
  would make each return None and silently stop steering that sleeve.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as m
from app.engines import strategy_backtest as bt
from app.services import strategy_catalog as cat


# --- the drift band -----------------------------------------------------------

def _two_legs(n=600, seed=7):
    """A 3x leg and its 1x base on the same random walk."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0005, 0.012, n)
    base = 100 * np.cumprod(1 + r)
    lev = 100 * np.cumprod(1 + 3 * r)
    return {"LEV": lev, "BASE": base}


def test_the_default_band_changes_nothing():
    """Every existing caller (cores, blends, the frontier) passes no band."""
    px = _two_legs()
    targets = [{"LEV": 0.5, "BASE": 0.5}] * 600
    a = bt._simulate(px, targets, cost_bps=5, cgt_rate=0.25)
    b = bt._simulate(px, targets, cost_bps=5, cgt_rate=0.25, drift_band=0.01)
    assert a["trades"] == b["trades"] and a["values"] == b["values"]


def test_a_wider_band_stops_re_truing_an_unchanged_target():
    """The defect: an unchanged 50/50 target re-trued for drift alone."""
    px = _two_legs()
    targets = [{"LEV": 0.5, "BASE": 0.5}] * 600
    tight = bt._simulate(px, targets, cost_bps=5, cgt_rate=0.25)
    wide = bt._simulate(px, targets, cost_bps=5, cgt_rate=0.25, drift_band=0.10)
    assert tight["trades"] > 3 * wide["trades"], (tight["trades"], wide["trades"])


def test_a_new_target_still_trades_at_the_floor():
    """The band is for drift. A rule that changes its mind is acted on."""
    px = _two_legs()
    targets = [{"LEV": 0.5, "BASE": 0.5}] * 300 + [{"LEV": 0.47, "BASE": 0.53}] * 300
    still = bt._simulate(px, [{"LEV": 0.5, "BASE": 0.5}] * 600, cost_bps=5,
                         cgt_rate=0.25, drift_band=1.0)
    moved = bt._simulate(px, targets, cost_bps=5, cgt_rate=0.25, drift_band=1.0)
    assert moved["trades"] > still["trades"]


def test_vol_target_carries_its_band_to_the_engine():
    spec = cat.backtestable(["btm_vol_target_tqqq"])[0]
    assert spec.get("drift_band") == 0.10, spec


# --- measured tiers -------------------------------------------------------------

@pytest.mark.parametrize("dd,label", [
    (35.12, "Medium"), (36.0, "Medium"), (37.31, "Medium-high"), (45.0, "Medium-high"),
    (56.28, "High"), (60.0, "High"), (61.34, "Very high"), (62.41, "Very high"),
    (-79.4, "Very high")])
def test_a_tier_is_read_off_the_measured_drop(dd, label):
    assert cat.measured_risk(dd) == label


def test_the_two_strategies_that_disagreed_now_agree():
    breakout = cat.get("btm_swing_breakout")
    trend = cat.get("btm_trend_tqqq")
    assert breakout["risk"] != trend["risk"], "the premise changed; re-read this test"
    lab = lambda e, dd: cat.risk_label(e, {"metrics": {"max_drawdown_pct": dd}})  # noqa: E731
    assert lab(breakout, 61.34) == lab(trend, 62.41) == "Very high"


def test_an_unmeasured_strategy_keeps_its_designed_tier():
    e = cat.get("btm_factor_stack")
    assert cat.risk_label(e, None) == e["risk"]
    assert cat.risk_label(e, {"ok": False, "metrics": None}) == e["risk"]


def test_plan_cards_carry_both_tiers():
    cards = {c["id"]: c for c in cat.as_plan_cards(
        {"btm_trend_tqqq": {"metrics": {"max_drawdown_pct": 62.41}}})}
    t = cards["btm_trend_tqqq"]
    assert t["risk_label"] == "Very high" and t["designed_risk"] == "Very high"
    assert t["risk_tolerance"] == "High"


# --- retiring -------------------------------------------------------------------

RETIRED = ("btm_swing_dip", "btm_swing_breakout")


def test_retired_strategies_still_resolve():
    """The whole reason for a flag instead of a deletion."""
    for sid in RETIRED:
        assert cat.get(sid) is not None
        assert cat.retired(sid)["decision"] == "D87"
        assert sid in cat.ids(), "dropped from ids(): its stored backtest would stop refreshing"
        assert sid not in cat.active_ids()


def test_a_retired_strategy_moves_somewhere_that_is_offered():
    for sid in RETIRED:
        to = cat.retired(sid)["move_to"]
        assert cat.get(to) is not None and not cat.retired(to), to


def test_nothing_else_is_retired():
    # PR 130 (D94): MH3 gave way to 3x Nasdaq Trend to Cash inside the High tier.
    assert {e["id"] for e in cat.CATALOG if e.get("retired")} == set(RETIRED) | {"btm_qld_trend"}


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 50, "cost_basis": 80,
         "expected_return_pct": 20, "volatility_pct": 60}]})


def test_a_retired_strategy_cannot_be_added_as_a_new_sleeve():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post("/api/v1/strategies/btm_swing_breakout/apply?sleeve_pct=10").json()
        assert r.get("ok") is False and r.get("error") == "strategy retired", r
        sl = c.get("/api/v1/plan/sleeves").json()
        assert "btm_swing_breakout" not in {x["strategy_id"] for x in sl.get("sleeves", [])}


async def _with_user(own_session, fn):
    from app.models.tables import User

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        out = await fn(s, user)
        await s.commit()
        return out


@pytest.mark.asyncio
async def test_a_book_that_runs_one_can_still_resize_it_and_move_it(own_session):
    """Retiring takes the strategy off the shelf, not out of a book."""
    from app.services import sleeve_service as sv
    from app.services.strategy_service import apply_strategy, swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            await sv.add_or_resize(s, user, "btm_swing_dip", 30.0)   # a legacy book
            resized = await apply_strategy(s, user, "btm_swing_dip", sleeve_pct=25.0)
            into = await swap_sleeve(s, user, from_id="btm_swing_dip",
                                     to_id="btm_swing_breakout")
            out = await swap_sleeve(s, user, from_id="btm_swing_dip", to_id="btm_qqq_hold")
            after = [(x.strategy_id, float(x.sleeve_pct)) for x in await sv.list_sleeves(s, user)]
            return resized, into, out, after

        resized, into, out, after = await _with_user(own_session, _go)

    assert resized.get("ok"), resized
    assert into["ok"] is False and "retired" in into["error"], into
    assert out["ok"], out
    assert after == [("btm_qqq_hold", 25.0)], after


def test_the_sleeve_count_never_proposes_adding_a_retired_strategy():
    import inspect

    from app.services import target_solver
    src = inspect.getsource(target_solver.solve_sleeve_count_for)
    assert "retired" in src, "the sleeve-count search would offer retired strategies again"


def test_explore_folds_them_and_offers_the_move():
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "Tried and did not beat holding" in html
    assert "/swap?to=" in html and "function swapRetired" in html
