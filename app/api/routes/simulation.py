"""Simulation endpoint (Section 4.6)."""
from fastapi import APIRouter, HTTPException, Query

from app.engines.simulation_engine import SimulationEngine

router = APIRouter(prefix="/api/v1", tags=["simulation"])


@router.get("/simulation")
async def simulation(
    initial_value: float = Query(1_000_000, gt=0, description="Starting portfolio value"),
    expected_return: float = Query(8.0, description="Annual expected return %"),
    volatility: float = Query(15.0, description="Annual volatility %"),
    horizon: str = Query("year", description="month | quarter | year"),
    cpi: float | None = Query(None, description="Annual inflation % (defaults to config)"),
    fx: float | None = Query(None, description="Annual FX drift % (defaults to config)"),
    seed: int | None = Query(None, description="Optional seed for reproducibility"),
) -> dict:
    try:
        res = SimulationEngine(seed=seed).run(
            initial_value=initial_value,
            expected_return_pct=expected_return,
            volatility_pct=volatility,
            horizon=horizon,
            cpi_pct=cpi,
            fx_change_pct=fx,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return res.model_dump()
