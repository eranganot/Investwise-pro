"""Data intake schemas (Section 5)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.state_machine import ActionType, Market


class IntakePosition(BaseModel):
    ticker: str
    market: Market
    depth: int = Field(default=1, ge=1, le=3)
    spot_price: float = Field(gt=0)
    listing_price: float = Field(gt=0)
    quantity: float = 0.0
    cost_basis: float = 0.0
    expected_return_pct: Optional[float] = None
    volatility_pct: Optional[float] = None
    action_type: ActionType = ActionType.BUY


class PortfolioIntakeRequest(BaseModel):
    entity_name: str = "Personal"
    entity_type: str = "Personal"   # Personal | Spouse | Corp
    account_name: str = "Main"
    positions: list[IntakePosition]
