"""W3 -- the projection was blind to your deposits.

Four things this file is here to hold down, in descending order of how much
damage they do if they break:

1. **THE ZERO-CONTRIBUTION PATH DID NOT MOVE.** The pre-W3 figures are pinned
   here as literals. `SimulationEngine.run()` was restructured to be able to
   step, and a restructure that changes numbers it did not intend to change is
   the failure mode -- every projection on every card comes through this method.
   Distributional equivalence is not enough: under `risk_distribution = "t"` the
   stepped path is not even distributionally equal, since a sum of Student-t
   draws is not Student-t.
2. **The deposits are an ordinary annuity**, matching what `recommendations.py`
   already tells you to pay. If the engine deposited at period start, the card
   would tell you to add X and then show you a future built on more than X.
3. **`None` is not `0`.** `total_contributed()` has always refused to conflate
   "never logged a deposit" with "deposited nothing", and `contribution_rate()`
   inherits that: four distinct reasons, none of them a silent zero.
4. **The rate is never invented.** A projection that assumed a savings rate
   would be a forecast wearing a measurement's clothes.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone   # noqa: E402
from decimal import Decimal                          # noqa: E402

import numpy as np                                   # noqa: E402
import pytest                                        # noqa: E402
from fastapi.testclient import TestClient            # noqa: E402
from sqlalchemy import select                        # noqa: E402

import app.main as m                                 # noqa: E402
from app.engines.simulation_engine import (          # noqa: E402
    CONTRIBUTION_TIMING, MAX_STEPS, SimulationEngine)
from app.models.tables import Contribution, User     # noqa: E402
from app.services import intake_service as isv       # noqa: E402


def _seed(c):
    """A book with a NAV, so the projection endpoint has something to project."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "VTI", "market": "NYSE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 200, "cost_basis": 90,
         "expected_return_pct": 8, "volatility_pct": 18},
    ]})

BASE = dict(initial_value=21542.0, expected_return_pct=8.0, volatility_pct=18.0,
            horizon_years=10, target_value=50000.0)

# Captured from HEAD~ -- the engine as it was before W3 touched it, seed 7.
# If a change to this engine moves one of these, that change moved every
# projection this app has ever shown, and the burden is on the change.
PRE_W3 = {
    "nominal_mean": 47531.47735981999,
    "nominal_p5": 15922.799834693356,
    "nominal_p50": 40463.13960626006,
    "nominal_p95": 103102.70198853838,
    "real_p50": 30108.375958634773,
    "prob_loss": 0.2761,
    "prob_meets": 0.3519,
}


# ---------------------------------------------------------------- 1. no drift

def test_the_no_deposit_projection_is_exactly_what_it_was_before_w3():
    r = SimulationEngine(seed=7).run(**BASE)
    assert r.nominal.mean == PRE_W3["nominal_mean"]
    assert r.nominal.p5 == PRE_W3["nominal_p5"]
    assert r.nominal.p50 == PRE_W3["nominal_p50"]
    assert r.nominal.p95 == PRE_W3["nominal_p95"]
    assert r.real.p50 == PRE_W3["real_p50"]
    assert r.probability_of_loss_real == PRE_W3["prob_loss"]
    assert r.probability_meets_target == PRE_W3["prob_meets"]


def test_none_and_zero_both_mean_the_old_path_in_the_engine():
    """The engine has nothing to say about None vs 0; the LEDGER does. Both take
    the single-shot branch, bit for bit."""
    a = SimulationEngine(seed=7).run(**BASE, contribution_per_year=None)
    b = SimulationEngine(seed=7).run(**BASE, contribution_per_year=0.0)
    assert a.model_dump() == b.model_dump()
    assert a.steps == 1
    assert a.nominal.p50 == PRE_W3["nominal_p50"]


def test_the_no_deposit_run_still_says_no_cash_flows():
    r = SimulationEngine(seed=7).run(**BASE)
    assert any("no rebalancing or cash flows" in a for a in r.assumptions)


