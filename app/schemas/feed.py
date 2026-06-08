"""Decision-feed request models (shared by route + orchestrator)."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.allocation import AllocationRequest
from app.schemas.lag import LagObservation


class PortfolioContext(BaseModel):
    holdings: dict[str, float] = {}
    liquidity_ratio: float = 1.0


class GenerateRequest(BaseModel):
    observations: list[LagObservation] | None = None
    portfolio: PortfolioContext | None = None
    from_portfolio: bool = False
    entity_name: str | None = None
    allocation: AllocationRequest | None = None
    asset_class_map: dict[str, str] | None = None
