"""PR 75 -- a take-profit sale keeps the sleeve's target; the app does not buy it back.

Eran, 2026-09-28: "if I chose to sell it should not change the goal of the
sleeve, only the current holding of it (target should remain the same as
planned)". And, asked what then stops the next card re-buying it: "Hold until
price retreats".

Before this, the Sell answer on a rule-vs-sleeve card SHRANK the sleeve
(40% -> 30.38%) so the funder would not buy the slice straight back. That kept
the book consistent by changing his plan. Now the plan stays as he set it and
the sale is remembered instead:

  * a HOLD is recorded per ticker at the price it was sold at;
  * while the ticker trades AT OR ABOVE that price the hold is ACTIVE: every proposer
    of buys treats the sleeve's target as met at its CURRENT weight, and the
    shortfall is parked in the Cash target -- so it is neither re-bought nor
    redirected into other names of the same class;
  * the first time the price is BELOW the sale price the hold is RELEASED for
    good, and ordinary top-ups toward the planned target resume. (At the sale
    price itself nothing has retreated -- that is the price the moment it was
    sold, and a hold that ended there would end the instant it was recorded.)

The displayed sleeve target is never touched (`all_sleeve_targets` is the plan;
`apply_holds` is what the buyers read). A buy the user starts himself (loading
a sleeve's basket from the Plan screen) is his decision and is not blocked.

Stored in `kv_settings` as JSON, one key per user, so no migration.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import KVSetting, User


def _key(user: User) -> str:
    return f"buyback_holds:{user.email}"[:80]


async def load(session: AsyncSession, user: User) -> dict[str, dict]:
    row = await session.get(KVSetting, _key(user))
    if not row:
        return {}
    try:
        data = json.loads(row.value)
    except Exception:  # noqa: BLE001
        return {}
    return {str(k).upper(): v for k, v in data.items()} if isinstance(data, dict) else {}


async def _save(session: AsyncSession, user: User, data: dict) -> None:
    row = await session.get(KVSetting, _key(user))
    if row is None:
        session.add(KVSetting(key=_key(user), value=json.dumps(data)))
    else:
        row.value = json.dumps(data)


async def record(session: AsyncSession, user: User, *, ticker: str, sold_price: float,
                 shares: float, strategy_id: str | None, rule_id: str | None = None,
                 until_rule: str | None = None) -> dict:
    """Remember a take-profit sale. A second sale of the same ticker while a hold
    is active keeps the HIGHER price: the hold ends when the price falls back
    below what was actually sold, and the latest, higher sale is that price."""
    data = await load(session, user)
    tk = ticker.upper()
    prev = data.get(tk)
    level = float(sold_price)
    if until_rule:
        # PR 81 -- a drawdown de-risk holds until the BOOK recovers, not until
        # this ticker's price dips: mid-drawdown the price keeps falling, and a
        # price-released hold would let the realign buy it straight back.
        # Level 0 never "retreats"; `release_for_rule` ends it.
        level = 0.0
    elif prev and not prev.get("until_rule") and float(prev.get("level") or 0) > level:
        level = float(prev["level"])
    elif prev and prev.get("until_rule"):
        until_rule = prev["until_rule"]
        level = 0.0
    sh = float(shares) + (float(prev.get("shares") or 0) if prev else 0.0)
    data[tk] = {"level": round(level, 6), "shares": round(sh, 6),
                "strategy_id": strategy_id, "rule_id": rule_id,
                **({"until_rule": until_rule} if until_rule else {}),
                "at": datetime.now(timezone.utc).isoformat()}
    await _save(session, user, data)
    return data[tk]


async def release_for_rule(session: AsyncSession, user: User, rule_id: str) -> list[str]:
    """End every hold a rule placed (PR 81: the drawdown guard re-armed)."""
    holds = await load(session, user)
    gone = sorted(k for k, v in holds.items() if str(v.get("until_rule")) == str(rule_id))
    if gone:
        await _save(session, user, {k: v for k, v in holds.items() if k not in gone})
    return gone


def _prices(rows) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows or []:
        px = getattr(r, "current_price", None)
        if px is not None and getattr(r, "ticker", None):
            out[r.ticker.upper()] = float(px)
    return out


def active(holds: dict[str, dict], rows) -> dict[str, dict]:
    """Holds whose ticker still trades at or above the sale price. A ticker with no
    price (sold to zero, or unpriced) stays held: "we cannot see the price" is
    not "the price came back down"."""
    px = _prices(rows)
    out = {}
    for tk, h in holds.items():
        now = px.get(tk)
        if now is None or now >= float(h.get("level") or 0):
            out[tk] = {**h, "price_now": now}
    return out


async def release_retreated(session: AsyncSession, user: User, rows) -> list[str]:
    """Drop every hold whose price has fallen below the sale price.
    Called from the Today build, which already writes; `active` alone is
    read-only and already ignores a retreated hold."""
    holds = await load(session, user)
    if not holds:
        return []
    still = active(holds, rows)
    gone = sorted(set(holds) - set(still))
    if gone:
        await _save(session, user, {k: v for k, v in holds.items() if k in still})
    return gone


def weights_of(rows, nav: float) -> dict[str, float]:
    """Each ticker's share of NAV, FX-normalised the way `current_mix` does it."""
    from app.services.fx import fx_rate, price_currency
    out: dict[str, float] = {}
    if not nav:
        return out
    for r in rows or []:
        m = getattr(r, "meta", None)
        rate = fx_rate(price_currency(getattr(r, "market", None), m if isinstance(m, dict) else None))
        v = float(getattr(r, "quantity", 0) or 0) * float(getattr(r, "current_price", 0) or 0) * rate
        tk = (r.ticker or "").upper()
        out[tk] = out.get(tk, 0.0) + v / nav
    return out


def apply_holds(targets: dict[str, float], active_holds: dict[str, dict],
                weights: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """(targets the BUYERS read, weight held back per ticker).

    A held ticker's target becomes min(planned, current weight): no gap, so no
    buy. The difference is returned so the caller can park it in Cash rather
    than hand it to the core. Never RAISES a target and never touches a ticker
    that is not held back.
    """
    eff = dict(targets)
    held: dict[str, float] = {}
    for tk, t in targets.items():
        if tk.upper() in active_holds:
            w = max(0.0, float(weights.get(tk.upper(), 0.0)))
            if w < float(t):
                eff[tk] = w
                held[tk.upper()] = float(t) - w
    return eff, held
