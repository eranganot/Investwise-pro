"""GET /portfolio: every holding valued in the base currency, with its gains.

PR 118b (L7): moved out of `routes/intake.py`, unchanged.
"""
from app.services.allocation_mix import classify_holding
from app.services.intake_service import list_positions, total_contributed
from app.services.ledger_service import last_buy


async def holdings(session, user, entity: str | None = None) -> dict:
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
            "last_buy": last_buy(_buys, p, price, _td),
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
