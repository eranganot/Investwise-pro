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
