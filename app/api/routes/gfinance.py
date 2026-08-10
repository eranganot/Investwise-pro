"""Markets (futures) + Gemini AI summaries & research endpoints."""
import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services import ai_service
from app.services.markets_service import futures_snapshot

router = APIRouter(prefix="/api/v1", tags=["markets-ai"])


@router.get("/markets/futures")
async def markets_futures() -> dict:
    """Key index/commodity/rate/vol futures + derived market regime.

    The futures regime is a **cross-check only**. It cannot be a signal input:
    the Yahoo futures feed has no usable history, so nothing built on it can be
    backtested -- and a live rule that could not be measured would make every
    number on a strategy card describe something other than what runs. The
    signal-side regime is the price-derived proxy in ``app/engines/regime.py``,
    returned here alongside it so the two can be compared.
    """
    snap = await asyncio.to_thread(futures_snapshot)
    proxy = await asyncio.to_thread(regime_proxy)
    market = dict(snap.get("market") or {})
    market["role"] = "cross_check"
    market["note"] = ("Display only. Futures have no usable history, so this can "
                      "never drive a backtested signal.")
    agree = None
    if proxy.get("ok") and market.get("regime"):
        # Normalise "risk-off" (futures) against "risk_off" (proxy).
        agree = str(market["regime"]).replace("-", "_") == proxy["state"]
    return {**snap, "market": market, "regime_proxy": proxy,
            "regime_agreement": agree,
            "disagreement_note": (
                None if agree is not False else
                "The futures cross-check and the price-derived signal regime "
                "disagree today. That is worth seeing, not worth acting on: only "
                "the price-derived one is measurable, and it is what the "
                "strategies read.")}


def regime_proxy() -> dict:
    """Today's price-derived regime -- the one the strategies actually read.

    Fetched here rather than imported from the signal path so the Markets page
    can show it without a strategy being applied. It calls the same
    ``regime.latest`` the backtest gates on.
    """
    from app.engines import regime as rg
    from app.engines.strategy_backtest import align
    from app.services.backtest_service import _fetch
    series, missing = _fetch(rg.tickers_needed())
    if not series:
        return {"ok": False, "reason": "MISSING_TICKER", "detail": ", ".join(missing)}
    _dates, px = align(series)
    out = rg.latest(px)
    if missing:
        # Say which index is absent rather than quietly narrowing breadth.
        out["degraded"] = f"no history for {', '.join(missing)}"
    return out


@router.get("/markets/summary")
async def markets_summary() -> dict:
    """AI 'what's moving' note over the futures snapshot."""
    return await ai_service.macro_summary()


@router.get("/ai/portfolio-summary")
async def ai_portfolio_summary(session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    return await ai_service.portfolio_summary(session, user)


@router.get("/ai/holding/{ticker}/summary")
async def ai_holding_summary(ticker: str, session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    return await ai_service.holding_summary(session, user, ticker)


@router.post("/ai/holding/{ticker}/research")
async def ai_holding_research(ticker: str, session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    return await ai_service.deep_research(session, user, ticker)
