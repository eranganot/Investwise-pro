"""Portfolio data intake (Section 5) - JSON or CSV upload."""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.core.offload import offload
from app.models.tables import User
from app.schemas.intake import IntakePosition, PortfolioIntakeRequest
from app.providers.registry import guarded_quote, market_provider
from app.services.allocation_mix import classify, classify_holding
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
    # `fx_rate_with_status`, NOT `fx_rate`. The status is the whole point here.
    #
    # `fx_rate` returns the rate alone, which is right for the call sites doing
    # plain arithmetic -- but this one BUILDS NAV, and an "unresolved" rate is
    # the literal 1.0 that `fx.py:167` returns when the provider failed and
    # nothing was ever cached. That function's own docstring says of it:
    # "Never present it as a value." Read through `fx_rate`, that 1.0 is
    # indistinguishable from a genuine peg, so a holding in a currency nobody
    # could resolve is valued at PAR and lands in NAV with no signal at all.
    #
    # This does not refuse to value it. Writing a holding down is not the app's
    # call -- the same reasoning as `price_stale` twenty lines below. It reports
    # it, so every reader of NAV can say which part of the total rests on a
    # conversion that did not happen.
    from app.services.fx import price_currency, fx_rate_with_status
    from app.core.config import get_settings as _gs
    base = _gs().base_currency
    positions = await list_positions(session, user, entity)
    # THE LAST PRICED BUY per ticker, from the trade ledger, so a holding can
    # show "vs your last buy" beside "vs your average price". Only buys since
    # the position was (re)opened count: a lot from before a full exit is not
    # part of what you hold now. Buys the ledger recorded without a price are
    # skipped rather than shown as 0.
    from datetime import timedelta as _td

    from sqlalchemy import select as _select

    from app.models.tables import Transaction as _Tx
    _buys = (await session.execute(
        _select(_Tx.ticker, _Tx.price, _Tx.executed_at).where(
            _Tx.subject == user.email, _Tx.txn_type == "Buy", _Tx.price > 0)
        .order_by(_Tx.executed_at.desc()))).all()
    out, nav_ils, nav_native, invested_ils = [], 0.0, 0.0, 0.0
    for p in positions:
        price = float(p.current_price) if p.current_price is not None else None
        qty = float(p.quantity)
        ccy = price_currency(p.market, p.meta if isinstance(p.meta, dict) else None)
        rate, fx_status = fx_rate_with_status(ccy)
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
            # "live" | "stale" | "unresolved". On the row, because the row is
            # where a reader can act on it: a value converted at par is not the
            # same fact as one converted at a rate that resolved this morning.
            "fx_status": fx_status,
            "current_price_ils": (round(price * rate, 4) if price is not None else None),
            "value_ils": round(val_ils, 2),
            "invested_ils": round(cost_ils, 2),
            "gain_ils": round(val_ils - cost_ils, 2),
            "depth": (p.meta or {}).get("depth"),
            "volatility_pct": (p.meta or {}).get("volatility_pct"),
            # CLASSIFIED, not the raw stamp. This is the field the holdings
            # card prints, and it is where "BND  NASDAQ  Equities" came from
            # on a live screen. One route reading the row differently from
            # every other reader is how two screens disagree about one book.
            "asset_class": classify_holding(p),
            # A frozen price counts in NAV exactly like a live one, so the row
            # has to say which it is. Without this, "this holding did not move"
            # and "this holding has no market any more" look identical.
            "price_as_of": (p.meta or {}).get("price_as_of"),
            "price_stale": bool((p.meta or {}).get("price_stale")),
            "price_freshness": (p.meta or {}).get("price_freshness"),
            "price_stale_days": (p.meta or {}).get("price_stale_days"),
            # What the provider actually said, when it is not what we stored.
            # Tel Aviv quotes agorot and calls them "ILA"; London quotes pence
            # and calls them "GBp". Both are normalised to the major unit on the
            # way in, and this is how a reader outside the process can tell that
            # it happened. The whole failure was that it did not happen and
            # nothing said so either way.
            "price_currency_quoted": (p.meta or {}).get("price_currency_quoted"),
            "price_currency_repaired": bool((p.meta or {}).get("price_currency_repaired")),
            # The provider answered about a different venue than this holding is
            # declared on, so its quote was refused. The row keeps the price it
            # had, and says the price is not one the app stands behind rather
            # than presenting it as current.
            "price_currency_conflict": (p.meta or {}).get("price_currency_conflict"),
            "price_unverified": bool((p.meta or {}).get("price_unverified")),
            "last_buy": _last_buy(_buys, p, price, _td),
        })
    # "What you put in" is external money, not the book's current cost basis.
    # The basis sum drifts for three reasons that are not deposits: it is
    # FX-converted at TODAY's rate, a sale replaces the original basis with
    # net-of-CGT proceeds (so taking a profit raised the figure), and fee swaps
    # re-stamp basis at the live price. Reported live: ₪20,000 in, ₪20,790 shown.
    # The contributions ledger is the only writer of this number now; the old
    # estimate survives solely for users who have never recorded a deposit, so
    # nobody is shown a confident "₪0 put in".
    # BOTH numbers, each named for what it is. One `invested_ils` that could mean
    # either was the bug: on 2026-08-23 this response carried `invested_ils:
    # 20000` from a contributions ledger holding a single row, while the
    # positions' OWN cost basis in the same payload summed to 27,387.81. The
    # headline read +8,496.54 (+42.48%); the book had actually gained 1,108.73
    # (+4.0%). The 7,387.81 between them was cost basis with no deposit behind it,
    # minted by an FX bug in `_rebalance_to` -- and folding it into "gain" is what
    # let a defect masquerade as performance for two days.
    #
    # So: `gain_vs_basis` answers "did my holdings make money", `gain_vs_
    # contributions` answers "is the book worth more than I paid in", and
    # `unexplained_basis_ils` is the gap that should be ~0 and is the thing to
    # watch. `invested_ils` / `gain_ils` keep their old meaning for existing
    # consumers.
    cost_basis_ils = invested_ils
    contributed = await total_contributed(session, user)
    invested_source = "contributions" if contributed is not None else "cost_basis_estimate"
    if contributed is not None:
        invested_ils = contributed
    gain_ils = nav_ils - invested_ils
    gain_vs_basis_ils = nav_ils - cost_basis_ils
    # WHAT YOUR MONEY DID -- and the headline, now that the book is repaired.
    #
    # PR 2 deliberately moved AWAY from this number, and was right to at the
    # time: NAV then contained 7,388 of positions an FX bug had minted, so
    # NAV-minus-deposits was measuring phantom holdings against real cash and
    # reported +42.48%. The metric was never wrong; the NAV was. On a repaired
    # book it is the number the owner actually asked for -- 21,339 against
    # 20,000 paid in is +6.69%.
    #
    # The 350 by which cost basis exceeds deposits is REALISED GAIN, not a
    # discrepancy: sell a winner, reinvest the proceeds, and the new lot is
    # stamped at a price that already contained the gain. Basis rises with no
    # new money. Every brokerage account does this, and gain-vs-contributions
    # counts it correctly -- which is why the "unexplained" warning and the
    # basis baseline built to silence it both come out with this change.
    gain_vs_contrib_ils = (nav_ils - contributed) if contributed is not None else None
    # Basis accepted as the starting point, if that line has been drawn. Cost
    # basis above deposits is NORMAL in any book that has rebalanced -- selling a
    # winner and reinvesting re-stamps the new lot at market, so basis rises with
    # no new money. Only the part above an accepted baseline is worth flagging.
    from app.services.reconcile_service import get_baseline as _get_baseline
    _bl = await _get_baseline(session, user)
    baseline_ils = float(_bl.get("amount_ils") or 0.0)
    unexplained = ((cost_basis_ils - contributed - baseline_ils)
                   if contributed is not None else None)
    return {
        "count": len(positions),
        "base_currency": base,
        "nav_ils": round(nav_ils, 2),
        "nav_native": round(nav_native, 2),
        "invested_ils": round(invested_ils, 2),
        "invested_source": invested_source,
        "gain_ils": round(gain_ils, 2),
        "gain_pct": (round(gain_ils / invested_ils * 100, 2) if invested_ils else None),
        # What the holdings themselves did -- the honest performance number.
        "cost_basis_ils": round(cost_basis_ils, 2),
        "gain_vs_basis_ils": round(gain_vs_basis_ils, 2),
        "gain_vs_basis_pct": (round(gain_vs_basis_ils / cost_basis_ils * 100, 2)
                              if cost_basis_ils else None),
        # THE HEADLINE: what your money did against what you paid in.
        "contributed_ils": (round(contributed, 2) if contributed is not None else None),
        "gain_vs_contributions_ils": (round(gain_vs_contrib_ils, 2)
                                      if gain_vs_contrib_ils is not None else None),
        "gain_vs_contributions_pct": (round(gain_vs_contrib_ils / contributed * 100, 2)
                                      if gain_vs_contrib_ils is not None and contributed
                                      else None),
        # Kept as a diagnostic, no longer an alarm. Basis above deposits is
        # realised gain that has been re-stamped into new lots, which is what a
        # rebalanced book looks like -- not something to be explained away.
        "unexplained_basis_ils": (round(unexplained, 2) if unexplained is not None else None),
        "basis_baseline_ils": (round(baseline_ils, 2) if _bl else None),
        "basis_baseline_as_of": _bl.get("as_of"),
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
        # THE THIRD WAY A PRICE CAN BE UNUSABLE, and the one that had no name.
        #
        # `stale_positions` covers a price nothing has traded against and
        # `fx_unresolved_positions` covers a rate that never resolved. A holding
        # whose price is 0 was disclosed by NEITHER: it simply valued at 0 ILS,
        # dropped out of NAV, and rendered on the holdings card as a line worth
        # nothing -- indistinguishable from a position that really is worthless.
        # On 2026-09-01 that cost two holdings, deleted because a row showing 0
        # looks like junk; by the next morning the survivors were priced again
        # and there was nothing left to explain what had happened.
        #
        # Derived from the PRICE, not from a meta flag, so rows already carrying
        # a zero from before the refresh guard shipped are disclosed too. A flag
        # only describes rows written by the build that sets it.
        "unpriced_positions": [
            {"ticker": o["ticker"], "market": o["market"], "quantity": o["quantity"],
             "cost_basis_ils": o["invested_ils"],
             "reason": ("no price has ever been recorded for this holding"
                        if o["current_price"] is None else
                        "the last quote was zero, so this holding could not be valued")}
            for o in out if not (o["current_price"] or 0) > 0
            and (o["ticker"] or "").upper() != "CASH"],
        # What NAV is MISSING because of it, at cost. Not a valuation -- cost is
        # the only number the app has for a holding it cannot price, and saying
        # "about this much is not in the total" beats letting the total look
        # complete. NAV itself is left alone: inventing a price is the one thing
        # worse than admitting there isn't one.
        "unpriced_cost_ils": round(sum(o["invested_ils"] for o in out
                                       if not (o["current_price"] or 0) > 0
                                       and (o["ticker"] or "").upper() != "CASH"), 2),
        # THE SAME DISCLOSURE, FOR THE OTHER HALF OF THE MULTIPLICATION.
        #
        # `value_ils` is `quantity x price x rate`. `stale_positions` above says
        # when the PRICE cannot be trusted; nothing said when the RATE could
        # not. An unresolved rate is 1.0 -- a holding quoted in a currency the
        # provider could not resolve is counted into NAV as though its currency
        # were the base one. On a TASE or LSE holding that is not a rounding
        # difference, it is the entire conversion.
        #
        # Split from `stale` deliberately, because they need different answers:
        # "stale" is a real rate that is a few hours old and roughly right;
        # "unresolved" is not a rate at all.
        "fx_unresolved_positions": [
            {"ticker": o["ticker"], "currency": o["currency"], "value_ils": o["value_ils"]}
            for o in out if o.get("fx_status") == "unresolved"],
        "fx_unresolved_value_ils": round(
            sum(o["value_ils"] for o in out if o.get("fx_status") == "unresolved"), 2),
        "fx_stale_positions": [
            {"ticker": o["ticker"], "currency": o["currency"], "value_ils": o["value_ils"]}
            for o in out if o.get("fx_status") == "stale"],
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
    """Buy a holding by a money amount, paid out of cash, priced live.

    PR 105. This called `upsert_positions`, an intake writer that REPLACES the
    row: "add $1,000 of IAU" to 100 held shares left 7.54 shares, and the money
    came from nowhere -- no cash debit, no ledger row. A purchase is now what
    every other buy in the app is: `buy_into_book` blends into what you hold,
    the cost leaves cash (owner, 2026-10-11: "to buy a holding I put money in
    cash, then buy with it"), and the leg is recorded.
    """
    from app.schemas.state_machine import Market
    from app.services import ledger_service
    from app.services.commodities import get as commodity, is_commodity
    from app.services.fx import quote_in_ils
    from app.services.intake_service import buy_into_book, get_cash, set_cash
    try:
        _q = await offload(guarded_quote, req.ticker)
        # THE 100x TRAP: Yahoo quotes Tel Aviv in AGOROT and says so only in the
        # currency code (TEVA.TA 11030.0 ILA = 110.30 ILS). `quote_in_ils` takes
        # the major unit first, then converts.
        price, _ccy, price_ils = quote_in_ils(float(_q.price), getattr(_q, "currency", None))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"couldn't price {req.ticker}: {str(e)[:80]}"}
    if price <= 0 or price_ils <= 0:
        return {"ok": False, "error": f"no live price for {req.ticker}"}
    qty = req.amount / price
    cost_ils = round(qty * price_ils, 2)
    cash = await get_cash(session, user)
    if cost_ils > cash + 0.005:
        return {"ok": False, "error": (f"not enough cash: this costs ₪{cost_ils:,.0f} and you have "
                                       f"₪{cash:,.0f}. Add money to cash first (Holdings › Set liquid cash)."),
                "cost_ils": cost_ils, "cash_ils": round(cash, 2)}
    # No "Equities" fallback: `classify` reads the tables far better than a
    # coin flip at the moment of least information does.
    ac = req.asset_class or ("Commodities" if is_commodity(req.ticker) else None)
    mk = req.market if req.market in {m.value for m in Market} else "NYSE"
    tk = req.ticker.upper()
    await buy_into_book(session, user, ticker=tk, market=mk, price=price, shares=qty,
                        asset_class=ac)
    er = (commodity(tk) or {}).get("expense_ratio_pct")
    if er is not None:
        from app.services.intake_service import list_positions
        for p in await list_positions(session, user):
            if (p.ticker or "").upper() == tk and isinstance(p.meta, dict)                     and p.meta.get("expense_ratio_pct") is None:
                p.meta = {**p.meta, "expense_ratio_pct": er}
    await set_cash(session, user, round(cash - cost_ils, 2))
    await ledger_service.record_leg(session, user, ticker=tk, txn_type=ledger_service.BUY,
                                    quantity=qty, price=price, value_ils=cost_ils, market=mk,
                                    source="holdings_add")
    await session.commit()
    return {"ok": True, "ticker": tk, "price": round(price, 2),
            "quantity": round(qty, 4), "value": round(qty * price, 2),
            "cost_ils": cost_ils, "cash_left_ils": round(cash - cost_ils, 2),
            "asset_class": classify(tk, mk, ac)}


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


