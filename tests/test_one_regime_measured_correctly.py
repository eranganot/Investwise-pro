"""PR 108: one market regime acts, and the futures cross-check is measured right.

C11 (re-proven live 2026-10-11): the futures "day change" used `chartPreviousClose`,
which with range=5d is the close BEFORE the window -- about four sessions ago.
ES=F showed -0.18% on a +0.56% day; NQ=F -1.19% on +0.45%.

C10 / D-a: Today's "Markets look risk-off" card read the futures regime, which
the code calls display-only, while the strategies gate on `engines/regime`.
On 2026-10-07 futures said risk-off and the proxy risk_on 0.95, and Today told
the owner to raise cash.
"""
import json
import time

from fastapi.testclient import TestClient

from app.main import app


def _chart(price, closes, chart_prev):
    return json.dumps({"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "chartPreviousClose": chart_prev, "currency": "USD"},
        "indicators": {"quote": [{"close": closes}]}}]}})


def test_the_day_change_is_against_the_previous_daily_bar(monkeypatch):
    """The live bars of 2026-10-06..09, as Yahoo returned them."""
    from app.services import markets_service as ms
    fixtures = {
        "ES=F": _chart(7859.75, [7874.0, 7852.75, 7816.25, 7859.75], 7874.0),
        "NQ=F": _chart(31108.75, [31483.25, 31402.25, 30969.5, 31108.75], 31483.25),
        "CL=F": _chart(91.85, [89.44, 88.28, 91.49, 91.85], 89.44),
        "^VIX": _chart(14.84, [15.52, 15.01, 15.08, 15.41, None, 14.84], 15.52),
    }
    monkeypatch.setattr(ms, "_http_text", lambda url, *a, **k: fixtures[url.split("/chart/")[1].split("?")[0]])
    got = {s: ms._fetch_one(s)["change_pct"] for s in fixtures}
    # On HEAD: ES -0.18, NQ -1.19 (sign flipped), CL +2.69, VIX -4.38.
    assert got == {"ES=F": 0.56, "NQ=F": 0.45, "CL=F": 0.39, "^VIX": -3.7}, got


def _set_proxy(state, **components):
    from app.services import regime_service as rs
    data = {"ok": True, "state": state, "score": -0.7 if state == "risk_off" else 0.95,
            "components": {"trend_up": state != "risk_off", "vol_pct": 30.0,
                           "vol_percentile": 91.0 if state == "risk_off" else 40.0,
                           "breadth": 0.0 if state == "risk_off" else 1.0, **components}}
    rs._CACHE.update(ts=time.time(), data=data)


def _set_futures(regime):
    from app.services import markets_service as ms
    ms._CACHE.update(ts=time.time(), data={"as_of": "now", "futures": [{"symbol": "^VIX"}],
                                           "market": {"regime": regime, "tone": "bad",
                                                      "rationale": "VIX 22.5, equity futures -0.40%"}})


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "VTI", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
         "spot_price": 100.0, "listing_price": 100.0, "quantity": 50, "cost_basis": 90.0},
        {"ticker": "BND", "market": "NASDAQ", "asset_class": "Fixed Income", "depth": 1,
         "spot_price": 100.0, "listing_price": 100.0, "quantity": 50, "cost_basis": 100.0}]})


def _riskoff_cards(body):
    """The risk-off message wherever Today put it: a card, or (no Accept, PR 102)
    a heads-up, possibly reconciled into "... phase the rebalance in"."""
    from tests._cards import all_cards
    return [x for x in all_cards(body) if str(x.get("title", "")).startswith("Markets look risk-off")]


def test_futures_risk_off_alone_does_not_tell_you_to_raise_cash():
    _set_futures("risk-off")
    _set_proxy("risk_on")
    with TestClient(app) as c:
        _seed(c)
        body = c.get("/api/v1/recommendations").json()
    # On HEAD: the futures card fires.
    assert _riskoff_cards(body) == [], [x["why"] for x in _riskoff_cards(body)]


def test_the_strategies_regime_risk_off_fires_the_card_and_says_why():
    _set_futures("risk-on")
    _set_proxy("risk_off")
    with TestClient(app) as c:
        _seed(c)
        body = c.get("/api/v1/recommendations").json()
    cards = _riskoff_cards(body)
    assert len(cards) == 1, [x.get("title") for x in body.get("recommendations", [])]
    text = " ".join(str(cards[0].get(k) or "") for k in ("why", "action"))
    # It names what the regime saw -- never an empty "()" (the reconciler read a
    # futures-only `rationale` field).
    assert "SPY below its 200-day trend" in text and "()" not in text, text
    assert "91st percentile" in text


def test_a_cold_or_old_regime_cache_says_nothing():
    from app.services import markets_service as ms
    from app.services import regime_service as rs
    rs._CACHE.update(ts=0.0, data=None)
    assert rs.cached_regime_proxy() == {}
    ms._CACHE.update(ts=time.time() - 10 * 3600, data={"futures": [{}], "market": {"regime": "risk-off"}})
    # On HEAD: no TTL -- a ten-hour-old futures reading is "today's".
    assert ms.cached_regime() == {}


def test_the_markets_route_serves_the_cached_proxy(monkeypatch):
    from app.services import markets_service as ms
    from app.services import regime_service as rs
    calls = []

    def stub():
        calls.append(1)
        return {"ok": True, "state": "risk_on", "score": 0.95, "components": {}}
    monkeypatch.setattr(rs, "regime_proxy", stub)
    monkeypatch.setattr(ms, "futures_snapshot", lambda *a, **k: {"futures": [], "market": {"regime": "neutral"}})
    rs._CACHE.update(ts=0.0, data=None)
    with TestClient(app) as c:
        a = c.get("/api/v1/markets/futures").json()
        b = c.get("/api/v1/markets/futures").json()
    assert a["regime_proxy"]["state"] == b["regime_proxy"]["state"] == "risk_on"
    assert len(calls) == 1, "the proxy is computed once an hour, not per request"
    assert a["market"]["role"] == "cross_check"
