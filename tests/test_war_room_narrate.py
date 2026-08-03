"""The Today path must not pay for war-room prose.

Measured on /api/v1/recommendations via the new timings_ms:

    holding_verdicts 4ms | hedge 0 | momentum 0 | income_cost 0
    war_room 6726ms | reconcile_and_filter 2 | backtest 0 | buy_ideas 0

war_room owned effectively the entire endpoint, and was SLOWER warm than cold --
the tell that it isn't cacheable provider data. It is adversary.narrate(): one
live Gemini call per signal, synchronous, on the event loop of a single-worker
server. It got slower once Gemini billing was topped up, because the calls had
previously failed fast with 429.

The narrative is never rendered on a Today card (those use outcome_label, impact
and confidence), so the recommendations path asks for narrate=False.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import inspect  # noqa: E402

from app.services.war_room import build_war_room  # noqa: E402


def test_build_war_room_can_skip_the_llm_narrative():
    sig = inspect.signature(build_war_room)
    assert "narrate" in sig.parameters
    assert sig.parameters["narrate"].default is True, "the war-room view keeps its prose"


def test_recommendations_asks_for_no_narrative():
    """The saving only lands if the Today path actually opts out."""
    src = inspect.getsource(
        __import__("app.services.recommendations", fromlist=["_war_room_recs"])._war_room_recs)
    assert "narrate=False" in src, \
        "the recommendations path must skip the per-signal Gemini call"


def test_war_room_view_still_narrates_by_default():
    from app.api.routes.war_room import _war_room_payload
    sig = inspect.signature(_war_room_payload)
    assert sig.parameters["narrate"].default is True


def test_narrate_false_makes_no_llm_call(monkeypatch):
    """Belt and braces: with narrate off, nothing reaches the LLM layer."""
    from app.services import llm

    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        return None, "should not be called"

    monkeypatch.setattr(llm, "gemini_generate_ex", _boom)
    build_war_room([], portfolio_tickers=set(), narrate=False)
    assert called["n"] == 0
