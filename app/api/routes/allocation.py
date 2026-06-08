"""Allocation Engine endpoint (Section Y)."""
from fastapi import APIRouter

from app.engines.allocation_engine import AllocationEngine
from app.schemas.allocation import AllocationRequest

router = APIRouter(prefix="/api/v1", tags=["allocation"])


@router.post("/allocation/analyze")
async def analyze(req: AllocationRequest) -> dict:
    report = AllocationEngine().compute(
        target_allocation=req.target_allocation,
        current_allocation=req.current_allocation,
        nav=req.nav,
    )
    return report.model_dump()
