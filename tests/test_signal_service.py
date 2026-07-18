"""Signals must come from observed prices, never from sample data.

The war room used to run the agent pipeline over demo_data.DEFAULT_OBSERVATIONS
(TEVA at a made-up spot of 100 vs a made-up listing of 108.2) and present the
verdict as a real "Approved: Buy TEVA" decision -- while the Today view, running
a completely separate pipeline, never mentioned it.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
import math

from app.services import signal_service as ss


def _series(start, n, drift=0.0, wobble=0.0):
    return [start * (1 + drift) ** i + (wobble if i % 2 else -wobble) for i in range(n)]


def test_too_little_history_yields_no_signal():
    assert ss.observation_for("AAA", "NYSE", _series(100, 10)) is None
    assert ss.observation_for("AAA", "NYSE", []) is None


def test_listing_price_is_the_trend_not_an_invented_target():
    closes = _series(100, 80)
    obs = ss.observation_for("AAA", "NYSE", closes)
    assert obs is not None
    assert obs.spot_price == round(closes[-1], 4)
    expected_ma = sum(closes[-50:]) / 50
    assert abs(obs.listing_price - expected_ma) < 0.01     # measured, not assumed


def test_price_below_trend_is_a_buy_candidate():
    closes = _series(100, 60) + [80.0]                     # sharp drop below trend
    obs = ss.observation_for("AAA", "NYSE", closes)
    assert obs.action_type.value.upper() == "BUY"
    assert obs.expected_return_pct > 0                     # reverting to trend implies upside


def test_price_above_trend_is_a_rebalance_candidate():
    closes = _series(100, 60) + [130.0]
    obs = ss.observation_for("AAA", "NYSE", closes)
    assert obs.action_type.value.upper() == "REBALANCE"
    assert obs.expected_return_pct < 0


def test_persistent_divergence_scores_as_backbone_depth():
    steady_climb = [100 + i for i in range(80)]            # every recent close above its MA
    assert ss.observation_for("AAA", "NYSE", steady_climb).depth == 3


def test_choppy_price_scores_as_surface_hype():
    chop = [100 + (5 if i % 2 else -5) for i in range(80)]  # oscillates around the MA
    assert ss.observation_for("AAA", "NYSE", chop).depth == 1


def test_realized_volatility_is_annualized_from_returns():
    flat = [100.0] * 80
    assert ss.realized_vol_pct(flat) == 0.0
    volatile = [100 * (1.05 if i % 2 else 0.95) ** 1 for i in range(80)]
    assert ss.realized_vol_pct(volatile) > ss.realized_vol_pct(flat)


def test_provider_shapes_both_normalize():
    yahoo = _series(100, 60)
    fmp = [(f"2026-01-{i:02d}", v) for i, v in enumerate(yahoo, start=1)]
    assert ss._closes(yahoo) == ss._closes(fmp)


def test_candidate_set_puts_holdings_first_and_skips_cash():
    class P:
        def __init__(s, t, m="TASE"): s.ticker, s.market = t, m
    cands = ss.candidate_set([P("AAA"), P("CASH"), P("BBB")], extra=3)
    tickers = [c["ticker"] for c in cands]
    assert tickers[:2] == ["AAA", "BBB"]
    assert "CASH" not in tickers
    assert len(tickers) == len(set(tickers))               # no duplicates


def test_build_observations_skips_unusable_tickers(monkeypatch):
    def fake_history(ticker, days=252):
        if ticker == "BAD":
            raise RuntimeError("provider down")
        if ticker == "SHORT":
            return [100.0, 101.0]
        return _series(100, 80)
    monkeypatch.setattr("app.providers.registry.guarded_history", fake_history)
    out = ss.build_observations([{"ticker": "GOOD", "market": "NYSE"},
                                 {"ticker": "BAD", "market": "NYSE"},
                                 {"ticker": "SHORT", "market": "NYSE"}])
    assert [o.ticker for o in out] == ["GOOD"]             # no placeholder filling


def test_war_room_reports_whether_its_signals_are_grounded():
    """The payload must declare its provenance so Today can refuse sample data."""
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 100},
        ]})
        w = c.get("/api/v1/war-room").json()
        assert "grounded" in w and "signal_basis" in w
        if not w["grounded"]:
            assert "SAMPLE" in w["signal_basis"].upper()


def test_ungrounded_signals_never_become_today_cards(monkeypatch):
    """Sample prices must not be laundered into advice via the Today view."""
    import asyncio
    from app.services import recommendations as rr

    async def fake_payload(session, user, rows=None):
        return {"grounded": False, "sessions": [
            {"ticker": "TEVA", "outcome": "DISPLAYED", "outcome_label": "Growth",
             "title": "Buy TEVA (NYSE)", "transcript": []}]}

    monkeypatch.setattr("app.api.routes.war_room._war_room_payload", fake_payload)
    out = asyncio.get_event_loop().run_until_complete(rr._war_room_recs(None, None, []))
    assert out == []


def test_grounded_approved_signals_do_become_today_cards(monkeypatch):
    import asyncio
    from app.services import recommendations as rr

    async def fake_payload(session, user, rows=None):
        return {"grounded": True, "sessions": [
            {"ticker": "MSFT", "outcome": "DISPLAYED", "outcome_label": "Growth",
             "title": "Buy MSFT (NASDAQ)",
             "transcript": [{"agent": "Alpha", "says": "Depth 3 divergence +8.2%"},
                            {"agent": "Decision", "says": "", "detail": {"impact": 61, "confidence": 80}}]},
            {"ticker": "NOISE", "outcome": "NO_ACTION", "outcome_label": "none", "transcript": []}]}

    monkeypatch.setattr("app.api.routes.war_room._war_room_payload", fake_payload)
    out = asyncio.get_event_loop().run_until_complete(rr._war_room_recs(None, None, []))
    assert len(out) == 1                                   # only the approved one
    card = out[0]
    assert card["meta"]["source"] == "war_room"
    assert card["meta"]["ticker"] == "MSFT"
    assert card["actionable"] is False if "actionable" in card else True
    assert "61" in card["impact"]
