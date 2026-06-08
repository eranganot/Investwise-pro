"""Formal scoring framework (Section Z) - strict 0-100 normalized scores."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Complexity(str, Enum):
    TRIVIAL = "Trivial"
    EASY = "Easy"
    MODERATE = "Moderate"
    DIFFICULT = "Difficult"
    COMPLEX = "Complex"


COMPLEXITY_FACTOR: dict[Complexity, float] = {
    Complexity.TRIVIAL: 1.0,
    Complexity.EASY: 1.25,
    Complexity.MODERATE: 1.50,
    Complexity.DIFFICULT: 1.75,
    Complexity.COMPLEX: 2.0,
}

IMPACT_WEIGHTS = {"return": 0.30, "tax": 0.25, "risk": 0.25, "liquidity": 0.10, "conviction": 0.10}
CONFIDENCE_WEIGHTS = {"data_quality": 0.40, "model_agreement": 0.30,
                      "historical_accuracy": 0.20, "market_stability": 0.10}


class ImpactScores(BaseModel):
    """Five normalized (0-100) Impact sub-scores."""
    ret: float        # S_return  (serialized as "return")
    tax: float        # S_tax
    risk: float       # S_risk
    liquidity: float  # S_liquidity
    conviction: float # S_conviction

    def as_contract(self) -> dict:
        return {"return": self.ret, "tax": self.tax, "risk": self.risk,
                "liquidity": self.liquidity, "conviction": self.conviction}


class ConfidenceBreakdown(BaseModel):
    """Four normalized (0-100) confidence components."""
    data_quality: float
    model_agreement: float
    historical_accuracy: float
    market_stability: float
