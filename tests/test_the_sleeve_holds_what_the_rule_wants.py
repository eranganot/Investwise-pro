"""PR 63. Eran, 2026-09-06: *"the swing should be a card that replace the TQQQ
with QQQ automatically"* — and, on the design: *"I agree (a) is right."*

**A sleeve's target was the catalogue's static weights, on every day of the
year.** `sleeve_targets` read `weights` minus `base` and stopped there, so
`btm_swing_dip` targeted TQQQ whether or not its rule was in a setup — including
the ~85% of the time it is not. The catalogue's own docstring says what that
means: *"``base`` is the core holding, not cash. A swing setup is live perhaps
10-15% of the time."* So for most of the year the Plan tab, every drift card, the
funding engine and the armed caps described a position the rule was not asking
for, and nothing in the app could tell "the rule wants TQQQ" apart from "the rule
has never been consulted".

**Option (a), which Eran chose.** When the rule is off, the sleeve targets its
BASE. The alternative — target nothing and let the money fall back to the core —
is cheaper and wrong for this book: his core is Classic 60/40, so it would sell
TQQQ into VTI and BND rather than into QQQ.

The docstring this overturns said funding the base means *"buying an index fund
you never asked for"*. That is true of the catalogue default and false here: you
asked for it by choosing a strategy whose spec parks the money in the base
between setups.

**One read, passed down.** `sleeve_service.live_sleeve_targets` loads the stored
signal once per request and hands it to the pure `sleeve_targets`, rather than
making that function async and dragging a session through `card_scope.scope_for`
— which is documented as pure *"so the mapping is testable without a book"*.

**And no staleness gate**, which is the one judgement call in here. The obvious
guard — ignore an old target, fall back to the catalogue — fails in the dangerous
direction, because the catalogue default is always the LEVERAGED leg. A dead
evaluator would turn "the rule says hold QQQ" back into "the sleeve wants TQQQ"
and the app would recommend its way into a 3x fund on no signal at all. The age
travels with the answer instead.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services.strategy_service import sleeve_targets  # noqa: E402

SWING = "btm_swing_dip"      # weights {TQQQ}, base {QQQ}
SOXL = "btm_trend_soxl"      # weights {SOXL}, base {SMH}


def _pos(ticker, ils, gain=0.2, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0,
            "cost_basis": px / (1.0 + gain),
            "expected_return_pct": 8, "volatility_pct": 20}


async def _store_signal(own_session, sid, target, as_of="2026-09-05"):
    from app.models.tables import StrategySignalState
    async with own_session() as s:
        s.add(StrategySignalState(subject="eran.ganot@gmail.com", strategy_id=sid,
                                  target=target, as_of=as_of))
        await s.commit()


# --------------------------------------------------------------------------- #
# 1. the primitive
# --------------------------------------------------------------------------- #
def test_without_a_signal_it_is_the_catalogue_exactly_as_before():
    """Nothing that has not opted in changes."""
    assert sleeve_targets(SWING, 20) == {"TQQQ": pytest.approx(0.20)}
    assert sleeve_targets(SWING, 20, live=None) == {"TQQQ": pytest.approx(0.20)}
    assert sleeve_targets(SWING, 20, live={}) == {"TQQQ": pytest.approx(0.20)}


def test_a_live_signal_replaces_the_leg_including_when_it_is_the_base():
    """The whole point. `base` is not "an index fund you never asked for" here.

    The catalogue filters the base OUT of a sleeve target, deliberately. A stored
    signal saying "hold the base" has to survive that filter or the rule cannot
    be followed at all.
    """
    assert sleeve_targets(SWING, 20, live={"QQQ": 1.0}) == {"QQQ": pytest.approx(0.20)}
    assert sleeve_targets(SWING, 20, live={"TQQQ": 1.0}) == {"TQQQ": pytest.approx(0.20)}
    # A multi-leg signal is normalised to the sleeve size, not to 1.0.
    got = sleeve_targets(SWING, 30, live={"QQQ": 0.5, "VTI": 0.5})
    assert got == {"QQQ": pytest.approx(0.15), "VTI": pytest.approx(0.15)}


def test_zero_weights_in_a_signal_are_not_positions():
    """`evaluate` already drops weights below 0.005; belt and braces."""
    assert sleeve_targets(SWING, 20, live={"QQQ": 1.0, "TQQQ": 0.0}) == {
        "QQQ": pytest.approx(0.20)}
    # An all-zero signal is not a signal: fall back rather than target nothing.
    assert sleeve_targets(SWING, 20, live={"QQQ": 0.0}) == {"TQQQ": pytest.approx(0.20)}


def test_the_sleeve_size_still_comes_from_the_slider():
    """The signal says WHAT to hold. It never says how much."""
    for pct in (5, 20, 64):
        got = sleeve_targets(SWING, pct, live={"QQQ": 1.0})
        assert got["QQQ"] == pytest.approx(pct / 100.0)


# --------------------------------------------------------------------------- #
# 2. end to end, on a book
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_the_plan_tab_reports_the_leg_the_rule_wants(own_session):
    """`sleeve_occupancy` is what the Sleeves card renders."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", 7476.0), _pos("VTI", 4200.0),
            _pos("BND", 2175.0, cls="Fixed Income")]})
        c.post(f"/api/v1/strategies/{SWING}/apply?sleeve_pct=33")

        before = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]
        assert before["tickers"] == ["TQQQ"]
        assert before["following_signal"] is False
        assert before["signal_as_of"] is None

    await _store_signal(own_session, SWING, {"QQQ": 1.0})

    with TestClient(m.app) as c:
        after = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]

    assert after["tickers"] == ["QQQ"], (
        "the sleeve still wants the leveraged leg while its rule says otherwise")
    assert after["following_signal"] is True
    assert after["signal_as_of"] == "2026-09-05"
    # The SIZE did not move. Only the instrument did.
    assert after["sleeve_pct"] == pytest.approx(before["sleeve_pct"])


