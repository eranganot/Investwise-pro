"""PR 11 / T5 -- the war room reads the solver, and the red team can finally veto.

`STATUS.md:79`: *"plan-T5 -- the war room | NOT STARTED. The solver becomes a
constraint the Risk / Decision / Adversary agents read; not an eighth agent, not
the decision maker."*

Two defects, found by reading the code rather than the plan:

**1. The Adversary could not veto anything on the card path.** `build_war_room`
runs `vet` / `optimize` / `rank` / `display` individually and appended the
Adversary's findings as transcript PROSE, branching on nothing.
`adversary_enforce_veto` defaults to **True** and was honoured in exactly one
function -- `StateMachine.cross_examine` -- reachable only from
`/api/v1/decision-feed`. The route that produces the cards people actually see
had no veto at all.

`test_war_room_includes_cross_examination_lines` did not catch it. It asserts
the Adversary lines exist and carry a severity field, which was true the whole
time and says nothing about whether anything acts on them. Coverage that looks
like coverage.

**2. Nothing stored a solver verdict.** `GET /plan/target` recomputed from
scratch every request -- a coarse-then-fine grid, each point one simulation of
ten years of daily closes -- so "the agents consult the solver" could not mean
"call the solver": the war room already costs 6,726 ms on `/recommendations`
while every other agent is under 50 ms.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import solver_store as ss  # noqa: E402


class _Row:
    """A SolverVerdict without a database, so the rule stays testable."""

    def __init__(self, **kw):
        self.subject = kw.get("subject", "a@b.c")
        self.solver_version = kw.get("solver_version", "s1")
        self.outcome = kw.get("outcome", "REACHED")
        self.target_excess_pct = kw.get("target_excess_pct", 2.0)
        self.max_drawdown_pct = kw.get("max_drawdown_pct", 13.3)
        self.solved_total_sleeve_pct = kw.get("solved_total_sleeve_pct", 64.0)
        self.floor_breaches_ceiling = kw.get("floor_breaches_ceiling", False)
        self.binding_constraint = kw.get("binding_constraint", "")
        self.source = kw.get("source", "on_demand")
        self.detail = kw.get("detail", {})
        self.created_at = kw.get("created_at",
                                 datetime.now(timezone.utc) - timedelta(hours=1))


def _aged(hours, **kw):
    return _Row(created_at=datetime.now(timezone.utc) - timedelta(hours=hours), **kw)


# --------------------------------------------------------------------------- #
# The constraint
# --------------------------------------------------------------------------- #
def test_no_solve_on_record_blocks_nothing_and_says_so():
    """A book nobody has solved must not be treated as a book that failed."""
    c = ss.as_constraint(None)
    assert c["known"] is False and c["blocks"] is False
    assert "No solve on record" in c["reason"]


@pytest.mark.parametrize("outcome,blocks", [
    ("UNREACHABLE", True),
    ("DRAWDOWN_BOUND", True),
    ("REACHED", False),
    # Reachable, just not inside the concentration cap. That is a different
    # conversation and not one that should disable a button.
    ("REACHED_ABOVE_CAP", False),
    ("NOT_MEASURABLE", False),
])
def test_only_the_outcomes_that_mean_you_cannot_get_there_block(outcome, blocks):
    assert ss.as_constraint(_Row(outcome=outcome))["blocks"] is blocks


def test_a_stale_verdict_keeps_its_finding_and_loses_its_teeth():
    """Letting a week-old measurement go on disabling buttons is how an app
    starts refusing things for reasons nobody can reconstruct."""
    fresh = ss.as_constraint(_aged(2, outcome="DRAWDOWN_BOUND"))
    stale = ss.as_constraint(_aged(ss.MAX_BLOCKING_AGE_H + 1, outcome="DRAWDOWN_BOUND"))

    assert fresh["blocks"] is True and stale["blocks"] is False
    assert stale["stale"] is True
    assert stale["outcome"] == "DRAWDOWN_BOUND", "it stopped speaking, not just blocking"
    assert "too old" in stale["reason"] and "re-solve" in stale["reason"]


def test_the_age_always_travels_with_the_answer():
    """A verdict allowed to block a card must be able to say how old it is."""
    c = ss.as_constraint(_aged(5, outcome="UNREACHABLE"))
    assert c["age_hours"] == pytest.approx(5.0, abs=0.2)
    assert c["solved_at"]
    assert "5h ago" in c["reason"]


def test_the_core_breaching_the_ceiling_gets_its_own_sentence():
    """It is different in kind: no sleeve size repairs it, INCLUDING ZERO. Saying
    "reduce your sleeves" here would be advice that cannot work."""
    c = ss.as_constraint(_Row(outcome="DRAWDOWN_BOUND", floor_breaches_ceiling=True,
                              max_drawdown_pct=13.3,
                              detail={"floor": {"max_drawdown_pct": 31.9}}))
    assert "core alone breaches" in c["headline"]
    assert "31.9%" in c["headline"] and "13.3%" in c["headline"]
    assert "including none at all" in c["headline"]
    assert "change the core" in c["reason"]


def test_the_constraint_hands_over_a_decision_not_the_solver_output():
    """An agent that can read the whole verdict will start re-deriving
    conclusions from it, and then there are two answers to "can this book get
    there" again."""
    c = ss.as_constraint(_Row(outcome="UNREACHABLE"))
    assert "curve" not in c and "measured" not in c and "would_execute" not in c


