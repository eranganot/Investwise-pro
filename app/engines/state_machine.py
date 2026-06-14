"""Orchestrator that drives a signal through the lifecycle.

The transition methods are typed so the compiler/runtime enforce ordering;
``run`` short-circuits to a VetoedSignal when Risk raises the veto flag and
returns ``None`` ("No Action Recommended") when an item fails the display
gates (Impact >= 20, Confidence >= 60).
"""
from __future__ import annotations

from typing import Optional, Protocol

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
    ) -> None:
        self.settings = settings or get_settings()
        self.risk = risk or RiskEngine(self.settings)
        self.tax = tax or TaxEngine(self.settings)
        self.decision = decision or DecisionEngine(self.settings)

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

    # --- full pipeline ---
    def run(
        self, signal: DetectedSignal
    ) -> DisplayedItem | VetoedSignal | None:
        vetted = self.vet(signal)
        if vetted.veto_flag:
            return VetoedSignal(
                source=vetted, reason=vetted.risk_critique or "Risk veto"
            )
        optimized = self.optimize(vetted)
        ranked = self.rank(optimized)
        return self.display(ranked)
