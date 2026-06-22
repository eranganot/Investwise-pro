"""Section 7 OUTPUT CONTRACT - the JSON shape of every recommendation."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.scoring import ConfidenceBreakdown


class ExpectedImpact(BaseModel):
    roi_delta: Optional[float] = None        # %
    risk_reduction: Optional[float] = None   # % drawdown
    tax_impact: Optional[float] = None       # ILS saved or deferred


class TradeOffs(BaseModel):
    gains: str = "Awaiting Data"
    risks: str = "Awaiting Data"


class Recommendation(BaseModel):
    title: str
    action_type: str  # Buy | Sell | Rebalance | Tax | Risk
    trigger: str
    execution_plan: str
    expected_impact: ExpectedImpact = Field(default_factory=ExpectedImpact)
    impact_score: float = 0.0
    confidence: float = 0.0
    confidence_breakdown: ConfidenceBreakdown | None = None
    urgency: int = 1            # 1-100
    complexity: int = 1         # 1-5
    time_sensitivity: str = "Monitor"  # Now | This Week | Monitor
    trade_offs: TradeOffs = Field(default_factory=TradeOffs)
    risk_critique: str = "Awaiting Data"
