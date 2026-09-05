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


def test_research_agent_emits_evidence_contract(monkeypatch):
    """The contract still matters; the fixture that used to supply it does not.

    This used to run against four hardcoded events and assert the top one was
    REGULATORY_SURTAX_UPDATE -- which pinned the shape of the payload to the
    content of a fiction. PR 58 removed the fiction, so the events are supplied
    here instead. The agent's contract is the thing under test: field set,
    bounded scores, ordering by relevance. It has to keep holding for the day a
    real provider is wired in, which is exactly when nobody will be reading
    this file.
    """
    from app.providers.base import EconomicEvent

    monkeypatch.setattr(
        "app.agents.research_agent.guarded_events",
        lambda: [
            EconomicEvent(event_type="MACRO_CPI_PRINT", description="stub",
                          affected_assets=["NYSE:SPY"], horizon="SHORT", severity=42),
            EconomicEvent(event_type="REGULATORY_SURTAX_UPDATE", description="stub",
                          affected_assets=["TASE:TA35"], horizon="MEDIUM", severity=91),
        ])

    events = ResearchAgent().scan()
    assert len(events) == 2
    top = events[0]
    d = top.model_dump()
    assert set(d) == {"event_id", "event_type", "relevance_score",
                      "affected_assets", "expected_time_horizon", "confidence"}
    assert 0 <= top.relevance_score <= 100 and 0 <= top.confidence <= 100
    # Ordered by relevance, so the 91 comes first regardless of input order.
    assert top.event_type == "REGULATORY_SURTAX_UPDATE"


def test_guarded_quote_returns_and_caches():
    q1 = guarded_quote("AAPL")
    q2 = guarded_quote("AAPL")
    assert q1.price == q2.price
