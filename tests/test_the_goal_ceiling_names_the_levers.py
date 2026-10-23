"""PR 138 -- the "nothing to accept" goal note names what would close the gap.

Live 2026-10-22: goal chance 6.8% against a 70% bar. The only lever card (a reachable
target) had been ignored, and the note said just "Nothing to accept here", so nothing
on Today showed the owner that a target, a deposit or the halving limit was the way out.
"""
from types import SimpleNamespace

import pytest

from app.services import book_optimizer as bo
from app.services import frontier_service as fs

R = {"on_track_pct": 70.0}
LIVE = {"goal": {"roi_pct": 30, "deadline": "2031-12-31"},
        "levers": {"roi_at_confidence_pct": 6.4, "amount_at_confidence": 27944,
                   "extra_monthly_ils": None, "deadline_extra_years": None},
        "tail": {"before": {"extra_monthly_needed": 606.45}}}


def _with(monkeypatch, detail):
    async def _latest(session, user):
        return None if detail is None else SimpleNamespace(detail=detail)
    monkeypatch.setattr(fs, "latest", _latest)


@pytest.mark.asyncio
async def test_the_live_shape_names_the_target_and_says_why_no_deposit_is_offered(monkeypatch):
    _with(monkeypatch, LIVE)
    t = await bo._lever_sentence(None, None, R)
    assert "from 30%/yr to 6.4%/yr" in t and "27,944" in t and "2031" in t
    assert "no deposit and no later deadline" in t
    assert "606" in t and "ignores the rules" in t


@pytest.mark.asyncio
async def test_a_deposit_lever_is_named_and_the_no_lever_sentence_is_dropped(monkeypatch):
    d = {**LIVE, "levers": {**LIVE["levers"], "extra_monthly_ils": 720}}
    _with(monkeypatch, d)
    t = await bo._lever_sentence(None, None, R)
    assert "add about 720 a month" in t and "no deposit" not in t


@pytest.mark.asyncio
async def test_no_stored_solve_adds_nothing_and_never_raises(monkeypatch):
    _with(monkeypatch, None)
    assert await bo._lever_sentence(None, None, R) == ""

    async def _boom(session, user):
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "latest", _boom)
    assert await bo._lever_sentence(None, None, R) == ""



@pytest.mark.asyncio
async def test_the_note_does_not_call_the_halving_limit_the_only_lever_when_it_names_another(monkeypatch):
    """PR 138c, seen live 2026-10-23 on eac1df4: "Changing that limit is the one lever
    left" and, a sentence later, "What would close the gap: lower your target return"."""
    from app.services import sleeve_service as sv
    _with(monkeypatch, LIVE)
    res = {"ok": True, "held": {}, "steps": [], "candidates_tried": 12, "on_track_pct": 65.0,
           "halve_limit_pct": 30.0, "after": {"p_goal_pct": 0.3},
           "before": {"p_goal_pct": 0.2, "p_goal_without_rules_pct": 21.3, "p_halve_without_rules_pct": 57.2}}

    async def _load(session, user):
        return res

    async def _none(session, user):
        return []
    monkeypatch.setattr(bo, "load", _load)
    monkeypatch.setattr(sv, "list_sleeves", _none)
    [c] = await bo.card(None, None)
    assert "lower your target return" in c["why"]
    assert "one lever left" not in c["why"] and "Changing that limit is one lever" in c["why"]
    _with(monkeypatch, None)                                   # no levers measured: the old sentence stands
    [c] = await bo.card(None, None)
    assert "Changing that limit is the one lever left" in c["why"]
