"""PR 148 -- a ceiling on the 2x/3x share of the book (owner, 2026-10-23: 44%).

The leveraged sleeves are planned at 40% (TQQQ vol-target) + 23% (SOXL trend):
fully invested, 63% of the book in 2x/3x funds. PR 141 stops the app buying them
back after a stop; nothing capped how much of the book they could be before one.

One number, `leveraged_ceiling_pct` (a goal setting on the Card-thresholds sheet,
default 44; 0 = no ceiling), and one arithmetic every buyer shares:

  * the HEADROOM is ceiling x NAV minus what the book holds in 2x/3x funds
    (`strategy_profile.GEARED`: the leveraged set without the -1x PSQ/SH);
  * a planner CLIPS its 2x/3x legs to the headroom (`clip_legs`);
  * the book writer REFUSES an app buy that would cross it (`OverCeiling`), the
    backstop for any buyer that forgot -- your own Holdings > Add is not refused.
"""
from __future__ import annotations

CEILING_PCT = 44.0
#: A buy may land this far over before it is refused: prices move between a plan and its Accept.
TOLERANCE_PTS = 0.5


def ceiling_pct() -> float:
    from app.services import thresholds
    return float(thresholds.get("leveraged_ceiling_pct", CEILING_PCT) or 0.0)


def geared() -> frozenset:
    from app.services.strategy_profile import GEARED
    return GEARED


def is_geared(ticker: str | None) -> bool:
    return (ticker or "").upper() in geared()


def geared_value(held_ils: dict[str, float]) -> float:
    return sum(float(v or 0.0) for k, v in (held_ils or {}).items() if is_geared(k))


def headroom_ils(nav: float, held_ils: dict[str, float]) -> float | None:
    """ILS of 2x/3x the book may still buy; None when there is no ceiling."""
    c = ceiling_pct()
    if c <= 0 or not nav or nav <= 0:
        return None
    return max(0.0, c / 100.0 * float(nav) - geared_value(held_ils))


def share_pct(nav: float, held_ils: dict[str, float]) -> float:
    return round(100.0 * geared_value(held_ils) / nav, 2) if nav and nav > 0 else 0.0


def clip_legs(legs: list[dict], headroom: float | None, *, min_leg: float = 0.0,
              key: str = "buy_ils") -> tuple[list[dict], float]:
    """Scale the 2x/3x legs of a buy plan down to `headroom` (pro rata); drop any
    left under `min_leg`. Returns (legs, ILS clipped). Other legs are untouched."""
    if headroom is None:
        return legs, 0.0
    g = [x for x in legs if is_geared(x.get("ticker"))]
    want = sum(float(x.get(key) or 0.0) for x in g)
    if want <= headroom + 1e-6:
        return legs, 0.0
    f = (headroom / want) if want > 0 else 0.0
    out = []
    for x in legs:
        if not is_geared(x.get("ticker")):
            out.append(x)
            continue
        amt = round(float(x.get(key) or 0.0) * f, 2)
        if amt >= max(min_leg, 0.01):
            out.append({**x, key: amt, "clipped_by_ceiling": True})
    return out, round(want - sum(float(x.get(key) or 0.0) for x in out if is_geared(x.get("ticker"))), 2)


def refusal(ticker: str, share: float, buy_ils: float, nav: float) -> str:
    c = ceiling_pct()
    after = share + (100.0 * buy_ils / nav if nav else 0.0)
    return (f"{ticker} is a 2x/3x fund: your 2x/3x funds are {share:.1f}% of the book, and this buy "
            f"would take them to {after:.1f}%, over your {c:g}% ceiling. Change the ceiling on the "
            f"Card-thresholds sheet to buy it.")


def _held_from_rows(rows) -> dict[str, float]:
    from app.services.fx import position_value_ils
    out: dict[str, float] = {}
    for p in rows or []:
        tk = (getattr(p, "ticker", "") or "").upper()
        out[tk] = out.get(tk, 0.0) + float(position_value_ils(p) or 0.0)
    return out


async def assert_within(session, user, buys: dict[str, float],
                        sells: dict[str, float] | None = None) -> None:
    """Raise OverCeiling if buying `buys` ({ticker: ILS}) would cross the ceiling.
    2x/3x `sells` in the same trade ({ticker: ILS}) are netted: TQQQ sold to buy
    SOXL leaves the share where it was. Called by executors BEFORE their first
    write (see buyback_hold.assert_buyable)."""
    g = {k.upper(): float(v or 0.0) for k, v in (buys or {}).items() if is_geared(k) and float(v or 0.0) > 0}
    if not g:
        return
    from app.services.fx import book_nav_ils
    from app.services.intake_service import list_positions
    rows = await list_positions(session, user)
    nav, held = book_nav_ils(rows), _held_from_rows(rows)
    room = headroom_ils(nav, held)
    if room is None:
        return
    total = sum(g.values()) - sum(float(v or 0.0) for k, v in (sells or {}).items() if is_geared(k))
    if total > room + TOLERANCE_PTS / 100.0 * nav:
        tk = max(g, key=g.get)
        raise OverCeiling(refusal(tk, share_pct(nav, held), total, nav))


class OverCeiling(Exception):
    """A buy would take the 2x/3x share past the ceiling. Raised as a subclass of
    `buyback_hold.HeldBack` (see `_bind`), so every Accept route that turns a
    refused buy into a clean 400 does the same here."""


def _bind():
    global OverCeiling
    from app.services.buyback_hold import HeldBack
    OverCeiling = type("OverCeiling", (HeldBack,), {"__doc__": OverCeiling.__doc__})


_bind()


# ---- The plan (C24) -----------------------------------------------------------

