"""Formal scoring framework (Section Z) - strict 0-100 normalized scores."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.schemas.validation import STRICT, Score


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
    model_config = STRICT
    ret: Score        # S_return  (serialized as "return")
    tax: Score        # S_tax
    risk: Score       # S_risk
    liquidity: Score  # S_liquidity
    conviction: Score # S_conviction

    def as_contract(self) -> dict:
        return {"return": self.ret, "tax": self.tax, "risk": self.risk,
                "liquidity": self.liquidity, "conviction": self.conviction}


class ConfidenceBreakdown(BaseModel):
    """Four normalized (0-100) confidence components."""
    model_config = STRICT
    data_quality: Score
    model_agreement: Score
    historical_accuracy: Score
    market_stability: Score
