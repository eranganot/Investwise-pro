"""Portfolio data intake (Section 5) - JSON or CSV upload."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.schemas.intake import IntakePosition, PortfolioIntakeRequest
from app.providers.registry import guarded_quote, market_provider
from app.providers.live import YahooMarketDataProvider
from app.services.performance_service import performance
from app.services.portfolio_risk_service import portfolio_risk
from app.services.intake_service import (
    delete_position,
    update_position,
    ensure_account, ensure_entity, list_positions, upsert_positions,
)

router = APIRouter(prefix="/api/v1", tags=["intake"])

CSV_COLUMNS = ["ticker", "market", "depth", "spot_price", "listing_price",
               "quantity", "cost_basis", "expected_return_pct", "volatility_pct", "action_type"]

TEMPLATE = (
    ",".join(CSV_COLUMNS) + "\n"
    "TEVA,NYSE,3,100,108.2,500,90,10,12,Buy\n"
    "HYPE,NYSE,1,100,112,200,105,15,40,Buy\n"
    "GOLD,SPOT,1,100,103.1,50,98,6,8,Rebalance\n"
)


def _row_to_position(row: dict) -> IntakePosition:
    def num(key, default=None):
        v = (row.get(key) or "").strip()
        return float(v) if v != "" else default
    return IntakePosition(
        ticker=(row.get("ticker") or "").strip(),
        market=(row.get("market") or "").strip(),
        depth=int(num("depth", 1)),
        spot_price=num("spot_price"),
        listing_price=num("listing_price"),
        quantity=num("quantity", 0.0),
        cost_basis=num("cost_basis", 0.0),
        expected_return_pct=num("expected_return_pct"),
        volatility_pct=num("volatility_pct"),
        action_type=(row.get("action_type") or "Buy").strip() or "Buy",
    )


async def _persist(session: AsyncSession, user: User, entity_name, entity_type, account_name, positions):
    entity = await ensure_entity(session, user, entity_name, entity_type)
    account = await ensure_account(session, entity, account_name)
    n = await upsert_positions(session, account, positions)
    await session.commit()
    return {"entity": entity.name, "account": account.name, "positions_saved": n}


@router.get("/intake/template.csv", response_class=PlainTextResponse)
async def intake_template() -> str:
    return TEMPLATE


@router.post("/intake/portfolio", dependencies=[Depends(require_role(Role.ANALYST))])
async def intake_portfolio(req: PortfolioIntakeRequest,
                           session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    if not req.positions:
        raise HTTPException(400, "no positions provided")
    return await _persist(session, user, req.entity_name, req.entity_type, req.account_name, req.positions)


@router.post("/intake/portfolio/csv", dependencies=[Depends(require_role(Role.ANALYST))])
async def intake_portfolio_csv(
    file: UploadFile,
    entity_name: str = "Personal",
    entity_type: str = "Personal",
    account_name: str = "Main",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(acting_user),
) -> dict:
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    positions, errors = [], []
    for i, row in enumerate(reader, start=2):
        try:
            positions.append(_row_to_position(row))
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": i, "error": str(exc)})
    if not positions:
        raise HTTPException(400, {"message": "no valid rows", "errors": errors})
    result = await _persist(session, user, entity_name, entity_type, account_name, positions)
    result["row_errors"] = errors
    return result


@router.get("/portfolio")
async def get_portfolio(entity: str | None = None,
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    from app.services.fx import price_currency, fx_rate
    from app.core.config import get_settings as _gs
    base = _gs().base_currency
    positions = await list_positions(session, user, entity)
    out, nav_ils, nav_native = [], 0.0, 0.0
    for p in positions:
        price = float(p.current_price) if p.current_price is not None else None
        qty = float(p.quantity)
        ccy = price_currency(p.market, p.meta if isinstance(p.meta, dict) else None)
        rate = fx_rate(ccy)
        val_native = (qty * price) if price is not None else 0.0
        val_ils = val_native * rate
        nav_native += val_native
        nav_ils += val_ils
        out.append({
            "id": str(p.id), "ticker": p.ticker, "market": p.market,
            "quantity": qty, "cost_basis": float(p.cost_basis),
            "current_price": price,
            "currency": ccy,
            "current_price_ils": (round(price * rate, 4) if price is not None else None),
            "value_ils": round(val_ils, 2),
            "depth": (p.meta or {}).get("depth"),
            "volatility_pct": (p.meta or {}).get("volatility_pct"),
            "asset_class": (p.meta or {}).get("asset_class"),
        })
    return {
        "count": len(positions),
        "base_currency": base,
        "nav_ils": round(nav_ils, 2),
        "nav_native": round(nav_native, 2),
        "positions": out,
    }


class AddHoldingRequest(BaseModel):
    ticker: str
    amount: float = Field(gt=0)            # budget in the instrument's price currency
    asset_class: str | None = None
    market: str = "NYSE"


@router.post("/portfolio/add", dependencies=[Depends(require_role(Role.ANALYST))])
async def add_holding(req: AddHoldingRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    """Add (or top up) a single holding sized by a money amount, priced live."""
    from app.schemas.intake import IntakePosition
    from app.schemas.state_machine import Market
    from app.services.commodities import get as commodity, is_commodity
    from app.services.intake_service import ensure_account, ensure_entity, upsert_positions
    try:
        price = float(guarded_quote(req.ticker).price)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"couldn't price {req.ticker}: {str(e)[:80]}"}
    if price <= 0:
        return {"ok": False, "error": f"no live price for {req.ticker}"}
    qty = req.amount / price
    ac = req.asset_class or ("Commodities" if is_commodity(req.ticker) else "Equities")
    mk = req.market if req.market in {m.value for m in Market} else "NYSE"
    er = (commodity(req.ticker) or {}).get("expense_ratio_pct")
    ip = IntakePosition(ticker=req.ticker.upper(), market=Market(mk), depth=1,
                        spot_price=price, listing_price=price, quantity=qty,
                        cost_basis=price, asset_class=ac, expense_ratio_pct=er)
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, [ip])
    await session.commit()
    return {"ok": True, "ticker": req.ticker.upper(), "price": round(price, 2),
            "quantity": round(qty, 4), "value": round(qty * price, 2), "asset_class": ac}


@router.post("/portfolio/refresh-prices", dependencies=[Depends(require_role(Role.ANALYST))])
async def refresh_prices(session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    """Update each holding's current_price from the live market provider."""
    rows = await list_positions(session, user)
    prices, errors = [], []
    primary = market_provider()
    # Keyless fallback: if the configured provider can't price a ticker (e.g. FMP
    # key missing/expired/over-quota, or a deprecated endpoint), use Yahoo so a
    # manual refresh never silently leaves every price stale.
    yahoo = None if primary.name == "yahoo" else YahooMarketDataProvider()
    by_source: dict[str, int] = {}
    for p in rows:
        q, used, last_err = None, None, None
        try:
            q = guarded_quote(p.ticker)
            used = primary.name
        except Exception as e:  # noqa: BLE001
            last_err = e
        if q is None and yahoo is not None:
            try:
                q = yahoo.get_quote(p.ticker)
                used = yahoo.name
            except Exception as e:  # noqa: BLE001
                last_err = e
        if q is None:
            errors.append({"ticker": p.ticker, "error": str(last_err)[:160]})
            continue
        p.current_price = Decimal(str(q.price))
        p.meta = {**(p.meta or {}), "price_as_of": q.as_of, "price_source": used,
                  "price_currency": q.currency}
        by_source[used] = by_source.get(used, 0) + 1
        prices.append({"ticker": p.ticker, "price": q.price, "currency": q.currency,
                       "as_of": q.as_of, "source": used})
    if by_source:
        from app.models.tables import KVSetting
        dominant = max(by_source, key=by_source.get)
        row = await session.get(KVSetting, "last_price_source")
        if row:
            row.value = dominant
        else:
            session.add(KVSetting(key="last_price_source", value=dominant))
    await session.commit()
    src = "+".join(sorted(by_source)) if by_source else primary.name
    return {"source": src, "updated": len(prices), "failed": len(errors),
            "by_source": by_source, "prices": prices, "errors": errors}


