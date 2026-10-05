"""PR 16 -- the book's forward numbers stop guessing.

Eran, 2026-08-29: *"the portfolio expected return is not updated based on the
strategy/performance - check it"*, plus three other complaints that turned out
to be the same defect. `_portfolio_stats` and `compute_snapshot` both priced the
book from a static per-ticker table that puts TQQQ and SOXL at 12%/yr and 55%
volatility BUY-AND-HOLD -- and he holds them inside trend and dip-buy rules.

What this file holds down, hardest first:

1. **The old call signature produces the old numbers.** Both functions gained an
   optional argument and eight call sites did not change. If omitting it moved
   anything, this PR broke every screen it was trying to fix.
2. **Ground or abstain** (investing-discipline S1). Four named reasons a
   measurement is refused, none of them a silent fallback, and a refusal always
   falls back to the table *labelled as the table*.
3. **A stale pin is invalid, not soft** (S3). An engine version change refuses
   the measurement outright.
4. **The two halves are sourced independently.** A component can have a measured
   volatility and a static return, and the payload has to say so -- collapsing
   them into one flag is how provenance gets lost.
5. **Weighting is by HELD share, never target.** This book's sleeves target 64%
   and hold 15%; a forward number weighted by targets describes a book that does
   not exist.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from types import SimpleNamespace                       # noqa: E402

import pytest                                           # noqa: E402
from fastapi.testclient import TestClient               # noqa: E402

import app.main as m                                    # noqa: E402
from app.services import measured_assumptions as ma     # noqa: E402
from app.services.portfolio_analytics import compute_snapshot  # noqa: E402
from app.services.backtest_service import ENGINE_VERSION  # noqa: E402


def _row(**kw):
    base = dict(strategy_id="btm_swing_dip", ok=True, engine_version="a5",
                observations=2513, metrics={}, robustness={},
                period_start="2015-01-01", period_end="2025-01-01", computed_at=None,
                reason="")
    base.update(kw)
    return SimpleNamespace(**base)


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 70, "listing_price": 70, "quantity": 100, "cost_basis": 60},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 80, "listing_price": 80, "quantity": 50, "cost_basis": 78},
    ]})


# ------------------------------------------------- 1. nothing moved by accident

def test_compute_snapshot_without_an_override_is_exactly_what_it_was():
    """Eight call sites pass no override. If this drifts, every one of them did."""
    pos = [{"ticker": "TQQQ", "market": "NASDAQ", "quantity": 100, "cost_basis": 60,
            "current_price": 70, "volatility_pct": None, "liquidity_score": None},
           {"ticker": "SCHD", "market": "NYSE", "quantity": 50, "cost_basis": 78,
            "current_price": 80, "volatility_pct": None, "liquidity_score": None}]
    a = compute_snapshot(pos)
    # TQQQ is 'leveraged' (55%) at 7000/11000, SCHD 'dividend_equity' (14%) at 4000/11000.
    expected = (7000 / 11000) * 55.0 + (4000 / 11000) * 14.0
    assert a["avg_volatility_pct"] == pytest.approx(expected, rel=1e-9)
    assert a["avg_volatility_pct_static"] == pytest.approx(expected, rel=1e-9)
    assert a["volatility_basis"] == "static"


def test_an_override_moves_the_risk_input_and_keeps_the_old_one_visible():
    pos = [{"ticker": "TQQQ", "market": "NASDAQ", "quantity": 100, "cost_basis": 60,
            "current_price": 70, "volatility_pct": None, "liquidity_score": None}]
    b = compute_snapshot(pos, 28.0)
    assert b["avg_volatility_pct"] == 28.0
    assert b["avg_volatility_pct_static"] == pytest.approx(55.0)
    assert b["volatility_basis"] == "measured"


def test_portfolio_stats_without_a_measurement_is_exactly_what_it_was():
    from app.api.routes.plan import _portfolio_stats
    rows = [SimpleNamespace(ticker="TQQQ", market="NASDAQ", quantity=100,
                            current_price=70, meta={"asset_class": "Equities"})]
    out = _portfolio_stats(rows)
    assert out["expected_roi"] == 12.0          # the leveraged row of the table
    assert out["volatility"] == 55.0
    assert out["assumption_basis"] == "static"
    # The comparison keys only appear when a measurement was actually supplied.
    assert "static_expected_roi" not in out


def test_a_measurement_replaces_each_half_only_if_it_produced_one():
    from app.api.routes.plan import _portfolio_stats
    rows = [SimpleNamespace(ticker="TQQQ", market="NASDAQ", quantity=100,
                            current_price=70, meta={"asset_class": "Equities"})]
    half = _portfolio_stats(rows, {"expected_return_pct": None, "volatility_pct": 27.5,
                                   "basis": "mixed", "measured_pct_of_book": 100.0})
    assert half["volatility"] == 27.5           # measured
    assert half["expected_roi"] == 12.0         # the table still, because there was none
    assert half["static_volatility"] == 55.0    # and what it would have said


# --------------------------------------------------------- 2/3. ground or abstain

def test_a_missing_row_is_refused_by_name():
    ok, why = ma.usable(None, engine_version="a5")
    assert ok is False and why == ma.NOT_MEASURED_NO_ROW


def test_a_failed_measurement_is_not_a_low_number():
    ok, why = ma.usable(_row(ok=False, reason="INSUFFICIENT_HISTORY"), engine_version="a5")
    assert ok is False and why == ma.NOT_MEASURED_FAILED


def test_an_engine_version_change_invalidates_the_pin_rather_than_softening_it():
    ok, why = ma.usable(_row(engine_version="a4"), engine_version="a5")
    assert ok is False and why == ma.NOT_MEASURED_STALE


def test_a_short_measurement_is_refused_at_the_engines_own_floor():
    ok, why = ma.usable(_row(observations=259), engine_version="a5")
    assert ok is False and why == ma.NOT_MEASURED_SHORT
    assert ma.usable(_row(observations=260), engine_version="a5")[0] is True


def test_the_floor_agrees_with_the_engine_it_copies():
    from app.engines.strategy_backtest import _MIN_OBSERVATIONS
    assert ma.MIN_OBSERVATIONS == _MIN_OBSERVATIONS


def test_the_reason_carries_a_fix_hint_not_just_a_code():
    d = ma._why_detail(ma.NOT_MEASURED_STALE, "btm_swing_dip", _row(engine_version="a4"), "a5")
    assert "a4" in d and "a5" in d
    d2 = ma._why_detail(ma.NOT_MEASURED_NO_ROW, "btm_swing_dip", None, "a5")
    assert "03:30" in d2


# ----------------------------------------------- 4. the two halves are separate

def test_the_default_return_basis_is_out_of_sample_not_the_whole_window():
    """W1 found not one strategy reaches FITTING_HELPS. A full-sample CAGR for a
    3x leveraged trend strategy over 2015-2025 is a bull-market extrapolation,
    and the app already RANKS on the out-of-sample figure."""
    from app.core.config import get_settings
    assert ma.RETURN_BASIS_OOS == "measured_oos"
    assert get_settings().assumption_return_basis == ma.RETURN_BASIS_OOS


def test_out_of_sample_is_read_when_present_and_differs_from_full_sample():
    metrics = {"cagr_pct": 28.0, "volatility_pct": 31.0}
    rob = {"out_of_sample": {"out_of_sample": {"cagr_pct": 16.5}}}
    assert ma._pick_return(metrics, rob, ma.RETURN_BASIS_OOS)[0] == 16.5
    assert ma._pick_return(metrics, rob, ma.RETURN_BASIS_FULL)[0] == 28.0


def test_a_missing_out_of_sample_abstains_rather_than_falling_back_to_full_sample():
    """Silently using the full-window number when the out-of-sample one is
    absent would hand back exactly the extrapolation this basis exists to
    avoid, under a label claiming otherwise."""
    v, why = ma._pick_return({"cagr_pct": 28.0}, {}, ma.RETURN_BASIS_OOS)
    assert v is None and why == ma.NOT_MEASURED_NO_OOS


def test_volatility_has_no_static_basis_option():
    """There is no reading under which a guess about an instrument beats a
    measurement of the rule being run, so the setting governs the return only."""
    assert ma._pick_volatility({"volatility_pct": 31.0}) == (31.0, None)
    assert ma._pick_volatility({})[1] == ma.NOT_MEASURED_NO_ROW


def test_a_component_can_be_measured_on_one_side_and_static_on_the_other():
    out = ma.blend([{"weight_pct": 100.0, "return_pct": 12.0, "volatility_pct": 31.0,
                     "return_source": ma.SOURCE_STATIC,
                     "volatility_source": ma.SOURCE_MEASURED}])
    assert out["basis"] == ma.BASIS_MIXED
    assert out["expected_return_pct"] == 12.0 and out["volatility_pct"] == 31.0


# -------------------------------------------------------------- blend arithmetic

def test_the_blend_is_nav_weighted():
    out = ma.blend([
        {"weight_pct": 60.0, "return_pct": 10.0, "volatility_pct": 30.0,
         "return_source": ma.SOURCE_MEASURED, "volatility_source": ma.SOURCE_MEASURED},
        {"weight_pct": 40.0, "return_pct": 5.0, "volatility_pct": 10.0,
         "return_source": ma.SOURCE_STATIC, "volatility_source": ma.SOURCE_STATIC},
    ])
    assert out["expected_return_pct"] == pytest.approx(8.0)
    assert out["volatility_pct"] == pytest.approx(22.0)
    assert out["basis"] == ma.BASIS_MIXED
    assert out["measured_pct_of_book"] == pytest.approx(60.0)


def test_an_uncharacterisable_holding_is_excluded_and_its_weight_reported():
    """Not counted as returning zero. That `or 0.0` is the exact bug
    `_portfolio_stats` used to have, which reported a normal equity book as
    '~0%/yr - behind'."""
    out = ma.blend([
        {"weight_pct": 50.0, "return_pct": 10.0, "volatility_pct": 20.0,
         "return_source": ma.SOURCE_MEASURED, "volatility_source": ma.SOURCE_MEASURED},
        {"weight_pct": 50.0, "return_pct": None, "volatility_pct": None,
         "return_source": None, "volatility_source": None},
    ])
    assert out["expected_return_pct"] == pytest.approx(10.0)   # not 5.0
    assert out["return_coverage_pct"] == pytest.approx(50.0)
    assert out["volatility_coverage_pct"] == pytest.approx(50.0)


def test_an_all_measured_book_says_measured_and_an_all_table_book_says_static():
    allm = ma.blend([{"weight_pct": 100.0, "return_pct": 9.0, "volatility_pct": 25.0,
                      "return_source": ma.SOURCE_MEASURED,
                      "volatility_source": ma.SOURCE_MEASURED}])
    assert allm["basis"] == ma.BASIS_MEASURED
    none = ma.blend([{"weight_pct": 100.0, "return_pct": 9.0, "volatility_pct": 25.0,
                      "return_source": ma.SOURCE_STATIC,
                      "volatility_source": ma.SOURCE_STATIC}])
    assert none["basis"] == ma.BASIS_STATIC
    assert none["measured_pct_of_book"] == 0.0


def test_an_empty_book_blends_to_nothing_rather_than_to_zero():
    out = ma.blend([])
    assert out["expected_return_pct"] is None
    assert out["volatility_pct"] is None
    assert out["basis"] == ma.BASIS_STATIC


# ---------------------------------------------------------------- 5. the routes

def test_the_plan_card_states_its_basis_even_with_nothing_measured():
    with TestClient(m.app) as c:
        _seed(c)
        p = c.get("/api/v1/plan").json()
    assert p["assumption_basis"] == "static"
    assert p["portfolio_expected_roi_pct"] is not None


def test_the_projection_card_states_its_basis():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection").json()
    assert r["assumption_basis"] in ("static", "mixed", "measured")
    joined = " ".join(r["assumptions"])
    assert "instrument table" in joined or "measured" in joined


def test_the_health_card_says_which_volatility_produced_the_risk_score():
    with TestClient(m.app) as c:
        _seed(c)
        h = c.get("/api/v1/health-check").json()
    assert h["volatility_basis"] in ("static", "measured")
    assert h["avg_volatility_pct_static"] is not None
    # The Risk score must remain reproducible from the volatility on the payload.
    from app.services.portfolio_analytics import risk_score_vs_budget
    assert h["risk_score"] == round(
        risk_score_vs_budget(h["avg_volatility_pct"], h["volatility_cap_pct"] / 100.0))


def test_a_book_with_no_sleeves_is_priced_entirely_from_the_table_and_says_so():
    """The honest degraded state: nothing measured, nothing claimed."""
    with TestClient(m.app) as c:
        _seed(c)
        h = c.get("/api/v1/health-check").json()
    asm = h.get("assumptions") or {}
    assert asm.get("basis") == ma.BASIS_STATIC
    assert asm.get("measured_pct_of_book") == 0.0
    assert "instrument table" in (asm.get("note") or "")


def test_the_return_caveat_travels_with_every_payload():
    with TestClient(m.app) as c:
        _seed(c)
        h = c.get("/api/v1/health-check").json()
    assert "FITTING_HELPS" in ((h.get("assumptions") or {}).get("caveat") or "")


def test_the_frontend_labels_both_the_banner_and_the_risk_chip():
    from pathlib import Path
    src = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert "volatility_basis" in src
    assert "assumption_basis" in src
    assert "buy-and-hold" in src
    # And no non-ASCII identifier slipped into the script.
    assert "был" not in src


# ------------------------------------------------ the measured path, end to end

@pytest.mark.asyncio
async def test_a_measured_sleeve_prices_the_book_and_the_numbers_actually_move(own_session):
    """The whole claim of PR 16, on a book shaped like Eran's.

    A TQQQ sleeve that the instrument table prices at 12%/yr and 55% volatility
    buy-and-hold, measured as the RULE it actually is: 16.5% out-of-sample at
    31% volatility. The table's 55% is what drove his Risk score to 15.
    """
    from sqlalchemy import select

    from app.models.tables import StrategyBacktest, User
    from app.services import sleeve_service as sv
    from app.services.measured_assumptions import book_assumptions

    with TestClient(m.app) as c:
        _seed(c)

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        await sv.add_or_resize(s, user, "btm_swing_dip", 60)
        s.add(StrategyBacktest(
            strategy_id="btm_swing_dip", ok=True, engine_version=ENGINE_VERSION,
            observations=2513, period_start="2015-01-01", period_end="2025-01-01",
            metrics={"cagr_pct": 28.0, "volatility_pct": 31.0, "max_drawdown_pct": -43.8},
            robustness={"out_of_sample": {"out_of_sample": {"cagr_pct": 16.5}}}))
        await s.commit()
        out = await book_assumptions(s, user)

    assert out["basis"] == ma.BASIS_MIXED          # TQQQ measured, SCHD from the table
    assert out["measured_pct_of_book"] > 0

    sleeve = next(c for c in out["components"] if c["kind"] == "sleeve")
    assert sleeve["volatility_source"] == ma.SOURCE_MEASURED
    assert sleeve["volatility_pct"] == 31.0        # not the table's 55
    assert sleeve["return_source"] == ma.SOURCE_MEASURED
    assert sleeve["return_pct"] == 16.5            # out-of-sample, not the 28 full-window
    # Both readings travel, so the extrapolation is visible rather than inferred.
    assert sleeve["measured_full_return_pct"] == 28.0
    assert sleeve["static_volatility_pct"] == 55.0

    # The book's volatility must land BELOW what the table alone would have said.
    assert out["volatility_pct"] < 55.0
    assert out["pins"] and out["pins"][0]["observations"] == 2513


@pytest.mark.asyncio
async def test_a_stale_measurement_falls_back_to_the_table_and_names_the_reason(own_session):
    from sqlalchemy import select

    from app.models.tables import StrategyBacktest, User
    from app.services import sleeve_service as sv
    from app.services.measured_assumptions import book_assumptions

    with TestClient(m.app) as c:
        _seed(c)

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        await sv.add_or_resize(s, user, "btm_swing_dip", 60)
        s.add(StrategyBacktest(
            strategy_id="btm_swing_dip", ok=True, engine_version="a4",   # stale
            observations=2513,
            metrics={"cagr_pct": 28.0, "volatility_pct": 31.0}, robustness={}))
        await s.commit()
        out = await book_assumptions(s, user)

    assert out["basis"] == ma.BASIS_STATIC
    assert out["measured_pct_of_book"] == 0.0
    why = [a for a in out["abstained"] if a["reason"] == ma.NOT_MEASURED_STALE]
    assert why, out["abstained"]
    assert "a4" in why[0]["detail"]
    # And the sleeve is still priced -- from the table, labelled as the table.
    sleeve = next(c for c in out["components"] if c["kind"] == "sleeve")
    assert sleeve["volatility_source"] == ma.SOURCE_STATIC
    assert sleeve["volatility_pct"] == 55.0


@pytest.mark.asyncio
async def test_weighting_is_by_held_share_not_by_target(own_session):
    """PR 6's distinction, applied forward. A sleeve targeting 60% of a book it
    holds none of must contribute nothing to the book's numbers."""
    from sqlalchemy import select

    from app.models.tables import StrategyBacktest, User
    from app.services import sleeve_service as sv
    from app.services.measured_assumptions import book_assumptions

    with TestClient(m.app) as c:
        # A book holding only SCHD -- the TQQQ sleeve below holds nothing.
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 80, "listing_price": 80, "quantity": 50, "cost_basis": 78}]})

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        await sv.add_or_resize(s, user, "btm_swing_dip", 60)     # targets 60%, holds 0
        s.add(StrategyBacktest(
            strategy_id="btm_swing_dip", ok=True, engine_version="a5",
            observations=2513,
            metrics={"cagr_pct": 28.0, "volatility_pct": 31.0}, robustness={}))
        await s.commit()
        out = await book_assumptions(s, user)

    # SCHD alone: the dividend-equity row of the table, 7%/14%.
    assert out["volatility_pct"] == pytest.approx(14.0, abs=0.01)
    assert out["measured_pct_of_book"] == 0.0
