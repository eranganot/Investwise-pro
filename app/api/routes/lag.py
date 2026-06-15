"""Lag Engine endpoints (Section 4.2)."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.engines.lag_engine import LagEngine
from app.schemas.lag import LagObservation

router = APIRouter(prefix="/api/v1", tags=["lag"])


class LagScanRequest(BaseModel):
    observations: list[LagObservation]


def _scan_payload(eng: LagEngine, observations: list[LagObservation]) -> dict:
    signals = eng.scan(observations)
    return {
        "count": len(signals),
        "backbone_vs_hype": eng.backbone_vs_hype(observations),
        "detected": [
            {
                "ticker": s.ticker,
                "market": s.market.value,
                "depth": s.depth,
                "divergence_pct": round(s.divergence_pct, 2),
                "priority": round(eng.priority(s.divergence_pct, s.depth), 2),
                "action_type": s.action_type.value,
                "trigger": s.trigger,
            }
            for s in signals
        ],
    }


@router.post("/lag/scan")
async def lag_scan(req: LagScanRequest) -> dict:
    return _scan_payload(LagEngine(), req.observations)