# --------------------------------------------------------------------------- #
# The red team can veto on the card path
# --------------------------------------------------------------------------- #
def _sm(enforce=True, enabled=True):
    from app.core.config import get_settings
    from app.engines.state_machine import StateMachine
    s = get_settings().model_copy()
    s.adversary_enforce_veto = enforce
    s.adversary_enabled = enabled
    return StateMachine(settings=s)


class _Note:
    def __init__(self, blocks):
        self.blocks = blocks


def test_the_veto_rule_lives_in_exactly_one_place():
    """It was inline four times in `cross_examine` and absent from the war room,
    which is how the two paths disagreed for as long as they did."""
    sm = _sm()
    assert sm.blocks_now(_Note(True)) is True
    assert sm.blocks_now(_Note(False)) is False
    assert sm.blocks_now(None) is False
    assert _sm(enforce=False).blocks_now(_Note(True)) is False
    assert _sm(enabled=False).blocks_now(_Note(True)) is False


def test_a_blocking_finding_vetoes_the_session_instead_of_being_prose():
    """THE defect. `adversary_enforce_veto` is True by default and was honoured
    on /decision-feed only; the war room appended BLOCK findings as transcript
    text and carried on to DISPLAYED."""
    from app.agents.adversary import AdversaryNote, Severity
    from app.services import war_room as wr
    from app.services.war_room import build_war_room
    from app.engines.state_machine import StateMachine

    blocking = AdversaryNote(stage="Ranked", severity=Severity.BLOCK,
                             critique="impossible sharpe", findings=["fabricated"])
    orig = StateMachine.blocks_now
    try:
        StateMachine.blocks_now = lambda self, note: note is not None and note.stage == "Ranked"
        _patch_examiner(wr, blocking)
        out = build_war_room(_OBS, narrate=False)
    finally:
        StateMachine.blocks_now = orig
        _unpatch_examiner(wr)

    assert out["sessions"], "no sessions at all"
    assert all(s["outcome"] == "VETOED" for s in out["sessions"]), (
        [s["outcome"] for s in out["sessions"]])
    veto_lines = [ln for s in out["sessions"] for ln in s["transcript"]
                  if ln.get("detail", {}).get("enforced")]
    assert veto_lines, "a BLOCK was recorded but nothing acted on it"
    assert "Blocking this" in veto_lines[0]["says"]


def test_every_adversary_line_now_reports_whether_it_blocks():
    """The old lines carried `severity` and `findings` and nothing else, so a
    reader could not tell an opinion from a veto."""
    from app.services.war_room import build_war_room

    out = build_war_room(_OBS, narrate=False)
    adv = [ln for s in out["sessions"] for ln in s["transcript"]
           if ln["agent"] == "Adversary" and "cross-examination" in ln["role"]]
    assert adv
    assert all("blocks" in ln["detail"] for ln in adv)


# --------------------------------------------------------------------------- #
# The solver constraint reaches the session
# --------------------------------------------------------------------------- #
_BLOCKING = {"known": True, "blocks": True, "outcome": "DRAWDOWN_BOUND",
             "headline": "Your core alone breaches your drawdown ceiling",
             "reason": "The solver says the core alone breaches your ceiling (solved 2h ago).",
             "age_hours": 2.0, "solved_at": "2026-08-25T00:00:00+00:00",
             "floor_breaches_ceiling": True}


