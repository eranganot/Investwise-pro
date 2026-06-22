"""Orchestrator that drives a signal through the lifecycle.

The transition methods are typed so the compiler/runtime enforce ordering;
``run`` short-circuits to a VetoedSignal when Risk raises the veto flag and
returns ``None`` ("No Action Recommended") when an item fails the display
gates (Impact >= 20, Confidence >= 60).

Phase 1.3: ``cross_examine`` routes the state through the Adversary after *every*
agent output. A BLOCK-severity finding (when ``adversary_enforce_veto``) halts
the pipeline with a hard veto before the next agent runs. ``run`` delegates to
``cross_examine`` and returns just the outcome, so existing callers are
unchanged while the per-stage critique log is available to those who want it.
"""
from __future__ import annotations

from typing import Optional, Protocol

from pydantic import BaseModel

from app.agents.adversary import Adversary, AdversaryNote
from app.core.config import Settings, get_settings
from app.engines.decision_engine import DecisionEngine
from app.engines.risk_engine import RiskEngine
from app.engines.tax_engine import TaxEngine
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    DisplayedItem,
    OptimizedSignal,
    RankedSignal,
    VetoedSignal,
    VettedSignal,
)
from app.schemas.validation import assert_handoff


class ExaminationResult(BaseModel):
    """Outcome of a full cross-examined run plus the Adversary's per-stage log."""
    model_config = {"arbitrary_types_allowed": True}
    outcome: object  # DisplayedItem | VetoedSignal | None
    notes: list[AdversaryNote] = []


class _Risk(Protocol):
    def vet(self, signal: DetectedSignal) -> VettedSignal: ...


class _Tax(Protocol):
    def optimize(self, signal: VettedSignal) -> OptimizedSignal: ...


class _Decision(Protocol):
    def rank(self, signal: OptimizedSignal) -> RankedSignal: ...


class StateMachine:
    def __init__(
        self,
        risk: _Risk | None = None,
        tax: _Tax | None = None,
        decision: _Decision | None = None,
        settings: Settings | None = None,
        adversary: Adversary | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.risk = risk or RiskEngine(self.settings)
        self.tax = tax or TaxEngine(self.settings)
        self.decision = decision or DecisionEngine(self.settings)
        self.adversary = adversary or Adversary(self.settings)

    # --- individual transitions (each requires the prior stage's type) ---
    # Every transition asserts that the producer handed the consumer exactly the
    # stage type it expects (Phase 1.2 cross-agent contract guard). Combined with
    # the typed ``source`` chain, this makes context drift between agents
    # impossible to pass silently.
    def vet(self, signal: DetectedSignal) -> VettedSignal:
        assert_handoff(signal, DetectedSignal)
        return assert_handoff(self.risk.vet(signal), VettedSignal)

    def optimize(self, signal: VettedSignal) -> OptimizedSignal:
        assert_handoff(signal, VettedSignal)
        if signal.veto_flag:
            raise ValueError("Cannot optimize a vetoed signal (risk override).")
        return assert_handoff(self.tax.optimize(signal), OptimizedSignal)

    def rank(self, signal: OptimizedSignal) -> RankedSignal:
        assert_handoff(signal, OptimizedSignal)
        return assert_handoff(self.decision.rank(signal), RankedSignal)

    def display(self, signal: RankedSignal) -> Optional[DisplayedItem]:
        s = self.settings
        if signal.impact_score < s.min_impact_score or signal.confidence < s.min_confidence:
            return None  # No Action Recommended (Section 8)
        detected = signal.source.source.source  # Ranked->Optimized->Vetted->Detected
        path = "Growth" if detected.action_type == ActionType.BUY else "Bulletproof"
        title = f"{detected.action_type.value} {detected.ticker} ({detected.market.value})"
        return DisplayedItem(source=signal, path=path, title=title)

    # --- adversary veto helper ---
    def _adv_veto(self, vetted: VettedSignal, note: AdversaryNote) -> VetoedSignal:
        return VetoedSignal(source=vetted, reason="ADVERSARY " + note.critique)

    # --- cross-examined pipeline (Phase 1.3) ---
    def cross_examine(self, signal: DetectedSignal) -> ExaminationResult:
        adv, enforce = self.adversary, self.settings.adversary_enforce_veto
        examine = self.settings.adversary_enabled
        notes: list[AdversaryNote] = []

        # Stage 1: Detected
        if examine:
            nd = adv.examine_detected(signal)
            notes.append(nd)
            if enforce and nd.blocks:
                vetted = VettedSignal(source=signal, veto_flag=True,
                                      risk_critique="ADVERSARY " + nd.critique)
                return ExaminationResult(outcome=VetoedSignal(source=vetted, reason=nd.critique), notes=notes)

        # Stage 2: Vetted (Risk)
        vetted = self.vet(signal)
        if examine:
            nv = adv.examine_vetted(vetted)
            notes.append(nv)
        if vetted.veto_flag:
            return ExaminationResult(
                outcome=VetoedSignal(source=vetted, reason=vetted.risk_critique or "Risk veto"),
                notes=notes)
        if examine and enforce and notes and notes[-1].blocks:
            return ExaminationResult(outcome=self._adv_veto(vetted, notes[-1]), notes=notes)

        # Stage 3: Optimized (Tax)
        optimized = self.optimize(vetted)
        if examine:
            no = adv.examine_optimized(optimized)
            notes.append(no)
            if enforce and no.blocks:
                return ExaminationResult(outcome=self._adv_veto(vetted, no), notes=notes)

        # Stage 4: Ranked (Decision)
        ranked = self.rank(optimized)
        if examine:
            nr = adv.examine_ranked(ranked)
            notes.append(nr)
            if enforce and nr.blocks:
                return ExaminationResult(outcome=self._adv_veto(vetted, nr), notes=notes)

        # Stage 5: Display gate
        return ExaminationResult(outcome=self.display(ranked), notes=notes)

    # --- full pipeline (backward-compatible: returns only the outcome) ---
    def run(self, signal: DetectedSignal) -> DisplayedItem | VetoedSignal | None:
        return self.cross_examine(signal).outcome