@router.post("/portfolio/risk", dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_risk_report(session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Portfolio-level risk: volatility, VaR/CVaR, beta, and goal probability."""
    return await portfolio_risk(session, user)


@router.post("/portfolio/performance", dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_performance(session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Backfilled portfolio performance vs benchmark from real price history."""
    return await performance(session, user)


@router.delete("/portfolio/position")
async def remove_position(ticker: str, market: str | None = None,
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """Remove a holding from the acting user's portfolio (by ticker, optionally market)."""
    removed = await delete_position(session, user, ticker, market)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No holding '{ticker}' found.")
    return {"deleted": removed, "ticker": ticker}


class EditPositionRequest(BaseModel):
    ticker: str | None = None
    market: str | None = None
    asset_class: str | None = None
    quantity: float | None = None
    cost_basis: float | None = None
    current_price: float | None = None


@router.put("/portfolio/position/{position_id}")
async def edit_position(position_id: str, body: EditPositionRequest,
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    """Edit one holding (ticker / market / asset class / shares / prices)."""
    row = await update_position(
        session, user, position_id,
        ticker=body.ticker, market=body.market, asset_class=body.asset_class,
        quantity=body.quantity, cost_basis=body.cost_basis, current_price=body.current_price)
    if row is None:
        raise HTTPException(status_code=404, detail="Holding not found.")
    return {"id": str(row.id), "ticker": row.ticker, "market": row.market,
            "quantity": float(row.quantity), "cost_basis": float(row.cost_basis),
            "current_price": float(row.current_price) if row.current_price is not None else None,
            "asset_class": (row.meta or {}).get("asset_class")}
