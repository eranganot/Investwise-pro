"""Explainability (XAI) contract (Section AF)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ConfidenceXAI(BaseModel):
    confidence: float
    components: dict


class ExpectedOutcomes(BaseModel):
    net_wealth_delta_currency: Optional[float] = None
    risk_profile_variance: str  # INCREASE | DECREASE | NEUTRAL


class Explanation(BaseModel):
    recommendation_id: str
    why_now: str
    supporting_factors: list[str]
    contradicting_factors: list[str]
    assumptions: list[str]
    confidence_breakdown: ConfidenceXAI
    expected_outcomes: ExpectedOutcomes
    failure_conditions: list[str]
