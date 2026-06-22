"""4.3 WHS ENGINE - Wealth Health Score (weighted composite).

WHS = 0.25*Risk + 0.25*Tax + 0.20*Alloc + 0.15*Liq + 0.15*Thematic
Each component is a 0-100 health score; the result carries a rating band.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings

WEIGHTS = {"risk": 0.25, "tax": 0.25, "alloc": 0.20, "liq": 0.15, "thematic": 0.15}


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
        self, *, risk: float, tax: float, alloc: float, liq: float, thematic: float
    ) -> dict:
        components = {"risk": risk, "tax": tax, "alloc": alloc, "liq": liq, "thematic": thematic}
        for k, v in components.items():
            if not 0.0 <= v <= 100.0:
                raise ValueError(f"{k} must be 0-100, got {v}")
        score = sum(self.WEIGHTS[k] * v for k, v in components.items())
        return {
            "score": round(score, 2),
            "rating": rating(score),
            "components": components,
            "weights": self.WEIGHTS,
        }
