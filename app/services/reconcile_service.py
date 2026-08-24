"""Repair a book whose share counts were scaled by the FX bug in `_rebalance_to`.

**What happened.** `_rebalance_to` summed `quantity * current_price` with no FX
normalisation -- adding an ILS cash row to USD-priced equities as if 1 ILS = 1
USD -- and wrote the resulting per-class factor straight back into every share
count. Measured on the live book: a factor of 1.066576, so every holding in the
class was multiplied by 1.0666 with no trade and no cash movement. Fixed in
`5a6dfef`; this module is about the damage already done.

**Why the repair reduces to ONE number.** The bug had two properties that make
it recoverable in a way most corruption is not:

  * It multiplied every position in a class by the SAME factor. The proportions
    between V, MSFT, SCHD and TQQQ are therefore untouched -- the book has the
    right shape and the wrong size.
  * It wrote **only** `quantity`. `cost_basis` per share was never altered.

So there is one unknown, not four: a scale `s` where `true_qty = qty / s`. That
is the whole reason this can be repaired without a broker statement.

**Why this module refuses to pick `s` for you.** Three anchors are available and
they disagree, and the disagreement is information rather than noise:

  `deposits`  securities basis should equal what you actually paid in. Gives the
              LARGEST s -- it over-corrects, because `sell_losers` legitimately
              re-stamped cost basis at market when it sold the losing positions,
              and that is a real basis increase, not phantom.
  `snapshot`  the last NAV recorded before the first bad accept. Gives the
              truest reading IF nothing legitimate changed the book in between --
              but a real sale did, so it is a floor rather than an answer.
  `manual`    you supply `s`, or one holding's true share count.

The honest answer is bracketed by the first two. `preview()` computes all of
them and returns the arithmetic; `apply()` writes only the one you name.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("investwise.reconcile")

RECONCILE_VERSION = "r1"

# The first accept that ran the FX-broken rebalance, from the audit log.
FIRST_BAD_ACCEPT_DATE = "2026-08-21"


def _ils(p, rate_for) -> float:
    return float(p.quantity) * float(p.current_price or 0) * rate_for(p)


async def preview(session, user) -> dict:
    """Read-only. What the book is, what each anchor implies, and why they differ."""
    from app.services.fx import fx_rate, price_currency
    from app.services.intake_service import (
        is_cash_position, list_contributions, list_positions,
    )
    from app.services.nav_history import series_for

    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "error": "no holdings to reconcile"}

    def rate_for(p):
        meta = p.meta if isinstance(p.meta, dict) else {}
        return fx_rate(price_currency(p.market, meta))

    positions, sec_value, sec_basis, cash_ils = [], 0.0, 0.0, 0.0
    for p in rows:
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = rate_for(p)
        value = float(p.quantity) * float(p.current_price or 0) * rate
        basis = float(p.quantity) * float(p.cost_basis or 0) * rate
        if is_cash_position(p.ticker, meta):
            cash_ils += value
            continue
        sec_value += value
        sec_basis += basis
        positions.append({
            "id": str(p.id), "ticker": p.ticker, "market": p.market,
            "quantity": float(p.quantity), "cost_basis": float(p.cost_basis or 0),
            "current_price": float(p.current_price or 0), "fx": rate,
            "value_ils": round(value, 2), "basis_ils": round(basis, 2),
        })

    contributed = round(sum(float(c["amount_ils"]) for c in
                            await list_contributions(session, user)), 2)
    nav_ils = round(sec_value + cash_ils, 2)

    anchors = []

    # 1) Deposits. Cash is not scaled by the bug, so it comes off both sides.
    sec_contributed = contributed - cash_ils
    if sec_contributed > 0 and sec_basis > 0:
        s = sec_basis / sec_contributed
        anchors.append({
            "id": "deposits", "scale": round(s, 6),
            "basis": f"securities cost basis {sec_basis:,.2f} / (deposits "
                     f"{contributed:,.2f} - cash {cash_ils:,.2f})",
            "resulting_nav_ils": round(sec_value / s + cash_ils, 2),
            "resulting_basis_ils": round(sec_basis / s + cash_ils, 2),
            "caveat": ("OVER-corrects. Selling a position re-stamps the basis of what "
                       "you buy with the proceeds at today's price, so some of the gap "
                       "is a real basis increase rather than phantom shares. Treat this "
                       "as the upper bound on s."),
        })

    # 2) The last NAV recorded before the first bad accept.
    try:
        hist = await series_for(session, user, range_key="MAX")
        dates, navs = hist.get("dates") or [], hist.get("nav_ils") or []
    except Exception:  # noqa: BLE001
        dates, navs = [], []
    prior = [(d, n) for d, n in zip(dates, navs) if d < FIRST_BAD_ACCEPT_DATE]
    if prior:
        when, then_nav = prior[-1]
        s = (sec_value + cash_ils) / then_nav if then_nav else None
        if s and s > 0:
            anchors.append({
                "id": "snapshot", "scale": round(s, 6),
                "basis": f"NAV now {nav_ils:,.2f} / NAV recorded {when} {then_nav:,.2f}",
                "resulting_nav_ils": round(sec_value / s + cash_ils, 2),
                "resulting_basis_ils": round(sec_basis / s + cash_ils, 2),
                "caveat": ("A FLOOR, not an answer. It assumes nothing legitimate moved "
                           "the book since that date -- and a real sale did, on "
                           "2026-08-21. It also treats genuine market movement since "
                           f"{when} as if it were part of the error."),
            })

    return {
        "ok": True, "version": RECONCILE_VERSION,
        "nav_ils": nav_ils, "cash_ils": round(cash_ils, 2),
        "securities_value_ils": round(sec_value, 2),
        "securities_basis_ils": round(sec_basis, 2),
        "contributed_ils": contributed,
        "unexplained_basis_ils": round(sec_basis + cash_ils - contributed, 2),
        "positions": positions,
        "anchors": anchors,
        "note": ("The bug scaled every holding in a class by ONE factor and never "
                 "touched cost_basis per share, so the book has the right shape and "
                 "the wrong size. Choose a scale; nothing is written until you do."),
        "auto_apply_forbidden": True,
        "auto_apply_reason": ("The anchors disagree and the disagreement is real. "
                              "Picking one automatically would present an estimate as "
                              "a measurement -- the same move that let 7,388 of "
                              "phantom basis read as +42.48% performance."),
    }


def scale_from_share_count(current_qty: float, true_qty: float) -> float:
    """`s` implied by one holding you actually know."""
    if true_qty <= 0:
        raise ValueError("true share count must be positive")
    return float(current_qty) / float(true_qty)


async def apply(session, user, *, scale: float, note: str = "") -> dict:
    """Divide every non-cash share count by `scale`, and record it as a ledger leg.

    Cash is left alone: it is ILS-native and the bug did not scale it -- it
    zeroed it, which is a different injury and not one this repairs.

    Every adjustment lands in the trade ledger as an `Adjust` row. A correction
    that is itself invisible would leave the next reader in exactly the position
    this whole exercise started from.
    """
    from app.services import ledger_service as ledger
    from app.services.fx import fx_rate, price_currency
    from app.services.intake_service import is_cash_position, list_positions

    if not scale or scale <= 0:
        return {"ok": False, "error": "scale must be positive"}
    if abs(scale - 1.0) < 1e-9:
        return {"ok": False, "error": "a scale of 1.0 would change nothing"}

    rows = await list_positions(session, user)
    changed = []
    for p in rows:
        meta = p.meta if isinstance(p.meta, dict) else {}
        if is_cash_position(p.ticker, meta):
            continue
        before = float(p.quantity)
        after = round(before / float(scale), 6)
        if after <= 0:
            return {"ok": False,
                    "error": f"scale {scale} would zero {p.ticker} -- refusing"}
        rate = fx_rate(price_currency(p.market, meta))
        removed_ils = (before - after) * float(p.current_price or 0) * rate
        from decimal import Decimal
        p.quantity = Decimal(str(after))
        changed.append({"ticker": p.ticker, "market": p.market,
                        "before": before, "after": after,
                        "removed_ils": round(removed_ils, 2)})
        await ledger.record_leg(
            session, user, ticker=p.ticker, market=p.market,
            txn_type=ledger.ADJUST, quantity=round(after - before, 6),
            price=float(p.current_price or 0), value_ils=round(-removed_ils, 2),
            position_id=p.id, source="reconcile",
            note=(note or f"scale {scale:.6f} ({RECONCILE_VERSION})")[:255])
    await session.commit()
    return {"ok": True, "scale": float(scale), "adjusted": changed,
            "removed_ils": round(sum(c["removed_ils"] for c in changed), 2)}
