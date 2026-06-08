"""Safety Layer endpoint (Section 8)."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.engines.safety_engine import SafetyEngine

router = APIRouter(prefix="/api/v1", tags=["safety"])


class SafetyCheckRequest(BaseModel):
    holdings: dict[str, float] = {}
    liquidity_ratio: float = 1.0
    proposals: list[dict] = []


@router.post("/safety/check")
async def safety_check(req: SafetyCheckRequest) -> dict:
    report = SafetyEngine().check(
        holdings=req.holdings,
        liquidity_ratio=req.liquidity_ratio,
        proposals=req.proposals,
    )
    return report.model_dump()
