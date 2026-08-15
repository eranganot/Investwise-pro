"""Portfolio data intake (Section 5) - JSON or CSV upload."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.schemas.intake import IntakePosition, PortfolioIntakeRequest
from app.providers.registry import guarded_quote, market_provider
from app.services.performance_service import performance
from app.services.portfolio_risk_service import portfolio_risk
from app.services.intake_service import (
    delete_position,
    get_cash, set_cash,
    list_contributions, record_contribution, set_contributed, total_contributed,
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
    out, nav_ils, nav_native, invested_ils = [], 0.0, 0.0, 0.0
    for p in positions:
        price = float(p.current_price) if p.current_price is not None else None
        qty = float(p.quantity)
        ccy = price_currency(p.market, p.meta if isinstance(p.meta, dict) else None)
        rate = fx_rate(ccy)
        val_native = (qty * price) if price is not None else 0.0
        val_ils = val_native * rate
        # What you actually put in: cost_basis is per-share in the position's own
        # price currency, so it needs the same FX normalization as market value.
        cost_ils = qty * float(p.cost_basis) * rate
        nav_native += val_native
        nav_ils += val_ils
        invested_ils += cost_ils
        out.append({
            "id": str(p.id), "ticker": p.ticker, "market": p.market,
            "quantity": qty, "cost_basis": float(p.cost_basis),
            "current_price": price,
            "currency": ccy,
            "current_price_ils": (round(price * rate, 4) if price is not None else None),
            "value_ils": round(val_ils, 2),
            "invested_ils": round(cost_ils, 2),
            "gain_ils": round(val_ils - cost_ils, 2),
            "depth": (p.meta or {}).get("depth"),
            "volatility_pct": (p.meta or {}).get("volatility_pct"),
            "asset_class": (p.meta or {}).get("asset_class"),
            # A frozen price counts in NAV exactly like a live one, so the row
            # has to say which it is. Without this, "this holding did not move"
            # and "this holding has no market any more" look identical.
            "price_as_of": (p.meta or {}).get("price_as_of"),
            "price_stale": bool((p.meta or {}).get("price_stale")),
            "price_freshness": (p.meta or {}).get("price_freshness"),
            "price_stale_days": (p.meta or {}).get("price_stale_days"),
        })
    # "What you put in" is external money, not the book's current cost basis.
    # The basis sum drifts for three reasons that are not deposits: it is
    # FX-converted at TODAY's rate, a sale replaces the original basis with
    # net-of-CGT proceeds (so taking a profit raised the figure), and fee swaps
    # re-stamp basis at the live price. Reported live: ₪20,000 in, ₪20,790 shown.
    # The contributions ledger is the only writer of this number now; the old
    # estimate survives solely for users who have never recorded a deposit, so
    # nobody is shown a confident "₪0 put in".
    contributed = await total_contributed(session, user)
    invested_source = "contributions" if contributed is not None else "cost_basis_estimate"
    if contributed is not None:
        invested_ils = contributed
    gain_ils = nav_ils - invested_ils
    return {
        "count": len(positions),
        "base_currency": base,
        "nav_ils": round(nav_ils, 2),
        "nav_native": round(nav_native, 2),
        "invested_ils": round(invested_ils, 2),
        "invested_source": invested_source,
        "gain_ils": round(gain_ils, 2),
        "gain_pct": (round(gain_ils / invested_ils * 100, 2) if invested_ils else None),
        "cash_ils": round(sum(o["value_ils"] for o in out
                              if (o["asset_class"] or "").lower() == "cash"
                              or (o["ticker"] or "").upper() == "CASH"), 2),
        # Which part of NAV is built on a price nothing has traded against. NAV
        # still includes it — writing a holding down to zero is not the app's
        # call — but every consumer of this number can now say what it rests on.
        "stale_positions": [
            {"ticker": o["ticker"], "value_ils": o["value_ils"],
             "price_as_of": o["price_as_of"], "trading_days": o["price_stale_days"]}
            for o in out if o["price_stale"]],
        "stale_value_ils": round(sum(o["value_ils"] for o in out if o["price_stale"]), 2),
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
    """Update each holding's current_price from the live market provider.

    Delegates to ``pricing_service.refresh_all_positions``. It used to carry its
    own copy of that loop, and the copy had neither the cash guard nor the
    freshness check: a manual refresh repriced the synthetic CASH row against
    NASDAQ:CASH (Pathward Financial) and stamped it USD -- the exact bug phase 1
    fixed in the scheduled job and only there -- and wrote a delisted holding's
    425-day-old price back in as current. Two implementations of one job means a
    fix lands on whichever one you happen to call.
    """
    from app.services.pricing_service import refresh_all_positions
    rows = await list_positions(session, user)
    res = await refresh_all_positions(session, positions=rows)
    by_source = res.get("by_source") or {}
    src = "+".join(sorted(by_source)) if by_source else market_provider().name
    return {"source": src, "updated": res["updated"], "failed": res["failed"],
            "by_source": by_source,
            "prices": res.get("prices") or [], "errors": res.get("errors") or [],
            # Newly visible, and the whole point: what was NOT accepted as current,
            # and what was healed back to its ILS-native invariant.
            "stale": res.get("stale", 0),
            "stale_tickers": res.get("stale_tickers") or [],
            "skipped_cash": res.get("skipped_cash", 0),
            "repaired_cash": res.get("repaired_cash", 0)}


@router.post("/portfolio/risk", dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_risk_report(session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Portfolio-level risk: volatility, VaR/CVaR, beta, and goal probability."""
    return await portfolio_risk(session, user)


