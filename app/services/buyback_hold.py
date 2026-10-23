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

Stored as `trading_rules` rows of type `buy_back` (PR 83; level = buy-back price), so
Holdings › Rules lists, edits and ends them. The legacy `kv_settings` blob
(`buyback_holds:<email>`) is migrated into rows on first read.
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


# PR 139 -- A STOP-OUT IS NOT BOUGHT BACK BY THE APP.
#
# Measured on production, 2026-10-12 .. 10-22: SOXL was stopped out at 130.43,
# re-bought by sleeve funding at 127.57 and 110.54, stopped out again at 93.96,
# re-bought at 98.65 -- above that sale -- and was 82.11 on 10-22. Neither exit
# recorded a hold, and the one hold that existed (above) ends the moment the
# price is below the sale, which in a falling market is at once.
#
# A STOP hold has no price that ends it. It ends when the sleeve that owns the
# ticker has a signal that went OUT after the exit and then came back IN (the
# strategy's own re-entry, measured by the 06:15 job), or when you end it on
# Holdings > Rules. A ticker no sleeve owns has no signal, so only you end it.
STOP = "stop"
PROTECTIVE_EXITS = frozenset({"stop_loss", "trailing_stop", "profit_protect", "trend_break"})
SIGNAL_OFF = 0.005   # a target weight at or under this is "out" (`rules_service._signal_side`)


def is_stop(h: dict | None) -> bool:
    return bool(h) and h.get("kind") == STOP


def _params_of(v: dict) -> dict:
    return {k: v.get(k) for k in ("shares", "strategy_id", "rule_id", "until_rule", "at",
                                  "kind", "exit_rule_type", "signal_out", "signal_out_at")
            if v.get(k) is not None}


def _note_for(tk: str, v: dict) -> str:
    if is_stop(v):
        what = str(v.get("exit_rule_type") or "stop").replace("_", " ")
        if v.get("strategy_id"):
            return (f"Holds {tk} after your {what} sold it at {float(v.get('level') or 0):,.2f}: "
                    f"no re-buy until {v['strategy_id']}'s signal is out and back in")[:160]
        return (f"Holds {tk} after your {what} sold it at {float(v.get('level') or 0):,.2f}: "
                f"no re-buy until you end this hold")[:160]
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
    if is_stop(prev):
        # PR 139: a later sale never turns a stop hold back into a price hold.
        data[tk] = {**prev, "shares": round(float(prev.get("shares") or 0) + float(shares), 6)}
        await _save(session, user, data)
        return data[tk]
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
        # PR 139: a stop hold is never ended by a price (see STOP above).
        if is_stop(h) or now is None or now >= float(h.get("level") or 0):
            out[tk] = {**h, "price_now": now}
    out = with_group(out)       # PR 141: every buyer that reads holds reads the group
    for tk, h in out.items():
        if h.get("via"):
            out[tk] = {**h, "price_now": px.get(tk)}
    return out


def with_group(holds: dict[str, dict]) -> dict[str, dict]:
    """PR 141 (D116): a stop hold on a 2x/3x fund holds EVERY 2x/3x fund.

    10-22: a trailing stop sold SOXL; the same morning Fund-my-sleeves funded the
    TQQQ sleeve and the held-cash card put TQQQ first among the places to invest
    the reserve. The two are one bet on the day a stop fires (0.95 daily-return
    correlation over the month before; together down on 8 of 8 sessions SOXL fell
    more than 3%), so "do not buy back SOXL" was a promise the app kept for one
    ticker and broke for the bet.

    Each added entry carries `via` (the ticker whose stop holds it) and that
    hold's rule id: ending the SOXL hold ends the block. Nothing is stored; the
    group is derived on every read, so it can never outlive its source.
    """
    from app.services.strategy_profile import GEARED
    src = sorted(tk for tk, h in holds.items() if is_stop(h) and not h.get("via") and tk in GEARED)
    if not src:
        return holds
    out = dict(holds)
    for tk in sorted(GEARED):
        if tk not in out:
            out[tk] = {**holds[src[0]], "via": src[0], "price_now": None}
    return out


def stop_held(holds: dict[str, dict]) -> dict[str, dict]:
    """Only the stop holds, and the 2x/3x funds they block (PR 141). These bind
    EVERY buyer, the Plan screen included."""
    return with_group({tk: h for tk, h in holds.items() if is_stop(h)})


