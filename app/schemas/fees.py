"""Fee / expense-ratio optimizer outputs (Phase 3.2)."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.validation import NonNegFloat, STRICT


class FeeAlternative(BaseModel):
    model_config = STRICT
    ticker: str
    name: str
    expense_ratio_pct: NonNegFloat
    liquidity: str = "high"


class FeeFinding(BaseModel):
    model_config = STRICT
    ticker: str
    asset_class: str
    value_ils: NonNegFloat
    current_expense_ratio_pct: NonNegFloat
    current_annual_fee_ils: NonNegFloat
    alternative: FeeAlternative
    alternative_annual_fee_ils: NonNegFloat
    annual_saving_ils: NonNegFloat
    saving_pct_of_fee: NonNegFloat   # how much of the fee is removed (0-100)


class FeeReport(BaseModel):
    model_config = STRICT
    threshold_pct: NonNegFloat
    scanned: int
    findings: list[FeeFinding]
    total_annual_saving_ils: NonNegFloat