@router.get("/portfolio/nav-history")
async def portfolio_nav_history(range: str = "1M",
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Recorded account history: what the money ACTUALLY did.

    Distinct from /portfolio/performance, which backfills today's holdings
    through their own past. This reads `nav_snapshots` -- days the job actually
    recorded -- and returns a TIME-WEIGHTED percentage series, so a deposit
    moves the value without moving the return.

    Returns ok=false with `recorded_since` and `points` until there are enough
    days. Never seeded from the backfill: a seeded curve is the reconstruction
    wearing the real thing's label.
    """
    from app.services.nav_history import series_for
    return await series_for(session, user, range_key=range)


@router.post("/portfolio/nav-history/snapshot",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_nav_snapshot(session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    """Record today's snapshot now, instead of waiting for tonight's job.

    Idempotent within a day -- it updates today's row rather than adding one.
    Exists so the first day of history starts the moment you deploy, not
    tomorrow: every day without a snapshot is a day that can never be recovered.
    """
    from app.services.nav_history import record_for, record_health_for
    out = await record_for(session, user, source="manual")
    try:
        out["health"] = await record_health_for(session, user)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: losing the score row must not cost the NAV row, which is
        # the one that cannot be recovered.
        out["health"] = {"ok": False, "reason": f"{type(exc).__name__}"}
    await session.commit()
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
    # Editing the share count to zero is an exit, and `update_position` removes
    # the row rather than leaving a 0-share holding in the book. Say so plainly:
    # a silent 200 carrying quantity 0 is what made the ghost look normal.
    if isinstance(row, dict) and row.get("deleted"):
        return {"ok": True, "deleted": True, "ticker": row["ticker"],
                "market": row["market"],
                "note": (f"{row['ticker']} was set to 0 shares, so it has been "
                         f"removed from your book. Any trading rules on it are "
                         f"removed with it.")}
    return {"id": str(row.id), "ticker": row.ticker, "market": row.market,
            "quantity": float(row.quantity), "cost_basis": float(row.cost_basis),
            "current_price": float(row.current_price) if row.current_price is not None else None,
            "asset_class": classify_holding(row)}


class ContributionRequest(BaseModel):
    """Either an absolute total (``set``) or a signed movement (``adjust``)."""
    amount_ils: float
    mode: str = "adjust"          # adjust | set
    note: str = ""
    # PR 74: when the money actually moved. The goal compounds each deposit
    # from its own date; absent, an adjust is "now" and a set keeps the
    # earliest date already on the ledger.
    occurred_at: datetime | None = None


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
    at = body.occurred_at
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if body.mode == "set":
        total = await set_contributed(session, user, body.amount_ils, occurred_at=at)
    else:
        total = await record_contribution(session, user, body.amount_ils, note=body.note,
                                          occurred_at=at)
    return {"ok": True, "total_ils": total, "mode": body.mode}


def _last_buy(buys, p, price, _td) -> dict | None:
    tk = (p.ticker or "").upper()
    opened = p.created_at
    for t, px, when in buys:
        if (t or "").upper() != tk or when is None:
            continue
        if opened is not None:
            o = opened if opened.tzinfo or not when.tzinfo else opened.replace(tzinfo=when.tzinfo)
            w = when if when.tzinfo or not o.tzinfo else when.replace(tzinfo=o.tzinfo)
            if w < o - _td(seconds=60):
                return None
        px = float(px)
        return {"price": round(px, 4), "at": when.isoformat(),
                "change_pct": (round((price / px - 1) * 100, 2) if price and px else None)}
    return None


class LedgerRepairRequest(BaseModel):
    confirm: bool = False
    include_estimates: bool = False
    include_rebalances: bool = False


@router.get("/portfolio/ledger/repair")
async def ledger_repair_preview(session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """READ-ONLY. Every ledger row the repair would change, the value, and why."""
    from app.services.ledger_repair import preview
    return await preview(session, user)


@router.post("/portfolio/ledger/repair", dependencies=[Depends(require_role(Role.ANALYST))])
async def ledger_repair_apply(body: LedgerRepairRequest,
                              session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """Write what the preview proposes. Requires `confirm: true`. Duplicates become
    `Void` (never deleted); blind sells get a size only at exact/balanced
    confidence unless `include_estimates` is set."""
    from app.services.ledger_repair import apply
    out = await apply(session, user, confirm=body.confirm,
                      include_estimates=body.include_estimates,
                      include_rebalances=body.include_rebalances)
    if not out.get("ok"):
        raise HTTPException(status_code=422, detail=out.get("reason"))
    return out


class HeldCashRepairRequest(BaseModel):
    card: str
    legs: str            # "VTI:620,USO:270,BND:270"
    at: str | None = None
    confirm: bool = False


@router.get("/portfolio/ledger/repair-held-cash")
async def held_cash_repair_preview(card: str, legs: str, at: str | None = None,
                                   session: AsyncSession = Depends(get_session),
                                   user: User = Depends(acting_user)) -> dict:
    """PR 86. READ-ONLY: the Buy rows a held-cash accept should have written
    (shares = amount / (price at the accept x FX)), dated at the accept."""
    from app.services.ledger_repair import held_cash_preview, parse_legs
    return await held_cash_preview(session, user, card=card, legs=parse_legs(legs), at=at)


@router.post("/portfolio/ledger/repair-held-cash",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def held_cash_repair_apply(body: HeldCashRepairRequest,
                                 session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    """PR 86. Write the preview's rows. Requires `confirm: true`; idempotent."""
    from app.services.ledger_repair import held_cash_apply, parse_legs
    out = await held_cash_apply(session, user, card=body.card, legs=parse_legs(body.legs),
                                at=body.at, confirm=body.confirm)
    if not out.get("ok"):
        raise HTTPException(status_code=422, detail=out.get("reason"))
    return out


@router.get("/portfolio/ledger")
async def read_ledger(limit: int = 200, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    """READ-ONLY. The trade ledger, newest first: every leg an accept wrote,
    with its source. The only way to audit what an accept did after the fact
    without database access."""
    from sqlalchemy import select

    from app.models.tables import NavSnapshot
    from app.services.ledger_service import recent
    rows = await recent(session, user, limit=max(1, min(int(limit), 2000)))
    # The daily snapshots' cash beside the trades: a day's cash change that the
    # day's trades cannot explain is money an accept created or destroyed.
    snaps = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.subject == getattr(user, "email", None))
        .order_by(NavSnapshot.as_of))).scalars().all()
    return {"count": len(rows), "entries": rows,
            "snapshots": [{"as_of": n.as_of, "nav_ils": float(n.nav_ils or 0),
                           "cash_ils": float(n.cash_ils or 0),
                           "positions": n.positions} for n in snaps]}