def test_a_deposit_run_replaces_that_line_rather_than_keeping_a_false_one():
    r = SimulationEngine(seed=7).run(**BASE, contribution_per_year=12000.0)
    assert not any("no rebalancing or cash flows" in a for a in r.assumptions)
    joined = " ".join(r.assumptions)
    assert "12,000/yr" in joined and "120,000 in total" in joined


def test_the_t_distribution_is_the_reason_this_is_a_branch_and_not_a_refactor():
    """Stepping is distributionally equal to the single shot only under normal
    shocks. Under t it is not, so the zero path must be the ORIGINAL expression
    rather than a one-step case of the stepped one."""
    a = SimulationEngine(seed=7).run(**BASE, distribution="t")
    b = SimulationEngine(seed=7).run(**BASE, distribution="t", contribution_per_year=1.0)
    assert a.steps == 1 and b.steps == 120
    assert a.distribution == "t"


# ------------------------------------------------------- 2. the annuity is right

def test_the_deposits_compound_as_an_ordinary_annuity():
    """With volatility at zero the whole thing is closed form, so the arithmetic
    is checkable rather than plausible."""
    r = SimulationEngine(seed=7).run(
        initial_value=100000.0, expected_return_pct=6.0, volatility_pct=0.0,
        horizon_years=10, cpi_pct=0.0, fx_change_pct=0.0, runs=200,
        contribution_per_year=12000.0)
    mu, T, n = 0.06, 10.0, 120
    dt = T / n
    G = np.exp(mu * dt)
    per_step = 12000.0 * dt
    expected = 100000.0 * np.exp(mu * T) + per_step * ((G ** n - 1) / (G - 1))
    assert r.nominal.p50 == pytest.approx(expected, rel=1e-9)
    assert r.contribution_timing == CONTRIBUTION_TIMING == "end"


def test_deposits_at_period_end_agree_with_the_card_that_asks_for_them():
    """`recommendations.py` Lever A solves `pmt = gap*rm/(((1+rm)**n)-1)` -- the
    future value of an ORDINARY annuity. The engine must use the same timing or
    the card and the chart describe different amounts of money.

    They are not identical to the shekel and should not be asserted as such: the
    card compounds simply at r/12, the engine continuously at exp(mu*dt). That
    is a compounding difference of under a tenth of a percent. The TIMING is what
    has to match, and it does.
    """
    r = SimulationEngine(seed=7).run(
        initial_value=0.0, expected_return_pct=6.0, volatility_pct=0.0,
        horizon_years=10, cpi_pct=0.0, fx_change_pct=0.0, runs=100,
        contribution_per_year=12000.0)
    rm = 0.06 / 12
    lever_a = 1000.0 * ((((1 + rm) ** 120) - 1) / rm)
    assert r.nominal.p50 == pytest.approx(lever_a, rel=0.005)
    # Begin-of-period would be a whole extra period of growth -- ~0.5% here.
    assert r.nominal.p50 < lever_a * (1 + rm) ** 1 * 1.001


def test_the_total_deposited_does_not_depend_on_how_the_step_count_rounds():
    """A step count that changed how much money went in would be a bug no reader
    could see. 7.5 years does not divide into whole months evenly."""
    for years in (1, 3, 7.5, 10):
        r = SimulationEngine(seed=7).run(
            initial_value=1000.0, expected_return_pct=5.0, volatility_pct=10.0,
            horizon_years=years, runs=50, contribution_per_year=6000.0)
        assert r.contribution_total == pytest.approx(6000.0 * years, abs=0.01)


def test_the_step_count_is_capped_and_says_so():
    r = SimulationEngine(seed=7).run(
        initial_value=1000.0, expected_return_pct=5.0, volatility_pct=10.0,
        horizon_years=200, runs=20, contribution_per_year=1200.0)
    assert r.steps == MAX_STEPS
    assert any("capped" in a for a in r.assumptions)
    # Capped or not, the money in is the money in.
    assert r.contribution_total == pytest.approx(1200.0 * 200, abs=0.01)


