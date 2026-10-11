"""A map nobody checks is a map that quietly stops being true.

Eran, 2026-08-30: *"map all the agents in the tool and for each one write its
goals, objectives, and purpose."* `docs/AGENTS.md` is that map. This file is the
reason it stays one.

Writing the map surfaced the thing it was written to surface: `war_room.py:18`
declares SEVEN agents, five classes exist under `app/agents/`, and the overlap
between the two sets is TWO. Nothing in the suite noticed, because nothing had
ever compared the label to the code.

These tests are deliberately shaped so they fail on the change that would break
the document, not on cosmetics:

  * add or delete an agent class            -> ROSTER fails
  * change the war-room speaker list        -> WAR_ROOM_SPEAKERS fails
  * move an agent's veto power              -> the veto tests fail
  * teach the screener to read strategy     -> the strategy test fails LOUDLY,
                                               which is the good direction and
                                               means the doc needs updating

Every constant below is a claim `docs/AGENTS.md` makes in prose. If you are
here because one of them failed, the fix is to change BOTH.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "docs" / "AGENTS.md"

# The five classes under app/agents/ that decide something.
ROSTER = {
    "ResearchAgent": "app.agents.research_agent",
    "OpportunityAgent": "app.agents.screener_agent",
    "FeeAgent": "app.agents.fee_agent",
    "AllocationAgent": "app.agents.allocation_agent",
    "Adversary": "app.agents.adversary",
}

# What the user is shown under "How the decisions are made".
WAR_ROOM_SPEAKERS = ["Research", "Alpha", "Risk", "Tax", "Decision", "Adversary", "UX"]

# Of the seven names above, the ones backed by a class under app/agents/.
SPEAKERS_THAT_ARE_REALLY_AGENTS = {"Research", "Adversary"}


def test_the_map_exists():
    assert DOC.is_file(), (
        "docs/AGENTS.md is gone. Every test below reads it, and the agents it "
        "describes are the ones that decide what lands in your feed.")


def test_every_agent_class_on_disk_is_in_the_roster_and_in_the_map():
    """A new agent must not be able to arrive unannounced.

    Discovered by walking app/agents/, not by reading a list -- a list checked
    against itself proves nothing.
    """
    import importlib

    agents_dir = Path(__file__).resolve().parents[1] / "app" / "agents"
    found: set[str] = set()
    for path in sorted(agents_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        mod = importlib.import_module(f"app.agents.{path.stem}")
        for name, obj in vars(mod).items():
            if (inspect.isclass(obj) and obj.__module__ == mod.__name__
                    and not name.startswith("_")
                    and not issubclass(obj, Exception)
                    and not name.endswith(("Note", "Report", "Event", "Pick"))
                    and name != "Severity"):
                found.add(name)

    assert found == set(ROSTER), (
        f"the agent classes on disk are {sorted(found)}, the roster says "
        f"{sorted(ROSTER)}. Update BOTH this list and docs/AGENTS.md.")

    text = DOC.read_text(encoding="utf-8")
    missing = [f"{mod.replace('.', '/')}.py" for mod in ROSTER.values()
               if mod.replace(".", "/") + ".py" not in text]
    assert not missing, f"docs/AGENTS.md never cites {missing}"


def test_the_war_room_speaker_list_has_not_moved():
    from app.services.war_room import AGENTS

    assert AGENTS == WAR_ROOM_SPEAKERS, (
        "war_room.AGENTS changed. That list is rendered to the user as the "
        "cast of specialists weighing in on their money, so docs/AGENTS.md has "
        "to change with it.")


def test_the_gap_between_what_is_shown_and_what_exists_is_recorded():
    """THE FINDING THIS FILE EXISTS FOR.

    Seven names are shown; five agents exist; two are in both sets. The other
    five speakers are ENGINES -- deterministic calculators -- wearing an agent's
    name in the transcript.

    This is not asserted as desirable. It is asserted as CURRENT, so that fixing
    it (renaming the four engine speakers, per the recommendation in the doc)
    fails here and forces the map to be updated in the same commit.
    """
    from app.services.war_room import AGENTS

    real = {n for n in AGENTS if f"{n}Agent" in ROSTER or n in ROSTER}
    assert real == SPEAKERS_THAT_ARE_REALLY_AGENTS, (
        f"the war-room names backed by an agent class are now {sorted(real)}, "
        f"not {sorted(SPEAKERS_THAT_ARE_REALLY_AGENTS)}. If you renamed the "
        "engine speakers, say so in docs/AGENTS.md and update this test.")

    engines = [n for n in AGENTS if n not in real]
    assert engines == ["Alpha", "Risk", "Tax", "Decision", "UX"]


# --------------------------------------------------------------------------
# Veto power. WHO can stop a card is the single most consequential fact in the
# map, so it is checked rather than described.
# --------------------------------------------------------------------------

def test_the_allocation_agent_still_holds_the_only_pre_card_veto():
    from app.agents.allocation_agent import AllocationAgent, VetoException
    from app.engines.allocation_engine import AllocationEngine

    src = inspect.getsource(AllocationAgent.review_buy)
    assert "raise VetoException" in src, (
        "review_buy no longer raises. It is the only veto in the codebase that "
        "fires BEFORE a card is built rather than annotating one afterwards.")
    assert issubclass(VetoException, Exception)
    assert hasattr(AllocationEngine, "overweight_classes")


def test_the_allocation_veto_is_still_unreachable_from_the_app():
    """Recorded as a DEFECT, pinned so the fix is visible when it lands.

    The veto's only caller is PipelineOrchestrator.generate, reached solely by
    POST /api/v1/decision-feed/generate -- an ANALYST-role endpoint the front
    end never calls. The cards the user actually sees come from
    recommendations.py, which does not import AllocationAgent at all.

    When someone wires the veto into the live path, this test fails. That is the
    point: it is the notification that item 2's biggest hole has been closed.
    """
    root = Path(__file__).resolve().parents[1]

    recs = (root / "app" / "services" / "recommendations.py").read_text(encoding="utf-8")
    assert "AllocationAgent" not in recs, (
        "recommendations.py now knows about the Allocation Agent -- the live "
        "card path may finally be honouring the overweight veto. Verify, then "
        "update docs/AGENTS.md, which currently states it never runs.")

    ui = (root / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    assert "decision-feed" not in ui, (
        "the front end now calls /decision-feed. docs/AGENTS.md says it never "
        "does, and that claim is load-bearing for the whole Allocation Agent "
        "entry.")


def test_the_adversary_veto_is_wired_into_the_path_that_builds_cards():
    """It was prose for months and nothing read it. Keep it wired."""
    from app.services import war_room

    src = inspect.getsource(war_room.build_war_room)
    assert src.count("blocks_now") >= 3, (
        "the Adversary's BLOCK is no longer consulted at all three stages "
        "(detected/vetted, optimized, ranked)")
    assert 'outcome": "VETOED"' in src


def test_the_research_agent_is_still_read_only():
    """Its docstring promises this. A promise in a docstring is not a check."""
    from app.agents.research_agent import ResearchAgent

    src = inspect.getsource(ResearchAgent)
    for forbidden in ("session", "commit", "add(", "Recommendation", "apply"):
        assert forbidden not in src, (
            f"ResearchAgent mentions {forbidden!r}. Its stated invariant is that "
            "it emits evidence and never touches portfolio state.")


# --------------------------------------------------------------------------
# Item 2: are the agents aligned with the strategy the user picked?
# Measured, not assumed.
# --------------------------------------------------------------------------

def test_the_objective_reaches_the_screener_but_still_changes_nothing():
    """THE DEFECT PIN THAT FIRED, AND WHY IT IS STILL A DEFECT PIN.

    Until 2026-08-30 this asserted the BUG: `screen_equities` called with no
    weights anywhere in the app, so Grow and Preserve ranked the identical
    candidates in the identical order. It was written to fail the day someone
    fixed that.

    It fired -- and the fix turned out to be half a fix. The WIRING is now real:
    the objective travels from the plan to the screener at both call sites. The
    TILT is not, because nothing in this codebase derives one and inventing four
    vectors would be the `strategy_profile.assumptions_for()` failure again.

    So item 2 is STILL OPEN, and this test says so precisely rather than
    reporting a fix that has not happened. The two halves are pinned separately
    so that adding a real tilt fails HERE and forces docs/AGENTS.md to be
    updated with where the numbers came from.
    """
    root = Path(__file__).resolve().parents[1]
    recs = (root / "app" / "services" / "recommendations.py").read_text(encoding="utf-8")

    # -- the half that IS fixed ------------------------------------------
    calls = re.findall(r"screen_equities\((.*?)\)", recs, flags=re.S)
    assert calls, "recommendations.py no longer calls screen_equities at all"
    assert any("weights" in c for c in calls), (
        f"recommendations.py stopped passing weights to the screener: {calls}. "
        "That is item 2's original bug returning -- the objective would once "
        "again be unable to influence the ranking at all.")
    assert "objective_weights" in recs, (
        "weights are passed but not sourced from the plan's objective")

    # -- the half that IS NOT --------------------------------------------
    from app.services import objective_weights as ow

    assert ow.OBJECTIVE_WEIGHTS == {}, (
        f"a factor tilt now exists: {ow.OBJECTIVE_WEIGHTS}.\n"
        "Item 2 may finally be fixed -- but only if those numbers are CALCULATED "
        "from data the app holds, or are a stated human judgement with an owner. "
        "Say which in docs/AGENTS.md, then rewrite this test to assert the "
        "derivation rather than the absence.")
    assert all(ow.weights_for(o) is None for o in ow.OBJECTIVES), (
        "an objective resolves to weights while the table is empty")


# --------------------------------------------------------------------------
# Item 5: What's moving. The map's sharpest finding, and the easiest to regress
# back into silence.
# --------------------------------------------------------------------------

def test_whats_moving_reports_nothing_rather_than_a_fixture():
    """This test used to be pinned as a DEFECT: passing meant the bug was there.

    It asserted `len(events) == 4` -- that "What's moving" was still four
    hardcoded rows -- because the map found the defect before anyone was ready
    to fix it, and a defect nobody has written down is one that gets
    rediscovered. PR 58 fixed it, so the assertion inverts: the honest count is
    zero, and the card says why.

    The unconditional-provider check stays. It is still true, and it is still
    the reason: no settings branch, and `app/providers/live.py` has a market
    provider and an FX provider and no economic provider at all. When one is
    wired in, this test should fail and send the author to docs/AGENTS.md.
    """
    from app.providers.builtin import BuiltinEconomicDataProvider
    from app.providers.registry import economic_provider

    assert isinstance(economic_provider(), BuiltinEconomicDataProvider), (
        "a live economic provider is now wired in. Update docs/AGENTS.md and "
        "this test -- the panel can carry real events again.")

    events = BuiltinEconomicDataProvider().get_events()
    assert events == [], (
        f"the builtin provider invents {len(events)} events again. It has no "
        "source, so the honest count is zero; see STATUS.md PR 58.")


def test_the_research_agent_passes_through_whatever_the_provider_gives_it(monkeypatch):
    """It filters nothing, so an empty panel is the provider's answer, not its own.

    This used to assert four events out of a four-row fixture. The point was
    never the number: it was that if the section ever looked thin, this test
    said whether the AGENT was thinning it. That still holds with the fixture
    gone -- an empty list here means the provider had nothing, not that the
    agent dropped it.
    """
    from app.agents.research_agent import ResearchAgent
    from app.providers.base import EconomicEvent

    events = ResearchAgent().scan()
    assert events == [], (
        f"ResearchAgent emitted {len(events)} events with no provider wired in")

    # One in, one out -- checked by giving it some, rather than by reading the
    # source for things that look like filters. The first version of this
    # assertion grepped for "[:" and tripped over the sha1 slice in the event
    # id, which is the difference between testing behaviour and testing prose.
    stub = [EconomicEvent(event_type="EARNINGS", description="stub",
                          affected_assets=["X"], horizon="SHORT", severity=n)
            for n in (10, 90, 50)]
    monkeypatch.setattr("app.agents.research_agent.guarded_events", lambda: stub)

    out = ResearchAgent().scan()
    assert len(out) == len(stub), (
        f"{len(stub)} events in, {len(out)} out -- the agent is thinning the "
        "list, so an empty panel stops being evidence about the provider")
    assert [e.relevance_score for e in out] == [90, 50, 10], "relevance ordering is gone"


@pytest.mark.parametrize("claim", [
    "app/agents/research_agent.py:15",
    "app/agents/screener_agent.py:22",
    "app/agents/fee_agent.py:26",
    "app/agents/allocation_agent.py:22",
    "app/agents/adversary.py:116",
    "app/services/war_room.py:18",
])
def test_every_line_anchor_in_the_map_points_where_it_says(claim):
    """The whole document's value is that its citations are openable.

    A `file:line` that has drifted by three lines is worse than no citation --
    it reads as verified and is not.
    """
    path, lineno = claim.rsplit(":", 1)
    root = Path(__file__).resolve().parents[1]
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    target = lines[int(lineno) - 1]
    assert re.match(r"\s*(class |AGENTS = )", target), (
        f"docs/AGENTS.md cites {claim}, which is now:\n    {target!r}\n"
        "Re-anchor the citation.")

    text = DOC.read_text(encoding="utf-8")
    assert claim in text, f"docs/AGENTS.md no longer cites {claim}"