def refusal(ticker: str, h: dict) -> str:
    """One sentence a refused buy shows, naming the stop."""
    what = str(h.get("exit_rule_type") or "stop").replace("_", " ")
    tail = (f"until {h['strategy_id']}'s signal is out and back in" if h.get("strategy_id")
            else "until you end the hold")
    if h.get("via"):
        return (f"{ticker} is a 2x/3x fund, and your {what} sold {h['via']} (also 2x/3x) at "
                f"{float(h.get('level') or 0):,.2f}: the app will not buy any 2x/3x fund {tail}. "
                f"End the {h['via']} hold on Holdings > Rules to buy it now.")
    return (f"{ticker} is on hold since your {what} sold it at {float(h.get('level') or 0):,.2f}: "
            f"the app will not buy it back {tail}. End the hold on Holdings > Rules to buy it now.")


class HeldBack(Exception):
    """A buy reached the book writer for a ticker under a stop hold."""


async def assert_buyable(session: AsyncSession, user: User, tickers) -> None:
    """Raise HeldBack if any of `tickers` is under a stop hold.

    Called by each executor BEFORE its first write: position writers commit as
    they go, so a refusal raised after a sale would leave the sale done and the
    buy refused. Raised here, nothing has been written and Accept answers
    "nothing was changed".
    """
    want = {str(t).upper() for t in (tickers or []) if t}
    if not want:
        return
    held = stop_held(await load(session, user))
    for tk in sorted(want & set(held)):
        raise HeldBack(refusal(tk, held[tk]))


async def record_stop(session: AsyncSession, user: User, *, ticker: str, sold_price: float,
                      shares: float, exit_rule_type: str, rule_id: str | None) -> dict:
    """Hold a ticker a protective rule just sold.

    The owning sleeve is the one whose CATALOGUE weights hold the ticker (not its
    live target: a trend sleeve that is out holds its base, and would look like
    no owner at exactly the moment it matters). If that sleeve's signal is
    already out, the hold starts as out, so the next re-entry releases it.
    """
    from app.services import strategy_catalog
    from app.services.sleeve_service import list_sleeves, live_sleeve_targets
    tk = ticker.upper()
    owner = None
    for s in await list_sleeves(session, user):
        w = (strategy_catalog.get(s.strategy_id) or {}).get("weights") or {}
        if tk in {k.upper() for k in w}:
            owner = s.strategy_id
            break
    out_now = False
    if owner:
        # NO READING IS NOT "OUT". With no stored signal for the sleeve yet, an
        # unknown target read as weight 0 would mark the hold out, and the first
        # reading of "in" would end it the next morning -- re-entry without the
        # signal ever having left. Only a measured target can say "out".
        row = (await live_sleeve_targets(session, user)).get(owner)
        if row is not None:
            tgt = row.get("target") or {}
            out_now = float({k.upper(): v for k, v in tgt.items()}.get(tk, 0.0) or 0.0) <= SIGNAL_OFF
    data = await load(session, user)
    prev = data.get(tk) or {}
    now = datetime.now(timezone.utc).isoformat()
    data[tk] = {"level": round(float(sold_price), 6), "kind": STOP,
                "shares": round(float(shares) + float(prev.get("shares") or 0), 6),
                "strategy_id": owner, "rule_id": rule_id, "exit_rule_type": exit_rule_type,
                "signal_out": bool(out_now), **({"signal_out_at": now} if out_now else {}),
                "at": now}
    await _save(session, user, data)
    return data[tk]


async def step_signal(session: AsyncSession, user: User, strategy_id: str,
                      target: dict) -> list[str]:
    """Advance the stop holds a sleeve owns with today's signal. Returns released tickers.

    Out (weight at or under SIGNAL_OFF) marks the hold; in, after an out, ends it.
    Called by the 06:15 signal job after it stores the target, so a release is a
    measured re-entry, never a guess between evaluations.
    """
    holds = await load(session, user)
    tgt = {k.upper(): float(v or 0.0) for k, v in (target or {}).items()}
    changed, gone = False, []
    for tk, h in list(holds.items()):
        if not is_stop(h) or h.get("strategy_id") != strategy_id:
            continue
        w = tgt.get(tk, 0.0)
        if w <= SIGNAL_OFF:
            if not h.get("signal_out"):
                holds[tk] = {**h, "signal_out": True,
                             "signal_out_at": datetime.now(timezone.utc).isoformat()}
                changed = True
        elif h.get("signal_out"):
            holds.pop(tk)
            gone.append(tk)
            changed = True
    if changed:
        await _save(session, user, holds)
    return gone


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