def test_fx_drift_is_not_credited_to_money_that_was_not_there_yet():
    """Applying the horizon's whole FX factor to the terminal value would give a
    deposit made in year 9 of 10 the full ten years of currency drift."""
    naive_style = SimulationEngine(seed=7).run(
        initial_value=0.0, expected_return_pct=0.0, volatility_pct=0.0,
        horizon_years=10, cpi_pct=0.0, fx_change_pct=5.0, runs=50,
        contribution_per_year=1000.0)
    total = 10000.0
    assert naive_style.nominal.p50 < total * (1.05 ** 10)   # not every shekel got 10y
    assert naive_style.nominal.p50 > total                  # but the early ones drifted


# --------------------------------------------- 3. two probabilities, two meanings

def test_losing_money_means_something_different_once_you_are_adding_money():
    """`probability_of_loss_real` keeps its old meaning (below STARTING value).
    With deposits that is no longer "did I lose money", so the honest comparison
    gets its own field instead of quietly redefining the old one."""
    r = SimulationEngine(seed=7).run(**BASE, contribution_per_year=20000.0)
    assert r.money_in == pytest.approx(21542.0 + 200000.0, abs=0.01)
    # Ending above your starting NAV is nearly certain once you have added ten
    # times it; ending above everything you PUT IN is a different question.
    assert r.probability_of_loss_real < r.probability_of_loss_vs_money_in


def test_with_no_deposits_the_two_probabilities_are_the_same_question():
    r = SimulationEngine(seed=7).run(**BASE)
    assert r.money_in == pytest.approx(r.initial_value, abs=0.01)
    assert r.probability_of_loss_vs_money_in == r.probability_of_loss_real


# ------------------------------------------------ 4. the rate is measured, not made

async def _rate_after(own_session, entries):
    """Write a ledger with real dates, then measure it.

    Dates matter here and no route lets you set them, so the rows go in through
    a session on the test's OWN NullPool engine -- the `own_session` fixture --
    rather than beside a live TestClient on the app's pooled one. That is the
    cross-loop trap `conftest` documents and it has gone red on Postgres twice.
    """
    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        for amount, days_ago in entries:
            s.add(Contribution(
                subject=user.email, amount_ils=Decimal(str(amount)),
                kind="deposit" if amount > 0 else "withdrawal", note="",
                occurred_at=datetime.now(timezone.utc) - timedelta(days=days_ago)))
        if entries:
            await s.commit()
        return await isv.contribution_rate(s, user)


@pytest.mark.asyncio
async def test_an_empty_ledger_is_no_rate_and_never_a_rate_of_zero(own_session):
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [])
    assert r["ok"] is False
    assert r["reason"] == isv.RATE_NO_LEDGER
    assert r["total_ils"] is None          # the None that must survive
    assert r["per_year"] is None
    assert "not the same as a rate of zero" in r["detail"]


@pytest.mark.asyncio
async def test_a_ledger_that_nets_to_zero_is_a_different_fact_from_an_empty_one(own_session):
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [(5000, 800), (-5000, 100)])
    assert r["ok"] is False
    assert r["reason"] == isv.RATE_NOT_POSITIVE
    assert r["total_ils"] == 0.0           # zero, and it says zero -- not None
    assert r["rows"] == 2


@pytest.mark.asyncio
async def test_one_entry_is_an_opening_balance_not_a_savings_rate(own_session):
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [(20000, 900)])
    assert r["ok"] is False
    assert r["reason"] == isv.RATE_SINGLE_ENTRY
    assert r["total_ils"] == 20000.0
    assert "opening balance" in r["detail"]


@pytest.mark.asyncio
async def test_a_span_too_short_to_annualise_is_refused_rather_than_annualised(own_session):
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [(10000, 20), (10000, 3)])
    assert r["ok"] is False
    assert r["reason"] == isv.RATE_TOO_SHORT
    assert r["per_year"] is None
    assert str(isv.MIN_SPAN_DAYS) in r["detail"]


