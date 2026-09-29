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


# PR 83 -- A HOLD IS A RULE YOU CAN SEE. Eran, 2026-09-29: "I don't see any rule
# created to hold reserve money for buy-back in different price". Holds lived
# in `kv_settings`, invisible and uneditable. They are now `trading_rules` rows
# of type `buy_back` (level = the buy-back price), so the Rules tab lists them,
# the edit button moves the price and the delete button ends the hold. The KV
# blob is migrated into rows the first time it is read. The API below is
# unchanged, so every buyer that reads holds is untouched.
BUY_BACK = "buy_back"


async def _rows(session: AsyncSession, user: User) -> list:
    from sqlalchemy import select

    from app.models.tables import TradingRule
    return list((await session.scalars(select(TradingRule).where(
        TradingRule.subject == user.email, TradingRule.rule_type == BUY_BACK,
        TradingRule.active.is_(True)))).all())


async def _migrate_kv(session: AsyncSession, user: User) -> None:
    row = await session.get(KVSetting, _key(user))
    if not row:
        return
    try:
        data = json.loads(row.value)
    except Exception:  # noqa: BLE001
        data = {}
    have = {r.ticker.upper() for r in await _rows(session, user)}
    from app.models.tables import TradingRule
    for tk, v in (data.items() if isinstance(data, dict) else []):
        if str(tk).upper() in have or not isinstance(v, dict):
            continue
        session.add(TradingRule(subject=user.email, ticker=str(tk).upper(), rule_type=BUY_BACK,
                                mode="price", level=float(v.get("level") or 0.0),
                                params=_params_of(v), note=_note_for(str(tk).upper(), v)))
    await session.delete(row)
    await session.commit()


def _params_of(v: dict) -> dict:
    return {k: v.get(k) for k in ("shares", "strategy_id", "rule_id", "until_rule", "at")
            if v.get(k) is not None}


def _note_for(tk: str, v: dict) -> str:
    if v.get("until_rule"):
        return f"Holds {tk} re-buys until your drawdown guard re-arms"
    return f"Holds {tk} re-buys until it trades below {float(v.get('level') or 0):,.2f}"


async def load(session: AsyncSession, user: User) -> dict[str, dict]:
    await _migrate_kv(session, user)
    out: dict[str, dict] = {}
    for r in await _rows(session, user):
        p = r.params if isinstance(r.params, dict) else {}
        out[r.ticker.upper()] = {"level": float(r.level or 0.0), **p,
                                 "hold_rule_id": str(r.id)}
    return out


async def _save(session: AsyncSession, user: User, data: dict) -> None:
    from app.models.tables import TradingRule
    rows = {r.ticker.upper(): r for r in await _rows(session, user)}
    for tk, r in rows.items():
        if tk not in data:
            await session.delete(r)
    for tk, v in data.items():
        r = rows.get(tk.upper())
        if r is None:
            session.add(TradingRule(subject=user.email, ticker=tk.upper(), rule_type=BUY_BACK,
                                    mode="price", level=float(v.get("level") or 0.0),
                                    params=_params_of(v), note=_note_for(tk.upper(), v)))
        else:
            r.level = float(v.get("level") or 0.0)
            r.params = _params_of(v)
            r.note = _note_for(tk.upper(), v)
            r.triggered = False
    await session.flush()


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


async def release(session: AsyncSession, user: User, tickers) -> list[str]:
    """End the holds on these tickers now (PR 83: you chose to buy back or
    invest the reserved cash elsewhere)."""
    want = {str(t).upper() for t in (tickers or [])}
    holds = await load(session, user)
    gone = sorted(k for k in holds if k in want)
    if gone:
        await _save(session, user, {k: v for k, v in holds.items() if k not in gone})
    return gone


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
    """PR 83: a no-op. A retreated hold is no longer deleted in silence: its
    `buy_back` rule FIRES (the 30-minute evaluator), which pushes an alert and a
    buy-back card (Eran's decision). `active` already ignores a retreated hold,
    so the reserve is freed the moment the price is below the level."""
    return []


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