@pytest.mark.anyio
async def test_the_held_share_is_measured_against_the_new_leg(own_session):
    """TQQQ stops counting toward a sleeve that no longer wants it.

    This is what makes the follow-on card possible: once the sleeve targets QQQ
    and holds none of it, TQQQ is a position no sleeve claims, and PR 61's
    `steps_back_to` rule makes selling it to buy QQQ actionable in one tap.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", 7476.0), _pos("VTI", 4200.0),
            _pos("BND", 2175.0, cls="Fixed Income")]})
        c.post(f"/api/v1/strategies/{SWING}/apply?sleeve_pct=33")
        held_before = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]["held_ils"]
        assert held_before > 7000

    await _store_signal(own_session, SWING, {"QQQ": 1.0})

    with TestClient(m.app) as c:
        s = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]
    assert s["held_ils"] == 0.0, "TQQQ is still counted toward a QQQ sleeve"
    assert s["gap_pct"] > 30, s


@pytest.mark.anyio
async def test_the_funder_buys_what_the_rule_wants(own_session):
    """One rule, every consumer. The card and the button size the same leg."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", 7476.0), _pos("VTI", 4200.0),
            _pos("BND", 2175.0, cls="Fixed Income")]})
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High"})
        c.post(f"/api/v1/strategies/{SWING}/apply?sleeve_pct=33")

    await _store_signal(own_session, SWING, {"QQQ": 1.0})

    with TestClient(m.app) as c:
        prev = c.post(f"/api/v1/strategies/{SWING}/load-basket",
                      json={"mode": "fund", "dry_run": True}).json()
        assert prev.get("ok"), prev
        assert [b["ticker"] for b in prev["buys"]] == ["QQQ"], prev["buys"]
        # And it funds itself out of the instrument it is stepping back from.
        sells = [s["ticker"] for s in prev["funding"]["sells"]]
        assert sells == ["TQQQ"], sells


@pytest.mark.anyio
async def test_a_signal_for_another_sleeve_does_not_leak(own_session):
    """Two sleeves, one signal. The other keeps its catalogue leg."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", 7000.0), _pos("SOXL", 7000.0), _pos("VTI", 4000.0)]})
        c.post(f"/api/v1/strategies/{SWING}/apply?sleeve_pct=30")
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=30")

    await _store_signal(own_session, SWING, {"QQQ": 1.0})

    with TestClient(m.app) as c:
        by_id = {s["strategy_id"]: s
                 for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
    assert by_id[SWING]["tickers"] == ["QQQ"]
    assert by_id[SOXL]["tickers"] == ["SOXL"], "a signal leaked across sleeves"
    assert by_id[SOXL]["following_signal"] is False


# --------------------------------------------------------------------------- #
# 3. the judgement calls, asserted so they cannot be quietly reversed
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_a_stale_signal_is_kept_rather_than_reverted(own_session):
    """Reverting to the catalogue is the DANGEROUS direction.

    The catalogue default is always the leveraged leg, so a staleness gate would
    turn "the rule says hold QQQ" back into "the sleeve wants TQQQ" the moment
    the evaluator stopped running — recommending a book into a 3x fund on the
    strength of no signal at all. The age is reported instead.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal",
                                                 "positions": [_pos("VTI", 10000.0)]})
        c.post(f"/api/v1/strategies/{SWING}/apply?sleeve_pct=20")

    await _store_signal(own_session, SWING, {"QQQ": 1.0}, as_of="2026-01-02")

    with TestClient(m.app) as c:
        s = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]
    assert s["tickers"] == ["QQQ"], "a stale signal was reverted to the leveraged leg"
    assert s["signal_as_of"] == "2026-01-02", "the age is not reported"


def test_retiring_a_sleeve_looks_for_caps_under_both_legs():
    """A flipped sleeve has armed a cap on the aggressive leg AND on the base.

    Retiring it with only today's leg in hand leaves the other armed with no
    owner — the exact failure PR 59.1 exists to prevent.
    """
    import inspect

    from app.services import strategy_service

    src = inspect.getsource(strategy_service.retire_strategy_rules)
    assert "live=_sig" in src, "retirement reads one leg again"
    assert src.count("sleeve_targets(strategy_id") == 2, (
        "the union of both legs is not being taken")
