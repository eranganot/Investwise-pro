"""Lag Engine input (Section 4.2 / 5 - CSV/API intake shape)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.state_machine import ActionType, Market


class LagObservation(BaseModel):
    ticker: str
    market: Market
    depth: int = Field(ge=1, le=3)          # 1 = surface/hype ... 3 = structural backbone
    spot_price: float = Field(gt=0)
    listing_price: float = Field(gt=0)
    action_type: ActionType = ActionType.BUY
    expected_return_pct: Optional[float] = None
    volatility_pct: Optional[float] = None
