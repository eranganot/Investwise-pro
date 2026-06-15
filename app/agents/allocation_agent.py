"""Section AB - ALLOCATION AGENT (reactive portfolio-construction specialist).

Holds programmatic veto rights over the Decision Engine: a high-scoring action
that would push an already-overweight asset class further out of policy (or
breach a concentration cap) is blocked with a VetoException before it can reach
the user's feed.
"""
from __future__ import annotations

from app.engines.allocation_engine import AllocationEngine
from app.schemas.allocation import AllocationReport


class VetoException(Exception):
    """Raised by the Allocation Agent to block a recommendation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AllocationAgent:
    def __init__(self, engine: AllocationEngine | None = None) -> None:
        self.engine = engine or AllocationEngine()

    def review_buy(self, asset_class: str, report: AllocationReport) -> None:
        """Veto a BUY into an asset class that is already overweight vs policy."""
        if asset_class in self.engine.overweight_classes(report):
            drift = report.drift_percentage.get(asset_class, 0.0)
            raise VetoException(
                f"Allocation veto: {asset_class} is overweight by {drift:+.0%} vs target; "
                f"a BUY would worsen policy drift."
            )