# What the Today chart's range buttons mean, in CALENDAR days -- the provider
# takes calendar days, not sessions. Kept server-side so the window that gets
# MEASURED is the window that was asked for.
PERF_RANGES = {"1W": 7, "1M": 31, "1Q": 93, "1Y": 366, "MAX": 2600}
_PERF_MIN_DAYS, _PERF_MAX_DAYS = 7, 2600


@router.post("/portfolio/performance", dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_performance(range: str | None = None,
                                history_days: int | None = None,
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Backfilled portfolio performance vs benchmark from real price history.

    ``range`` is one of 1W / 1M / 1Q / 1Y / MAX; ``history_days`` overrides it.
    Both default to the previous behaviour (252 days) so existing callers are
    unaffected.

    **The window is applied on the server, deliberately.** ``index_series``
    normalises to the first value IN THE SERIES, so a re-based percentage only
    means "change over this window" if the series was fetched for that window.
    Slicing a long series client-side would re-base against the wrong day, and
    slicing a DOWNSAMPLED long series would be worse still -- at 160 points a
    ten-year series is one point per ~16 sessions, so "last week" would be two
    points sixteen sessions apart, drawn as though it were a week.
    """
    days = history_days
    if days is None and range:
        days = PERF_RANGES.get(range.strip().upper())
        if days is None:
            return {"ok": False, "reason": "unknown range",
                    "detail": f"range must be one of {', '.join(PERF_RANGES)}"}
    if days is None:
        days = 252
    days = max(_PERF_MIN_DAYS, min(int(days), _PERF_MAX_DAYS))
    out = await performance(session, user, history_days=days)
    if isinstance(out, dict):
        out["requested_days"] = days
        out["range"] = (range or "").strip().upper() or None
    return out


@router.delete("/portfolio/position")
async def remove_position(ticker: str, market: str | None = None,
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """Remove a holding from the acting user's portfolio (by ticker, optionally market)."""
    removed = await delete_position(session, user, ticker, market)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No holding '{ticker}' found.")
    return {"deleted": removed, "ticker": ticker}


class CashRequest(BaseModel):
    # No ge=0: a negative amount is a withdrawal under mode='adjust'. 'set' rejects
    # negatives in the handler so a typo can't silently zero the balance.
    amount_ils: float = Field(description="New balance when mode='set'; delta when mode='adjust'")
    mode: str = Field(default="set", pattern="^(set|adjust)$")


@router.get("/portfolio/cash")
async def read_cash(session: AsyncSession = Depends(get_session),
                    user: User = Depends(acting_user)) -> dict:
    """Current liquid cash balance, in the base currency."""
    return {"cash_ils": round(await get_cash(session, user), 2)}


@router.post("/portfolio/cash", dependencies=[Depends(require_role(Role.ANALYST))])
async def write_cash(body: CashRequest, session: AsyncSession = Depends(get_session),
                     user: User = Depends(acting_user)) -> dict:
    """Set or adjust liquid cash.

    Cash held outside the app was previously invisible \u2014 it only materialised as
    a side effect of accepting a sell \u2014 so allocation, liquidity scoring and the
    donut all behaved as if the book were fully invested.
    """
    if body.mode == "set" and body.amount_ils < 0:
        raise HTTPException(status_code=422, detail="Cash balance can't be negative.")
    if body.mode == "adjust":
        new_balance = await set_cash(session, user,
                                     await get_cash(session, user) + body.amount_ils)
    else:
        new_balance = await set_cash(session, user, body.amount_ils)
    return {"ok": True, "cash_ils": round(new_balance, 2)}


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


class ContributionRequest(BaseModel):
    """Either an absolute total (``set``) or a signed movement (``adjust``)."""
    amount_ils: float
    mode: str = "adjust"          # adjust | set
    note: str = ""


@router.get("/portfolio/contributions")
async def read_contributions(session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    total = await total_contributed(session, user)
    return {"total_ils": total, "tracked": total is not None,
            "entries": await list_contributions(session, user)}


@router.post("/portfolio/contributions",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def write_contribution(body: ContributionRequest,
                             session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    """Record money moving in or out of the account from outside.

    Nothing in the trading path may call this: selling, trimming and swapping
    rearrange money already inside the account and must leave "what you put in"
    untouched. That conflation is what made a ₪20,000 deposit read as ₪20,790.
    """
    if body.mode == "set":
        total = await set_contributed(session, user, body.amount_ils)
    else:
        total = await record_contribution(session, user, body.amount_ils, note=body.note)
    return {"ok": True, "total_ils": total, "mode": body.mode}
