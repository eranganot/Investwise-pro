"""Historical backtesting endpoint (Phase 3.3): validate beta vs 2008/2020/2022."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.engines.backtest_engine import BacktestEngine
from app.models.tables import User
from app.services.intake_service import list_positions
from app.services.portfolio_analytics import compute_snapshot

router = APIRouter(prefix="/api/v1", tags=["backtest"])


def _holdings(rows) -> list[dict]:
    return [{"ticker": p.ticker, "asset_class": (p.meta or {}).get("asset_class") or "Equities",
             "value_ils": float(p.quantity) * float(p.current_price or 0)} for p in rows]


@router.post("/backtest")
async def backtest(session: AsyncSession = Depends(get_session),
                   user: User = Depends(acting_user)) -> dict:
    rows = await list_positions(session, user)
    holdings = _holdings(rows)
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "volatility_pct": (p.meta or {}).get("volatility_pct")} for p in rows]
    vol = compute_snapshot(pdicts)["avg_volatility_pct"] if pdicts else None
    report = BacktestEngine().run(holdings, portfolio_vol_pct=vol)
    return report.model_dump()
