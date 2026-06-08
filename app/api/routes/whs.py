"""WHS endpoint (Section 4.3)."""
from fastapi import APIRouter, Query

from app.engines.whs_engine import WhsEngine

router = APIRouter(prefix="/api/v1", tags=["whs"])


@router.get("/whs")
async def whs(
    risk: float = Query(70, ge=0, le=100),
    tax: float = Query(75, ge=0, le=100),
    alloc: float = Query(60, ge=0, le=100),
    liq: float = Query(80, ge=0, le=100),
    thematic: float = Query(55, ge=0, le=100),
) -> dict:
    return WhsEngine().compute(risk=risk, tax=tax, alloc=alloc, liq=liq, thematic=thematic)
