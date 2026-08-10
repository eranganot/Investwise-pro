"""Market data DTOs."""
from __future__ import annotations

from pydantic import BaseModel


class Quote(BaseModel):
    ticker: str
    market: str
    price: float
    currency: str
    as_of: str
    # Where `as_of` came from. "market" = the venue's own last-trade time, which
    # is the only thing a staleness check may be built on. "request" = the
    # provider gave us nothing and the field is just "when we asked", which is
    # always fresh and therefore says nothing about the instrument. Conflating
    # the two is how a delisted holding kept reporting a healthy price.
    as_of_source: str = "request"


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