def test_a_risk_adding_signal_is_marked_blocked_but_still_shown():
    """Not suppressed. Eleven cards went missing in PR 4 and a card deleted
    because of a background measurement nobody can see is strictly worse than
    one that explains itself and refuses."""
    from app.services.war_room import build_war_room

    out = build_war_room(_OBS, narrate=False, constraint=_BLOCKING)
    shown = [s for s in out["sessions"] if s["outcome"] == "DISPLAYED"]
    assert shown, "the constraint suppressed the session instead of marking it"

    # `ActionType.BUY.value` is "Buy", not "BUY" -- the production check
    # uppercases before comparing, and this assertion has to as well or it
    # silently tests nothing.
    buys = [s for s in shown if str(s.get("action_type") or "").upper() == "BUY"]
    assert buys, "fixture produced no BUY signal, so this proves nothing"
    for s in buys:
        assert s["blocked_by"], f"{s['ticker']} adds risk and was not marked"
        assert s["blocked_by"]["kind"] == "solver"
        assert s["blocked_by"]["age_hours"] == 2.0
        lines = [ln for ln in s["transcript"] if "Target solver" in ln.get("role", "")]
        assert lines and lines[0]["detail"]["blocks_one_tap"] is True


def test_a_de_risking_signal_is_never_blocked():
    """The constraint says the book cannot reach its target. Trimming, hedging
    and de-risking are exactly what such a book should be doing -- blocking them
    would turn a safety feature into a trap."""
    from app.services.war_room import build_war_room

    out = build_war_room(_OBS, narrate=False, constraint=_BLOCKING)
    for s in out["sessions"]:
        if s.get("action_type") and str(s["action_type"]).upper() != "BUY":
            assert not s.get("blocked_by"), f"{s['ticker']} ({s['action_type']}) was blocked"


def test_the_transcript_says_the_constraint_was_consulted_even_when_it_does_not_bite():
    """Silence is indistinguishable from "nobody asked", which is the state this
    whole phase exists to end."""
    from app.services.war_room import build_war_room

    quiet = {"known": True, "blocks": False, "outcome": "REACHED",
             "reason": "The solver's last answer was REACHED, which does not block anything.",
             "age_hours": 3.0}
    out = build_war_room(_OBS, narrate=False, constraint=quiet)
    shown = [s for s in out["sessions"] if s["outcome"] == "DISPLAYED"]
    assert shown
    for s in shown:
        lines = [ln for ln in s["transcript"] if "Target solver" in ln.get("role", "")]
        assert lines, f"{s['ticker']}: the constraint was consulted and left no trace"
        assert lines[0]["detail"]["blocks_one_tap"] is False
        assert not s.get("blocked_by")


def test_no_constraint_at_all_changes_nothing():
    """Every book that has never opened the target card is in this state."""
    from app.services.war_room import build_war_room

    before = build_war_room(_OBS, narrate=False)
    after = build_war_room(_OBS, narrate=False, constraint={"known": False, "blocks": False})
    assert [s["outcome"] for s in before["sessions"]] == [s["outcome"] for s in after["sessions"]]
    assert all(not s.get("blocked_by") for s in after["sessions"])
    assert all(not any("Target solver" in ln.get("role", "") for ln in s["transcript"])
               for s in after["sessions"])


def test_a_blocked_card_loses_its_one_tap_and_keeps_its_explanation():
    """The card path: `_is_actionable` reads `apply.kind`, so demoting to "none"
    is the same mechanism every advisory card already uses -- not a new flag
    something could forget to check."""
    import inspect

    from app.services import recommendations as rec
    src = inspect.getsource(rec._war_room_recs)
    assert '"apply": ({"kind": "none"} if _bb else buy.apply_spec)' in src
    assert '"blocked_by": _bb' in src
    # And the reason has to reach the user, not just the payload.
    assert '_bb.get(\'headline\')' in src or "_bb.get(\"headline\")" in src


# --------------------------------------------------------------------------- #
# fixtures + helpers
# --------------------------------------------------------------------------- #
def _observations():
    from app.services.demo_data import DEFAULT_OBSERVATIONS
    return list(DEFAULT_OBSERVATIONS)


_OBS = _observations()


def _patch_examiner(wr_mod, note):
    from app.agents.adversary import Adversary
    wr_mod._ORIG_RANKED = Adversary.examine_ranked
    Adversary.examine_ranked = lambda self, ranked: note


def _unpatch_examiner(wr_mod):
    from app.agents.adversary import Adversary
    if getattr(wr_mod, "_ORIG_RANKED", None):
        Adversary.examine_ranked = wr_mod._ORIG_RANKED
        wr_mod._ORIG_RANKED = None
