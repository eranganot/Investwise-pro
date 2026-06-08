"""Market provider + Research Agent tests (Sections AE, AA)."""
from app.agents.research_agent import ResearchAgent
from app.providers.builtin import BuiltinFXProvider, BuiltinMarketDataProvider
from app.providers.registry import guarded_quote
from app.schemas.market import Quote


def test_builtin_quote_is_deterministic():
    p = BuiltinMarketDataProvider()
    a, b = p.get_quote("TEVA"), p.get_quote("TEVA")
    assert isinstance(a, Quote)
    assert a.price == b.price and a.price > 0


def test_builtin_fx_usd_ils():
    r = BuiltinFXProvider().get_rate("USD", "ILS")
    assert r.base == "USD" and r.quote == "ILS" and r.rate > 1


def test_research_agent_emits_evidence_contract():
    events = ResearchAgent().scan()
    assert events  # non-empty
    top = events[0]
    d = top.model_dump()
    assert set(d) == {"event_id", "event_type", "relevance_score",
                      "affected_assets", "expected_time_horizon", "confidence"}
    assert 0 <= top.relevance_score <= 100 and 0 <= top.confidence <= 100
    # sorted by relevance (surtax update is highest severity)
    assert top.event_type == "REGULATORY_SURTAX_UPDATE"


def test_guarded_quote_returns_and_caches():
    q1 = guarded_quote("AAPL")
    q2 = guarded_quote("AAPL")
    assert q1.price == q2.price
