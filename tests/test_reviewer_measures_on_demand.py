"""PR 77, Eran 2026-09-29 (option a): a holding the nightly run has not measured
is measured in the background from the Today build, once per ticker at a time,
and not re-tried on every page load."""
import asyncio

import pytest

from app.services import holding_review as hr
from app.services import recommendations as rr
from tests._review_fixtures import down_both, series, up
from tests.test_reviewer_card_claims import _patch_history, _session, _two_account_book


@pytest.fixture(autouse=True)
def _clean_state():
    hr._inflight.clear()
    hr._tried.clear()
    yield
    hr._inflight.clear()
    hr._tried.clear()


def _read(tk):
    async def go():
        eng, S = _session()
        try:
            async with S() as s:
                return (await hr.stored(s, [tk])).get(tk)
        finally:
            await eng.dispose()
    return asyncio.run(go())


def test_off_where_the_scheduler_is_off():
    """Under pytest ENABLE_SCHEDULER is false: no second writer is started."""
    assert hr.kick_background(["ANY"]) == []
    assert hr._inflight == set()


def test_a_kick_measures_and_stores_and_is_not_repeated(monkeypatch):
    from app.services import backtest_service
    monkeypatch.setattr(backtest_service, "_fetch",
                        lambda tks, classify=False, basis=None: ({t: series(t, up(700))[t] for t in tks}, []))
    assert hr.kick_background(["rvod"], force=True) == ["RVOD"]
    hr.kick_background.last_thread.join(timeout=60)
    got = _read("RVOD")
    assert got and hr.fresh(got)[0] is True, "measured and stored, fit to make a card actionable"
    assert hr._inflight == set()
    # Within RETRY_AFTER_S the same ticker is not fetched again, whatever the outcome.
    assert hr.kick_background(["RVOD"], force=True) == []


def test_a_failed_fetch_leaves_nothing_in_flight(monkeypatch):
    from app.services import backtest_service

    def boom(tks, classify=False, basis=None):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(backtest_service, "_fetch", boom)
    assert hr.kick_background(["RVBAD"], force=True) == ["RVBAD"]
    hr.kick_background.last_thread.join(timeout=60)
    assert hr._inflight == set() and _read("RVBAD") is None


@pytest.mark.asyncio
async def test_the_today_build_hands_unmeasured_holdings_over(monkeypatch):
    tk = "RVU"
    _patch_history(monkeypatch, tk, down_both())
    asked: list[list[str]] = []

    def fake_kick(tickers, *, force=False):
        asked.append(list(tickers))
        return [t for t in tickers if t == tk]
    monkeypatch.setattr(hr, "kick_background", fake_kick)
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_account_book(s, "review_on_demand@example.com", tk)
            built = await rr.build_recommendations(s, user)
    finally:
        await eng.dispose()
    assert asked and tk in asked[0]
    row = next(r for r in built["holding_review"] if r["ticker"] == tk)
    assert row["measuring"] is True and "Measuring now" in row["measurement_note"]
    # A3r (D132): the unmeasured review card offered a stop-loss; exits are the protection
    # manager's now, so no review card arms one -- and none sells unmeasured.
    cards = [c for c in built["recommendations"] + built["more_recommendations"]
             if c.get("dimension") == "holding_review" and (c.get("meta") or {}).get("ticker") == tk]
    assert all((c.get("apply") or {}).get("kind") not in ("trim", "sell_position", "create_rules") for c in cards)
