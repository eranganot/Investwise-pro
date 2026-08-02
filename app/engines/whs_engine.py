"""4.3 WHS ENGINE - Wealth Health Score (weighted composite).

WHS = 0.25*Risk + 0.25*Tax + 0.20*Alloc + 0.15*Liq + 0.15*Thematic
Each component is a 0-100 health score; the result carries a rating band.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings

WEIGHTS = {"risk": 0.25, "tax": 0.25, "alloc": 0.20, "liq": 0.15, "thematic": 0.15}

# Portfolio health scores only what we can actually observe from the holdings.
# `thematic` had no measured input — it was passed as the constant 60.0 — so it
# silently removed 6 points from every score (a flawless book topped out at 94)
# and left 15% of the number unexplainable to the user, who was never shown it.
# Omitting it and renormalizing across the four measured components is the
# honest fix: every point of the score now traces to something displayed.
MEASURED_WEIGHTS = {"risk": 0.30, "tax": 0.25, "alloc": 0.25, "liq": 0.20}


def rating(score: float) -> str:
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Healthy"
    if score >= 40:
        return "Needs attention"
    return "At risk"


class WhsEngine:
    WEIGHTS = WEIGHTS

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def compute(
        self, *, risk: float, tax: float, alloc: float, liq: float,
        thematic: float | None = None
    ) -> dict:
        """Weighted composite. Pass ``thematic=None`` (the portfolio path) to
        score only the four measured components; pass a value to use the legacy
        five-component weighting."""
        components = {"risk": risk, "tax": tax, "alloc": alloc, "liq": liq}
        if thematic is not None:
            components["thematic"] = thematic
        weights = self.WEIGHTS if thematic is not None else MEASURED_WEIGHTS
        for k, v in components.items():
            if not 0.0 <= v <= 100.0:
                raise ValueError(f"{k} must be 0-100, got {v}")
        score = sum(weights[k] * v for k, v in components.items())
        return {
            "score": round(score, 2),
            "rating": rating(score),
            "components": components,
            "weights": weights,
        }
