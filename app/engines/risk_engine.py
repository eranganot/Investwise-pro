"""4.4 RISK ENGINE - Monte Carlo stress, drawdown/vol caps, veto.

Risk OVERRIDES return: a veto here prohibits the item from ever displaying.
Phase 0 stub: no Monte Carlo yet; metrics are 'Awaiting Data', veto=False.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.state_machine import DetectedSignal, VettedSignal


class RiskEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def vet(self, signal: DetectedSignal) -> VettedSignal:
        # TODO Phase 2: 10k-run Monte Carlo -> probability_of_ruin,
        # enforce max_drawdown_cap / volatility_cap -> veto_flag.
        return VettedSignal(
            source=signal,
            probability_of_ruin=None,
            max_drawdown=None,
            volatility=None,
            veto_flag=False,
            risk_critique=(
                "Awaiting Data - Monte Carlo stress test not yet implemented "
                "(Phase 2)."
            ),
        )
