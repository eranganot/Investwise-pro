"""Risk Engine preview endpoint (Section 4.4)."""
from fastapi import APIRouter, Query

from app.engines.risk_engine import RiskEngine

router = APIRouter(prefix="/api/v1", tags=["risk"])


@router.get("/risk/preview")
async def risk_preview(
    expected_return: float = Query(..., description="Annual expected return %, e.g. 10"),
    volatility: float = Query(..., description="Annual volatility %, e.g. 18"),
    seed: int | None = Query(None, description="Optional seed for reproducible Monte Carlo"),
) -> dict:
    eng = RiskEngine(seed=seed)
    assessment, veto, critique = eng.assess(expected_return, volatility)
    return {**assessment.model_dump(), "veto_flag": veto, "critique": critique}
