"""Market data DTOs."""
from __future__ import annotations

from pydantic import BaseModel


class Quote(BaseModel):
    ticker: str
    market: str
    price: float
    currency: str
    as_of: str


class FXRate(BaseModel):
    base: str
    quote: str
    rate: float
    as_of: str


class EconomicEvent(BaseModel):
    event_type: str
    description: str
    affected_assets: list[str]
    horizon: str           # SHORT | MEDIUM | LONG
    severity: int          # 0-100
