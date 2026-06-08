"""Allocation domain schemas (Section Y)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AssetClass(str, Enum):
    EQUITIES = "Equities"
    FIXED_INCOME = "Fixed Income"
    CASH = "Cash"
    COMMODITIES = "Commodities"
    ALTERNATIVES = "Alternatives"
    REAL_ESTATE = "Real Estate"
    PRIVATE = "Private Investments"


class RebalanceAction(BaseModel):
    asset_class: str
    action_type: str                       # BUY | SELL
    target_weight: float
    estimated_trade_value_currency: float  # gross notional to move
    tax_drag_currency: float               # CGT crystallized (SELL only)
    transaction_cost_currency: float
    slippage_cost_currency: float
    net_trade_value_currency: float        # gross minus all frictions


class AllocationReport(BaseModel):
    nav: float
    target_allocation: dict[str, float]
    current_allocation: dict[str, float]
    drift_percentage: dict[str, float]
    rebalance_required: bool
    rebalance_actions: list[RebalanceAction]


class AllocationRequest(BaseModel):
    nav: float = Field(gt=0)
    target_allocation: dict[str, float]
    current_allocation: dict[str, float]
    mode: str = "SAA"   # SAA (strategic) | TAA (tactical)