def cap_targets(targets: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """Sleeve targets ({ticker: share of NAV}) with the 2x/3x ones scaled down, pro rata,
    to the ceiling. Returns (capped, cut per ticker). What is cut is not re-assigned
    here: the core is the remainder of the sleeves (`book_target.compose`), so the
    excess lands in the core by construction."""
    c = ceiling_pct()
    g = {k: float(v or 0.0) for k, v in (targets or {}).items() if is_geared(k) and float(v or 0.0) > 0}
    tot = sum(g.values())
    if c <= 0 or tot <= c / 100.0 + 1e-9:
        return dict(targets or {}), {}
    f = (c / 100.0) / tot
    out = dict(targets)
    cut = {}
    for k, v in g.items():
        out[k] = v * f
        cut[k.upper()] = round(v - v * f, 6)
    return out, cut


# ---- Today -------------------------------------------------------------------

_BUY_KINDS = ("buy_funded", "buy")


def filter_cards(recs: list[dict], nav: float, held_ils: dict[str, float],
                 *, min_trade: float) -> tuple[list[dict], list[dict]]:
    """Today's buys of 2x/3x funds, cut to the headroom, before reconciliation.

    A card may not claim a purchase its Accept will refuse. So a sized buy of a
    2x/3x fund that does not fit is dropped (and listed, with the reason), and a
    cash card's 2x/3x legs are clipped like a planner's. A card from YOUR rule is
    kept -- dropping it leaves the rule latched with no card (PR 81) -- but says
    that its buy is over the ceiling. Sleeve funding is clipped where it is
    planned (`strategy_service`), so its card already quotes the clipped plan.
    Returns (cards, hidden) where `hidden` is [{id, title, reason}]."""
    room = headroom_ils(nav, held_ils)
    if room is None:
        return recs, []
    tol = TOLERANCE_PTS / 100.0 * (nav or 0.0)
    kept, hidden = [], []
    for r in recs:
        ap = r.get("apply") or {}
        k = ap.get("kind")
        if k in _BUY_KINDS and is_geared(ap.get("ticker")):
            amt = float(r.get("est_amount") or ap.get("amount_ils") or 0.0)
            if amt > room + tol:
                reason = refusal(str(ap.get("ticker")).upper(), share_pct(nav, held_ils), amt, nav)
                if str(r.get("id", "")).startswith("rule_"):
                    r["why"] = f"{r.get('why') or ''} {reason}".strip()
                    r.setdefault("meta", {})["over_leveraged_ceiling"] = True
                    kept.append(r)
                else:
                    hidden.append({"id": r.get("id"), "title": r.get("title"), "reason": reason})
                continue
            room = max(0.0, room - amt)
        elif k == "redeploy_cash":
            legs, clipped = clip_legs(list(ap.get("legs") or []), room, min_leg=min_trade, key="amount_ils")
            if clipped > 0:
                if sum(float(x.get("amount_ils") or 0.0) for x in legs) < min_trade:
                    hidden.append({"id": r.get("id"), "title": r.get("title"),
                                   "reason": f"its 2x/3x part is over your {ceiling_pct():g}% ceiling"})
                    continue
                ap["legs"] = legs
                r["est_amount"] = round(sum(float(x.get("amount_ils") or 0.0) for x in legs), 2)
                r.setdefault("meta", {})["clipped_by_ceiling_ils"] = clipped
            room = max(0.0, room - sum(float(x.get("amount_ils") or 0.0) for x in legs if is_geared(x.get("ticker"))))
        kept.append(r)
    return kept, hidden


def over_ceiling_card(rows, nav: float, held_ils: dict[str, float], *, min_trade: float,
                      rid) -> dict | None:
    """When the 2x/3x share is over the ceiling (a rally, or a ceiling just lowered),
    one card trims the largest 2x/3x holding by the excess. Proceeds land in cash."""
    c = ceiling_pct()
    if c <= 0 or not nav or nav <= 0:
        return None
    share = share_pct(nav, held_ils)
    if share <= c + TOLERANCE_PTS:
        return None
    excess = (share - c) / 100.0 * nav
    g = {k: v for k, v in held_ils.items() if is_geared(k) and v > 0}
    if not g or excess < min_trade:
        return None
    tk = max(g, key=g.get)
    sell = min(excess, g[tk])
    qty = sum(float(p.quantity or 0) for p in rows if (p.ticker or "").upper() == tk)
    shares = round(qty * sell / g[tk], 6) if g[tk] > 0 else 0.0
    if shares <= 0:
        return None
    after = share - 100.0 * sell / nav
    return {
        "id": rid("leveraged_ceiling", tk), "dimension": "risk", "severity": "HIGH",
        "title": f"Your 2x/3x funds are {share:.1f}% of the book, over your {c:g}% ceiling",
        "why": (f"2x/3x funds ({', '.join(sorted(g))}) are {share:.1f}% of the book; your ceiling is {c:g}%. "
                f"A fall in the market they track is two to three times as deep in them."),
        "action": f"Sell about ₪{sell:,.0f} of {tk} (~{shares:g} shares).",
        "impact": (f"Brings your 2x/3x funds from {share:.1f}% to about {after:.1f}% of the book; "
                   f"the proceeds land in cash."),
        "how": [f"Accept sells ~{shares:g} {tk}; the proceeds land in cash",
                "The ceiling is on the Card-thresholds sheet",
                "No brokerage order is placed - mirror it at your broker"],
        "est_amount": round(sell, 2),
        "apply": {"kind": "trim", "ticker": tk, "shares": shares},
        "meta": {"ticker": tk, "geared_pct": share, "ceiling_pct": c, "after_pct": round(after, 2),
                 "source": "leveraged_ceiling"},
    }
