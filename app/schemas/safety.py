"""Safety Layer outputs (Section 8)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SafetyFlag(BaseModel):
    type: str           # concentration | liquidity | irrational
    severity: str       # high | medium
    detail: str
    ticker: Optional[str] = None


class SafetyReport(BaseModel):
    verdict: str        # ok | warn | block
    flags: list[SafetyFlag]
