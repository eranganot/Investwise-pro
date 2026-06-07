"""Tax Engine preview endpoint (Section 4.1)."""
from fastapi import APIRouter, Query

from app.engines.tax_engine import TaxEngine

router = APIRouter(prefix="/api/v1", tags=["tax"])


@router.get("/tax/preview")
async def tax_preview(
    gross_gain: float = Query(..., description="Realized/realizable gain in ILS"),
    prior_income: float = Query(0.0, description="Prior annual taxable income (ILS)"),
    loss_carry_forward: float = Query(0.0, description="Available loss carry-forward (ILS)"),
) -> dict:
    breakdown = TaxEngine().compute(
        gross_gain=gross_gain,
        prior_taxable_income=prior_income,
        loss_carry_forward=loss_carry_forward,
    )
    return breakdown.model_dump()
