"""Global markets + portfolio-aware research-event annotation."""
from types import SimpleNamespace

from app.schemas.state_machine import Market, MARKET_CURRENCY, MARKET_REGION
from app.schemas.research import ResearchEvent
from app.services.market_impact import annotate
from app.services.portfolio_analytics import CUR, GEO


def _pos(ticker, market, qty=10, price=100.0, asset_class=None):
    return SimpleNamespace(ticker=ticker, market=market, quantity=qty,
                           current_price=price, meta={"asset_class": asset_class} if asset_class else {})


def test_major_markets_are_supported_with_currencies():
    for code in ("NASDAQ", "LSE", "XETRA", "JPX", "HKEX", "ASX", "TSX", "B3", "NSE", "SSE", "SIX", "EURONEXT"):
        assert code in Market.__members__ or code in [m.value for m in Market]
    assert MARKET_CURRENCY["LSE"] == "GBP"
    assert MARKET_CURRENCY["JPX"] == "JPY"
    assert MARKET_CURRENCY["TASE"] == "ILS"
    # analytics uses the same shared maps
    assert CUR is MARKET_CURRENCY and GEO is MARKET_REGION


def test_event_annotation_finds_affected_holdings_and_actions():
    ev = ResearchEvent(event_id="evt_1", event_type="REGULATORY_SURTAX_UPDATE",
                       relevance_score=95, affected_assets=["TASE:TA35"],
                       expected_time_horizon="MEDIUM", confidence=90)
    rows = [_pos("TA35", "TASE", qty=100, price=50.0), _pos("AAPL", "NASDAQ", qty=10, price=100.0)]
    out = annotate([ev], rows)[0]
    assert "TA35" in out["affected_holdings"]
    assert out["exposure_pct"] > 0
    assert out["actions"]                      # surtax => concrete tax actions
    assert "tax" in out["impact"].lower()


def test_fx_event_flags_foreign_currency_exposure():
    ev = ResearchEvent(event_id="evt_2", event_type="FX_MOVE", relevance_score=65,
                       affected_assets=["ILS:USD"], expected_time_horizon="MEDIUM", confidence=60)
    rows = [_pos("AAPL", "NASDAQ", qty=10, price=100.0),    # USD-denominated -> flagged
            _pos("BOND", "TASE", qty=10, price=100.0)]      # ILS-denominated -> not flagged
    out = annotate([ev], rows)[0]
    assert out["affected_holdings"] == ["AAPL"]
    assert out["actions"]


def test_event_with_no_overlap_is_informational():
    ev = ResearchEvent(event_id="evt_3", event_type="EARNINGS", relevance_score=70,
                       affected_assets=["TASE:TA35"], expected_time_horizon="SHORT", confidence=65)
    rows = [_pos("AAPL", "NASDAQ", qty=10, price=100.0)]
    out = annotate([ev], rows)[0]
    assert out["affected_holdings"] == []
    assert out["direction"] == "info"
    assert out["actions"] == []


def test_exposure_is_fx_normalized_to_base_currency(monkeypatch):
    """A USD holding's exposure must be reported in ILS, not raw USD.

    Regression: annotate() used to compute quantity x price with no FX rate while
    every other surface (NAV, allocation mix) is ILS-normalized -- so the panel
    claimed an event touched "100% of your portfolio (₪5,680)" for a book the
    app valued at ₪17,306.
    """
    monkeypatch.setattr("app.services.fx.fx_rate",
                        lambda ccy, base=None: 1.0 if (ccy or "").upper() == "ILS" else 3.5)
    ev = ResearchEvent(event_id="evt_fx", event_type="REGULATORY_SURTAX_UPDATE",
                       relevance_score=95, affected_assets=["NASDAQ:AAPL"],
                       expected_time_horizon="MEDIUM", confidence=90)
    rows = [_pos("AAPL", "NASDAQ", qty=10, price=100.0)]   # $1,000 -> ₪3,500
    out = annotate([ev], rows)[0]
    assert out["exposure_ils"] == 3500
    assert out["exposure_pct"] == 100


def test_mixed_currency_exposure_uses_normalized_denominator(monkeypatch):
    """Exposure % must divide an ILS numerator by an ILS NAV, not mix currencies."""
    monkeypatch.setattr("app.services.fx.fx_rate",
                        lambda ccy, base=None: 1.0 if (ccy or "").upper() == "ILS" else 3.5)
    ev = ResearchEvent(event_id="evt_mix", event_type="EARNINGS", relevance_score=70,
                       affected_assets=["NASDAQ:AAPL"], expected_time_horizon="SHORT", confidence=65)
    rows = [_pos("AAPL", "NASDAQ", qty=10, price=100.0),    # ₪3,500
            _pos("BOND", "TASE", qty=35, price=100.0)]      # ₪3,500
    out = annotate([ev], rows)[0]
    assert out["exposure_ils"] == 3500
    assert out["exposure_pct"] == 50      # would be ~22% unnormalized