@router.get("/portfolio/position-history")
async def read_position_history(ticker: str, range: str = "1M",
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """One holding's price line and your position's value line, for a range.

    `range` is 1D, 1W, 1M, 3M, 1Y or ALL. See services/position_history.py for
    why the value line starts where the trade record starts.
    """
    from app.services.position_history import NotChartable, position_history
    try:
        return await position_history(session, user, ticker, range)
    except NotChartable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Standing orders -- a monthly deposit into cash that records itself
# --------------------------------------------------------------------------- #
class StandingOrderRequest(BaseModel):
    amount_ils: float = Field(gt=0)
    day_of_month: int = Field(default=1, ge=1, le=28)
    note: str = ""


class StandingOrderPatch(BaseModel):
    amount_ils: float | None = Field(default=None, gt=0)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    active: bool | None = None
    note: str | None = None


async def _orders_payload(session: AsyncSession, user: User) -> dict:
    from app.services import standing_order_service as so
    orders = await so.list_orders(session, user)
    return {"orders": [so.to_dict(o) for o in orders],
            "monthly_total_ils": await so.monthly_total(session, user),
            "today": so.local_today().isoformat(),
            "note": ("On the day, the amount is added to your cash and recorded as a "
                     "deposit. No money is moved: this records the transfer your bank makes.")}


@router.get("/portfolio/standing-orders")
async def read_standing_orders(session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    return await _orders_payload(session, user)


@router.post("/portfolio/standing-orders",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def create_standing_order(body: StandingOrderRequest,
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    from app.services import standing_order_service as so
    try:
        o = await so.create_order(session, user, amount_ils=body.amount_ils,
                                  day_of_month=body.day_of_month, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "order": so.to_dict(o), **await _orders_payload(session, user)}


@router.put("/portfolio/standing-orders/{order_id}",
            dependencies=[Depends(require_role(Role.ANALYST))])
async def update_standing_order(order_id: str, body: StandingOrderPatch,
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    from app.services import standing_order_service as so
    try:
        o = await so.update_order(session, user, order_id, amount_ils=body.amount_ils,
                                  day_of_month=body.day_of_month, active=body.active,
                                  note=body.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "order": so.to_dict(o), **await _orders_payload(session, user)}


@router.delete("/portfolio/standing-orders/{order_id}",
               dependencies=[Depends(require_role(Role.ANALYST))])
async def delete_standing_order(order_id: str,
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    from app.services import standing_order_service as so
    try:
        await so.delete_order(session, user, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, **await _orders_payload(session, user)}


@router.post("/portfolio/standing-orders/run",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def run_standing_orders_now(session: AsyncSession = Depends(get_session),
                                  user: User = Depends(acting_user)) -> dict:
    """Credit anything DUE now. Idempotent: nothing due -> nothing credited.

    The scheduler does this hourly; this exists so a smoke test and the user can
    see the same path run without waiting for it.
    """
    from app.services import standing_order_service as so
    done = await so.run_due(session, user)
    pushed = await so._notify(session, user, done)
    return {"ok": True, "credited": done, "pushed": pushed,
            **await _orders_payload(session, user)}


# --------------------------------------------------------------------------- #
# Reconciliation (PR 3)
# --------------------------------------------------------------------------- #
@router.get("/portfolio/reconcile/preview")
async def portfolio_reconcile_preview(session: AsyncSession = Depends(get_session),
                                      user: User = Depends(acting_user)) -> dict:
    """READ-ONLY. What the book is, and what each anchor would make it.

    Deliberately a GET with no side effects, so it can be run and re-run while
    you decide. `apply` is a separate call and requires the scale by name.
    """
    from app.services.reconcile_service import preview
    return await preview(session, user)


class ReconcileApply(BaseModel):
    scale: float | None = None
    ticker: str | None = None
    true_quantity: float | None = None
    confirm: bool = False
    force: bool = False
    note: str = ""


class ReconcileBaseline(BaseModel):
    confirm: bool = False
    note: str = ""


@router.post("/portfolio/reconcile/apply",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_reconcile_apply(body: ReconcileApply,
                                    session: AsyncSession = Depends(get_session),
                                    user: User = Depends(acting_user)) -> dict:
    """Rewrite share counts by a scale you name. Requires `confirm: true`.

    `confirm` is not ceremony. This endpoint rewrites every holding on the book,
    and a write that fires on a bare POST is one a stray retry can trigger -- the
    same reasoning that guards the Phase A plan apply.

    Give either `scale`, or a `ticker` plus the `true_quantity` you know it
    should be, in which case the scale is derived from that one holding.
    """
    from app.services.reconcile_service import (
        apply as _apply, preview as _preview, scale_from_share_count,
    )

    scale = body.scale
    if scale is None and body.ticker and body.true_quantity:
        snap = await _preview(session, user)
        row = next((p for p in snap.get("positions", [])
                    if (p["ticker"] or "").upper() == body.ticker.upper()), None)
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"{body.ticker} is not on the book")
        scale = scale_from_share_count(row["quantity"], body.true_quantity)
    if scale is None:
        raise HTTPException(
            status_code=400,
            detail="give either scale, or ticker + true_quantity")
    if not body.confirm:
        return {"ok": False, "would_scale_by": round(float(scale), 6),
                "note": "nothing written. Re-send with confirm=true to apply."}
    return await _apply(session, user, scale=float(scale), note=body.note,
                        force=body.force)


@router.post("/portfolio/reconcile/baseline",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def portfolio_reconcile_baseline(body: ReconcileBaseline,
                                       session: AsyncSession = Depends(get_session),
                                       user: User = Depends(acting_user)) -> dict:
    """Accept today's residual cost basis as the starting point.

    Not a way to make a warning disappear. Cost basis above deposits is normal
    in any book that has rebalanced -- reinvesting a realised gain re-stamps the
    new lot at market, so basis rises with no new money. What is NOT knowable is
    anything from before the trade ledger existed, so this draws the line
    explicitly: basis as of today is accepted, and the ledger measures
    divergence from here.

    Scaling shares again to force the number to zero would delete real holdings
    to satisfy a label, and would be inside the reconciliation's own error bars
    anyway.
    """
    from app.services.reconcile_service import preview as _preview, set_baseline

    snap = await _preview(session, user)
    if not snap.get("ok"):
        raise HTTPException(status_code=400, detail=snap.get("error", "cannot read the book"))
    residual = float(snap.get("unexplained_basis_ils") or 0.0)
    if abs(residual) < 0.01:
        return {"ok": True, "note": "nothing to accept -- basis already matches deposits."}
    if not body.confirm:
        return {"ok": False, "would_accept_ils": round(residual, 2),
                "note": "nothing written. Re-send with confirm=true to accept."}
    return await set_baseline(session, user, amount_ils=residual, note=body.note)
