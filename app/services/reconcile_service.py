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

  `deposits`  securities basis should equal what you actually paid in.
              Contaminated in BOTH directions by an unbounded amount: selling a
              winner and reinvesting re-stamps basis at today's price and raises
              it, selling a loser lowers it, and this book did both. So it is
              not a bound, despite an earlier version of this file claiming it
              was an upper one.
  `snapshot`  NAV now over the last NAV recorded before the first bad accept.
              The better of the two -- a sale is NAV-neutral, so the only
              contamination is real market movement over the window, which is
              bounded at a few percent.
  `manual`    you supply `s`, or one holding's true share count.

`preview()` computes all of them and returns the arithmetic; `apply()` writes
only the one you name, and refuses a scale that would move the gap further from
zero than it already is.

**When to stop.** After the repair, cost basis above deposits is normal
accounting, not residue: any book that has rebalanced carries some, because
reinvesting a realised gain re-stamps the new lot at market. `set_baseline()`
draws that line explicitly rather than scaling shares again to satisfy a label.
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
    # Basis accepted as the starting point, if the line has been drawn.
    baseline = await get_baseline(session, user)
    baseline_ils = float(baseline.get("amount_ils") or 0.0)

    anchors = []

    # 1) Deposits. Cash is not scaled by the bug, so it comes off both sides.
    sec_contributed = contributed + baseline_ils - cash_ils
    if sec_contributed > 0 and sec_basis > 0:
        s = sec_basis / sec_contributed
        anchors.append({
            "id": "deposits", "scale": round(s, 6),
            "basis": f"securities cost basis {sec_basis:,.2f} / (deposits "
                     f"{contributed:,.2f} - cash {cash_ils:,.2f})",
            "resulting_nav_ils": round(sec_value / s + cash_ils, 2),
            "resulting_basis_ils": round(sec_basis / s + cash_ils, 2),
            "caveat": ("Contaminated in BOTH directions by an unbounded amount, so it "
                       "is not a bound at all. Selling a winner and reinvesting "
                       "re-stamps basis at today's price and raises it; selling a "
                       "loser lowers it. This book did both. (An earlier version of "
                       "this string called it an upper bound -- that was asserting a "
                       "direction it cannot know.)"),
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
                "caveat": ("The better anchor of the two: a sale is NAV-neutral "
                           "(proceeds become cash), so the ONLY thing contaminating "
                           f"this is real market movement since {when} -- a bounded, "
                           "few-percent error, against the unbounded basis creep in "
                           "the deposits anchor. If the market rose over that window, "
                           "true s is slightly BELOW this."),
            })

    return {
        "ok": True, "version": RECONCILE_VERSION,
        "nav_ils": nav_ils, "cash_ils": round(cash_ils, 2),
        "securities_value_ils": round(sec_value, 2),
        "securities_basis_ils": round(sec_basis, 2),
        "contributed_ils": contributed,
        "unexplained_basis_ils": round(
            sec_basis + cash_ils - contributed - baseline_ils, 2),
        "baseline_ils": round(baseline_ils, 2) if baseline else None,
        "baseline_as_of": baseline.get("as_of"),
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


def baseline_key(user) -> str:
    return f"basis_baseline:{getattr(user, 'email', '')}"


async def get_baseline(session, user) -> dict:
    """The basis that was accepted as the starting point, if one was set."""
    import json

    from app.models.tables import KVSetting

    row = await session.get(KVSetting, baseline_key(user))
    if not row:
        return {}
    try:
        return json.loads(row.value) or {}
    except Exception:  # noqa: BLE001
        return {}


async def set_baseline(session, user, *, amount_ils: float, note: str = "") -> dict:
    """Accept today's residual basis as the starting point, and say so.

    **Why this exists rather than another share-scaling pass.** After the repair
    the live book showed 350.16 of cost basis above recorded deposits. That is
    not phantom, and scaling shares again to make it vanish would delete real
    holdings to satisfy a label. Cost basis legitimately exceeds deposits in any
    book that has ever rebalanced: selling a winner and reinvesting re-stamps
    the new lot at today's price, so basis rises with no new money. Every real
    brokerage account does this.

    It is also inside the reconciliation's own error bars -- 350 on 20,350 is
    1.7%, and the two anchors that produced the scale were 1.8% apart. So it
    cannot be distinguished from estimation error, and forcing it to zero would
    be false precision dressed as a fix.

    What is true is that nothing before the trade ledger existed can be
    reconstructed. So this draws the line explicitly: basis as of today is
    accepted, the ledger measures divergence from here, and the label stops
    calling normal accounting "unexplained".
    """
    import json
    from datetime import datetime, timezone

    from app.models.tables import KVSetting
    from app.services import ledger_service as ledger

    today = datetime.now(timezone.utc).date().isoformat()
    payload = {"amount_ils": round(float(amount_ils), 2), "as_of": today,
               "note": note or "accepted as the starting point"}
    row = await session.get(KVSetting, baseline_key(user))
    if row:
        row.value = json.dumps(payload)
    else:
        session.add(KVSetting(key=baseline_key(user), value=json.dumps(payload)))
    # Visible in the ledger, like every other correction.
    await ledger.record_leg(
        session, user, ticker=None, txn_type=ledger.ADJUST, quantity=0,
        value_ils=0.0, source="baseline",
        note=f"basis baseline {payload['amount_ils']:,.2f} as of {today}"[:255])
    await session.commit()
    return {"ok": True, **payload}


async def apply(session, user, *, scale: float, note: str = "",
                force: bool = False) -> dict:
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

    # REFUSE A SCALE THAT MAKES THE GAP WORSE. This is not hypothetical: on
    # 2026-08-24 the same 1.360168 was applied twice in a row, taking a book
    # from a 350 gap to a -4,874 one, because the operation reads as idempotent
    # and is a plain multiply. The smoke caught it AFTER the write; this catches
    # it before. `force` exists for the deliberate second pass, and has to be
    # asked for by name.
    snap = await preview(session, user)
    if snap.get("ok"):
        gap_now = float(snap.get("unexplained_basis_ils") or 0.0)
        sec_basis = float(snap.get("securities_basis_ils") or 0.0)
        cash = float(snap.get("cash_ils") or 0.0)
        contributed = float(snap.get("contributed_ils") or 0.0)
        gap_after = (sec_basis / float(scale)) + cash - contributed
        if not force and abs(gap_after) > abs(gap_now) + 0.01:
            return {
                "ok": False,
                "error": (f"refusing: this would move the unexplained gap from "
                          f"{gap_now:,.2f} to {gap_after:,.2f} -- further from zero, "
                          f"not closer. If the book has already been reconciled, a "
                          f"second pass at the same scale double-corrects it. Pass "
                          f"force=true if you mean it."),
                "gap_now": round(gap_now, 2), "gap_after": round(gap_after, 2),
            }

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