@pytest.mark.asyncio
async def test_a_real_ledger_measures_a_rate_and_reports_both_readings(own_session):
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [(20000, 730), (5000, 365), (5000, 30)])
    assert r["ok"] is True
    assert r["total_ils"] == 30000.0
    assert r["elapsed_years"] == pytest.approx(2.0, abs=0.02)
    assert r["per_year"] == pytest.approx(15000.0, rel=0.02)
    # Excluding the opening 20,000 -- always the smaller, usually the honest one.
    assert r["per_year_excluding_first"] == pytest.approx(5000.0, rel=0.02)
    assert r["per_year_excluding_first"] < r["per_year"]
    assert "opening capital" in r["detail"]


@pytest.mark.asyncio
async def test_the_rate_is_measured_from_the_first_entry_to_now_not_to_the_last(own_session):
    """First-to-last would report a stale rate forever and be undefined on the
    day you stop. First-to-now decays, which is what "you have averaged X since
    you started" should do."""
    with TestClient(m.app) as c:
        _seed(c)
    r = await _rate_after(own_session, [(10000, 1460), (10000, 1400)])
    assert r["ok"] is True
    assert r["elapsed_years"] == pytest.approx(4.0, abs=0.05)
    assert r["per_year"] == pytest.approx(5000.0, rel=0.05)


# ------------------------------------------------------------- 5. the route

def test_the_card_no_longer_drops_the_engines_own_cash_flow_caveat():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection").json()
    assert "message" not in r
    assert "cash flows" in " ".join(r["assumptions"]), r["assumptions"]


def test_with_no_ledger_the_card_says_why_rather_than_showing_nothing():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection").json()
    wc = r["with_contributions"]
    assert wc["available"] is False
    assert wc["measured"]["reason"] == isv.RATE_NO_LEDGER
    assert wc["note"]


def test_an_explicit_rate_is_honoured_and_labelled_as_yours():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection?contribution_per_year=24000").json()
    wc = r["with_contributions"]
    assert wc["available"] is True
    assert wc["source"] == "override"
    assert wc["per_year"] == 24000.0
    assert wc["projected_median"] > r["projected_median"]
    assert wc["uplift_median"] > 0
    assert wc["total_added"] == pytest.approx(24000.0 * r["years"], abs=0.01)


def test_the_base_projection_is_unchanged_by_asking_for_a_contribution():
    """The with/without pair only means anything if 'without' is the same run."""
    with TestClient(m.app) as c:
        _seed(c)
        a = c.get("/api/v1/plan/projection").json()
        b = c.get("/api/v1/plan/projection?contribution_per_year=24000").json()
    for k in ("projected_median", "projected_low", "projected_high",
              "probability_of_loss_real", "probability_meets_target"):
        assert a[k] == b[k], k


def test_the_rival_lever_is_on_screen_so_the_claim_can_be_checked():
    """Phase W's claim is that at this NAV the deposit moves terminal value more
    than any admissible excess does. A claim like that is either next to its
    rival or it is just a sentence."""
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection?contribution_per_year=24000").json()
    xc = r["excess_comparison"]
    assert xc["extra_return_pp"] == 1.0
    assert xc["uplift_median"] > 0
    assert xc["moves_more"] in ("contributions", "excess")
    # The verdict must follow the numbers, in either direction.
    cu = r["with_contributions"]["uplift_median"]
    assert xc["moves_more"] == ("contributions" if cu > xc["uplift_median"] else "excess")


def test_a_zero_contribution_override_is_not_a_projection_with_deposits():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/plan/projection?contribution_per_year=0").json()
    assert r["with_contributions"]["available"] is False


def test_the_route_states_its_excess_before_it_sees_the_numbers():
    """Same rule W1's bar follows: a comparison threshold chosen after the
    result is not a comparison."""
    from app.services import projection_service
    assert projection_service.EXCESS_PP == 1.0
    src = open(projection_service.__file__, encoding="utf-8").read()
    assert "EXCESS_PP = 1.0" in src


def test_the_frontend_says_the_headline_assumes_you_stop_depositing():
    from pathlib import Path
    src = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert "never add another shekel" in src
    assert "with_contributions" in src
