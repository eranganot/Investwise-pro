"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.offload import offload
from app.engines.allocation_engine import AllocationEngine
from app.models.tables import KVSetting, User
from app.services.allocation_mix import OBJ_TARGET, classify, classify_holding, current_mix
from app.services.book_target import explain as bt_explain
from app.services.audit_trail import audit_for, f
from app.services.audit_trail import f as _fml  # alias: 'f' is shadowed by Fundamentals locals below
from app.agents.fee_agent import FeeAgent
from app.engines.backtest_engine import BacktestEngine
from app.services import objective_weights as _obj_weights
from app.services.intake_service import (
    credit_cash, delete_position, list_positions, update_position,
)
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot, tax_opportunities

logger = logging.getLogger(__name__)

CLASS_ETF = {"Equities": "VTI", "Fixed Income": "BND", "Cash": "BIL",
             "Commodities": "DBC", "Real Estate": "VNQ", "Alternatives": "BTAL"}

_SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# How far the sleeve may wander from the size you chose before Today says so.
# Wide enough that ordinary price movement does not nag; narrow enough that a
# sleeve quietly becoming something else gets noticed.
SLEEVE_DRIFT_BAND_PCT = 5.0

# Apply kinds that genuinely mutate holdings/plan/rules. Anything else is advice
# the app can't execute for you -- Accept on those used to fall through every
# branch, report "Done -- applied." and silently dismiss the card, so guidance
# looked like it had been carried out when nothing had happened.
_ACTIONABLE_KINDS = {"trim", "sell_losers", "fee_swap", "rebalance_to_objective",
                     "set_objective_and_rebalance", "set_plan", "create_rule", "create_rules",
                     "buy_funded", "sell_position", "redeploy_cash", "fund_sleeve",
                     "fund_plan", "relevel_sleeve_caps", "core_to_basket"}


async def _offload(fn, *args, **kwargs):
    """Run one blocking agent on a worker thread (#15, Phase B).

    Offloading a whole agent rather than each individual provider call is
    deliberate. The call sites are ordinary sync functions, so awaiting inside
    them would mean turning ten service modules async to buy the same thing:
    the loop is released for the entire blocking stretch either way, and this
    version does not thread `async` through code that moves money.

    ORM SAFETY -- the sharp edge here. Several of these agents take `rows`,
    which are live ORM objects, and touching an EXPIRED attribute from a worker
    thread would try to lazy-load through the async session and raise
    MissingGreenlet. Two existing decisions make that safe, and both are
    load-bearing rather than lucky:

      * AsyncSessionLocal sets `expire_on_commit=False`, so a commit mid-build
        does not expire the positions.
      * `_agent_tx` uses a SAVEPOINT precisely because `session.rollback()`
        expires everything -- its docstring below already records that lesson.

    tests/test_offload.py asserts nothing is unloaded at this boundary, so if
    either decision is ever reversed it fails there rather than as an
    intermittent 500 in production.
    """
    return await offload(fn, *args, **kwargs)


def _agent_tx(session: AsyncSession):
    """A SAVEPOINT around one agent, so its failure cannot kill the request.

    Every "defensive" handler here catches an agent's failure, logs it, marks
    the pipeline degraded and carries on with the SAME session. SQLite tolerates
    that -- which is why the entire local suite was green -- but Postgres does
    not: the first failed statement ABORTS the transaction, and every query
    after it raises InFailedSQLTransactionError. Observed in production as a 500
    from /recommendations, with load_dismissed dying on "current transaction is
    aborted" long after the agent that actually broke.

    The obvious repair, session.rollback(), is worse: rollback EXPIRES every
    loaded ORM object, so the positions this function keeps using would each
    trigger a lazy reload and raise MissingGreenlet instead. A savepoint undoes
    only the failed agent's own statements and leaves both the outer transaction
    and the identity map intact.
    """
    return session.begin_nested()


def _is_actionable(rec: dict) -> bool:
    return ((rec.get("apply") or {}).get("kind") or "none") in _ACTIONABLE_KINDS


class _Stopwatch:
    """Per-agent timings for /recommendations.

    The endpoint took 24.2s, then ~5s after the provider cache fix, and there
    was no way to see WHICH of the ~10 agents was responsible -- so the next
    step would have been another guess. Timings ship in the response so the cost
    is attributable instead of inferred.
    """

    def __init__(self) -> None:
        self.marks: dict[str, int] = {}
        self._last = time.perf_counter()

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.marks[name] = int((now - self._last) * 1000)
        self._last = now

    def slowest(self, n: int = 3) -> list:
        return sorted(self.marks.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _rid(*parts) -> str:
    return "rec_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


# Multiples of `allocation_drift_threshold` at which a rebalance stops being
# routine. The threshold is the number that DECIDED this card should exist, so
# it is also the honest unit for how far past it the book has gone.
_DRIFT_HIGH_X = 3.0
_DRIFT_CRITICAL_X = 6.0


def _drift_severity(current: float, target: float) -> str:
    """How far past its own trigger this drift is, in multiples of the trigger.

    This card hardcoded "MEDIUM" -- so a book 3.1 points out of target and a
    book 20.4 points out rendered identically, as "nice to have". Four other
    card types in this file already scale by magnitude (behind-goal at
    CRITICAL >= 40, benchmark lag at HIGH <= -10, tax harvest, holding verdict);
    the one card that reports a measured gap did not.

    The multiple, not a fresh set of absolute cut-offs: `allocation_drift_threshold`
    is already the setting that decides a drift is worth a card at all, and a
    second independent scale would be a second opinion about what "drifted"
    means. At the default 3%, 3x is 9 points and 6x is 18.
    """
    from app.core.config import get_settings
    thr = float(get_settings().allocation_drift_threshold or 0.03)
    if thr <= 0:
        return "MEDIUM"
    drift = abs(float(current) - float(target))
    ratio = drift / thr
    # `0.59 - 0.50` is 0.08999999999999997, so an exactly-3x drift divides to
    # 2.9999999999999996 and lands one tier low. The tolerance is about binary
    # representation, not about judgement: a card whose severity depends on
    # which two floats the caller happened to subtract is not reproducible.
    eps = 1e-9
    if ratio >= _DRIFT_CRITICAL_X - eps:
        return "CRITICAL"
    if ratio >= _DRIFT_HIGH_X - eps:
        return "HIGH"
    return "MEDIUM"


async def _funding_exclusions(session, user, btgt: dict | None) -> set[str]:
    """Tickers no Today card may sell to fund a purchase.

    Sleeves were the whole of this set. The core was not, and on 2026-09-03 the
    consequence was on the screen: with the book exactly on its target and
    nothing overweight, `plan_funding`'s fallback funded a commodity idea by
    proposing to sell **6.7347 BND -- ILS 1,465 of a ILS 2,263 position, 65% of
    it** -- which would have taken Fixed Income from 10.8% to 3.8% against an 11%
    target. The card said, correctly after PR 42, that this "corrects no drift".
    Saying so is not the same as declining to do it.

    PR 37 settled that a core is a strategy too, and PR 41 already applied that
    to the tax-harvest exclusion. This applies the same rule to funding, which is
    the last place that treated the core as spare change. The core is not
    frozen: `core_to_basket` (PR 39) exists precisely to trade it, sized by one
    plan, and that is the path that may touch it.

    Failures are logged and degrade to the sleeves alone rather than to nothing:
    a missing core basket must not silently widen what is sellable.
    """
    names: set[str] = set()
    try:
        from app.services import sleeve_service as _sv
        names |= await _sv.sleeve_tickers(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("sleeve tickers unavailable; card funding will not exclude them",
                       exc_info=True)
    try:
        from app.services.strategies import core_weights as _core_weights
        names |= {(t or "").upper()
                  for t in (_core_weights((btgt or {}).get("core_strategy_id")) or {})}
    except Exception:  # noqa: BLE001
        logger.warning("core basket unavailable; card funding will not exclude it",
                       exc_info=True)
    return names


async def _book_target(session, user, objective: str) -> dict:
    """THE target mix for the WHOLE book, sleeves included. One answer.

    Was `_core_target`, and the rename is the fix. That function returned the
    mix of the strategy managing the CORE -- 36% of this book -- and both
    callers handed it to `AllocationEngine.compute` against the whole book's
    current mix and the whole NAV. Every leg came out overstated by 1/0.36:

        the card said            correct for a 36% core
        Sell Equities  12,139    4,324
        Buy Fixed Inc   8,469    3,054
        Buy Commodities 5,293    1,908

    And it got worse the day a core was chosen, which is the opposite of what
    choosing one should do. Before that the fallback was `OBJ_TARGET[objective]`
    -- Grow, 80/10/10 -- which at least reads as a whole-book target, so the
    error looked like ordinary drift. Choosing All-Weather (35/40/25) turned a
    3,600 equity sale into a 12,139 one, on a book whose sleeves are 64% equity
    BY DESIGN and cannot be sold without abandoning the strategies.

    `book_target` adds the two: the sleeves' own per-ticker targets classified
    and summed, plus the core's mix scaled to the share the sleeves left. It
    carries its provenance, so a card can say 77% is 64 points of sleeve plus
    13 of core rather than asserting 77% and hoping.
    """
    from app.services.book_target import book_target

    return await book_target(session, user, objective)


def _ils(x) -> str:
    return f"₪{round(x):,}"


def _unrealized_ils(rows, only: list[str] | None = None, sign: int = -1) -> float:
    """Unrealized P/L in SHEKELS for the named holdings, one side only.

    ``sign=-1`` sums losses and returns them positive; ``sign=+1`` sums gains.

    FX-converted per holding, because ``(price - cost) x qty`` is in the
    holding's TRADING currency: SOXL at $106.35 against a $111.24 basis is a
    $113 loss, and adding that to a shekel total as though it were ILS 113 is
    the error this app has been bitten by twice. ``compute_snapshot`` does the
    same conversion; this exists so a card can ask about the subset of holdings
    it is actually proposing to sell.
    """
    from app.services.fx import fx_rate, price_currency

    want = {(t or "").upper() for t in (only or [])} if only is not None else None
    total = 0.0
    for p in rows:
        if want is not None and (p.ticker or "").upper() not in want:
            continue
        meta = p.meta if isinstance(p.meta, dict) else None
        rate = fx_rate(price_currency(p.market, meta)) or 1.0
        pl = ((float(p.current_price or 0.0) - float(p.cost_basis or 0.0))
              * float(p.quantity) * rate)
        if sign < 0 and pl < 0:
            total += -pl
        elif sign > 0 and pl > 0:
            total += pl
    return round(total, 2)


# Plain-language fallbacks so every card explains *why* it fired and its *impact*,
# even for the agent-built cards that don't set these fields explicitly. Kept
# qualitative on purpose — the advisor never invents numbers it hasn't computed.
_DIM_WHY = {
    "diversification": "Too much of your portfolio rides on a single position, region or currency.",
    "tax": "You're holding position(s) where a tax move could work in your favor.",
    "allocation": "Your current mix has drifted from your plan's target.",
    "goal": "On the current path your projection falls short of your goal.",
    "fees": "You're paying more in fund fees than a comparable cheaper fund would cost.",
    "macro": "The market backdrop has shifted, changing your near-term risk.",
    "liquidity": "A large share of your book may be hard to sell quickly.",
    "risk": "A risk metric is outside your comfort band.",
}
_DIM_IMPACT = {
    "diversification": "Spreads your risk so no single holding can dominate your outcome.",
    "tax": "Improves your after-tax return.",
    "allocation": "Realigns your mix with your plan.",
    "goal": "Moves you closer to reaching your goal on time.",
    "fees": "Keeps more of your return instead of paying it away in fees.",
    "macro": "Reduces your exposure to a near-term drawdown.",
    "liquidity": "Improves how quickly you could raise cash if you need to.",
    "risk": "Brings a risk measure back into your comfort band.",
}


def _ensure_why_impact(recs: list[dict]) -> list[dict]:
    """Guarantee every recommendation carries a 'why', an 'impact', and an
    'audit_trail' (the invariant every card must satisfy)."""
    for r in recs:
        dim = r.get("dimension", "")
        r.setdefault("apply", {"kind": "none"})
        r["actionable"] = _is_actionable(r)
        if not r.get("why"):
            r["why"] = _DIM_WHY.get(dim, "This helps your portfolio track your plan.")
        if not r.get("impact"):
            r["impact"] = _DIM_IMPACT.get(dim, "Moves your portfolio closer to your plan.")
        if not r.get("audit_trail"):
            rd: dict = {"reason": r.get("why") or r.get("action") or r.get("title") or dim,
                        "severity": r.get("severity")}
            if r.get("est_amount") is not None:
                rd["est_amount"] = r["est_amount"]
            r["audit_trail"] = audit_for(dim, raw_data=rd, formulas=[
                f("Basis", f"surfaced when the {dim or 'portfolio'} signal crosses its threshold",
                  substituted=(r.get("why") or "")[:160],
                  result=r.get("impact") or r.get("severity") or "flagged")])
    return recs


# --- Server-side dismissals (so the Today list and push notifications agree) ---
# An "Ignore" lasts up to this many days; if the recommendation is still relevant
# after that, it resurfaces (and can notify again).
_DISMISS_TTL_DAYS = 7
# "Done" is a stronger statement than "ignore", so it suppresses for longer -- but
# NOT forever. A permanent hide-list is exactly what caused the original
# notification/Today mismatch (push kept firing for cards the app had buried), so
# completed items still age out, and the UI shows the count with its own restore.
#
# 90 days was far too long, and the reason is the ID rather than the number: a
# card's id is sha1(category|ticker), with no amount and no date in it, so the
# card you complete today and the card the same condition generates tomorrow
# are THE SAME id. On 2026-08-20/21 five Accepts muted the tax card, the
# sell-Equities card and the SOXL cold-start card until mid-November -- while
# /mix went on asking to sell 5,078 of Equities and /health-check went on
# flagging an unrealised loss as HIGH. 14 days caps the damage; making the id
# carry its own content is the actual fix and is a separate change.
_DONE_TTL_DAYS = 14


#: How far a card's own number has to move before "done" stops applying to it.
#: Big enough that ordinary drift does not un-hide a card you just filed; small
#: enough that a materially different ask reaches you.
_REFIRE_DELTA = 0.25
#: ...and below this a ratio means nothing. A card that asked for 40 ILS and now
#: asks for 55 has not changed situation, it has rounded.
_REFIRE_FLOOR_ILS = 100.0

_SEV_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

#: PR 38. How long a "done" gets the benefit of the doubt before the book is
#: allowed to contradict it. An order placed on Friday settles on Tuesday, a
#: transfer takes days, and a card reappearing the same afternoon to say "this
#: did not happen" would be wrong most of the time and rude the rest. Three days
#: is long enough for a real trade to reach the positions and short enough that
#: something meant and forgotten comes back the same week.
_DONE_SETTLE_DAYS = 3


def moved_on(entry, card: dict, *, done: bool = False) -> str | None:
    """Why this card's mute no longer applies, or None if it still does.

    THE DEFECT THIS ANSWERS. A card's id is `sha1(category|ticker)` -- no
    amount, no date -- so the card you file today and the card the same
    condition generates next week are THE SAME id, and the mute lands on both.
    Measured on this book, 2026-09-02: `/mix` wanted to sell 1,627 of Equities
    and buy 2,244 of Fixed Income, `/recommendations` returned four screener
    cards, and `dismissed_count: 4, completed_count: 5` -- nine cards muted,
    the rebalance pair among them, for a fortnight, however far the drift moved
    afterwards.

    The id is deliberately NOT changed. A content-bearing id was the obvious
    fix and it is the worse one: the id is what Restore, the audit log and the
    push dedupe all key on, and it would churn on every boundary crossing --
    1,499 and 1,501 becoming different cards. What changes instead is that the
    mark remembers the SITUATION, and the situation is compared. No boundary,
    no new identity, and the comparison can be read in the reason string.

    Two things un-mute a card:

      * its severity went UP. "I dealt with that" was said about a MEDIUM; the
        app now calls it HIGH, which is new information.
      * its own number moved by more than `_REFIRE_DELTA`. Selling 1,627 of
        equities and selling 260 of them are not the same instruction.

    An old mark (or one whose card carries no amount) has no situation to
    compare, so it keeps the old behaviour exactly: muted until the TTL runs
    out. Nothing needs migrating and nothing silently changes meaning.
    """
    if not isinstance(entry, dict):
        return None
    then_sev, now_sev = entry.get("severity"), card.get("severity")
    if then_sev and now_sev and _SEV_RANK.get(str(now_sev), -1) > _SEV_RANK.get(str(then_sev), -1):
        return (f"you filed it while it was {then_sev}; the app now calls it {now_sev}")
    if entry.get("amount") is None or card.get("est_amount") is None:
        return None
    try:
        then_amt = float(entry["amount"])
        now_amt = float(card["est_amount"])
    except (TypeError, ValueError):
        return None
    base = max(abs(then_amt), _REFIRE_FLOOR_ILS)
    if abs(now_amt - then_amt) / base > _REFIRE_DELTA:
        return (f"it asked for about {then_amt:,.0f} ILS when you filed it "
                f"and asks for about {now_amt:,.0f} ILS now")

    # ------------------------------------------------------------------ #
    # PR 38 -- A "DONE" THE BOOK CONTRADICTS IS NOT DONE.
    #
    # Eran, 2026-09-02: *"if I'm not holding VTI as part of my portfolio -->
    # I expect to get a recommendation card to purchase it."*
    #
    # The card existed. "Buy Equities", MEDIUM, 1,215 ILS -- hidden since
    # 2026-08-21 because he pressed DONE. The equities gap on 2026-09-02 was
    # 1,194 ILS: twelve days later, the same instruction, to within 2%.
    #
    # And the rule above is why it stayed hidden, working exactly as designed.
    # For a card you IGNORED that is right: you looked, you chose not to act,
    # and only a changed situation earns your attention again.
    #
    # FOR A CARD YOU MARKED DONE THE SIGNAL IS INVERTED. "Done" is a claim
    # about the book, and the book can be asked. If the same instruction still
    # stands, unchanged, after the trade would have settled, then the number
    # NOT MOVING is the evidence the action was never taken -- and the mute is
    # resting on something that did not happen. The app took the user's word
    # for it and never checked; nothing else in this file trusts a claim it can
    # measure.
    #
    # Deliberately narrow, because the failure mode is nagging:
    #   * only "done", never "ignored";
    #   * only after `_DONE_SETTLE_DAYS`, so real settlement is not called a lie;
    #   * only for a card asking to move real money (`_REFIRE_FLOOR_ILS`), since
    #     a 40 ILS ask that stays 40 has not proved anything;
    #   * and the card says WHY it is back, in the user's own timeline.
    # ------------------------------------------------------------------ #
    if not done:
        return None
    if abs(now_amt) < _REFIRE_FLOOR_ILS:
        return None
    when = _parse_dt(_at(entry))
    if datetime.now(timezone.utc) - when < timedelta(days=_DONE_SETTLE_DAYS):
        return None
    return (f"you marked this done on {when.date().isoformat()}, and your book "
            f"still shows about {now_amt:,.0f} ILS to move - the same ask, so it "
            f"looks like it did not happen")


def _dismiss_key(user: User) -> str:
    return f"dismissed_recs:{user.email}"


def _done_key(user: User) -> str:
    return f"completed_recs:{user.email}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)  # unparseable -> treat as fresh


def _at(entry) -> str:
    """The timestamp out of a mark, whichever shape it is stored in.

    A mark used to be the bare ISO string. It is now a dict, because "when did
    you hide this" was never enough to answer the question that matters --
    "is this still the same card I hid" -- and the answer needs the card's own
    number alongside the date. Both shapes are read; only the dict is written.
    """
    if isinstance(entry, dict):
        return str(entry.get("at") or "")
    return str(entry or "")


async def _load_map(session: AsyncSession, key: str) -> dict[str, dict]:
    """Return {rec_id: {"at": iso, "amount": float|None, "severity": str|None}}.

    Tolerates both older shapes: a bare list of ids, and {id: iso}. An old mark
    carries no situation, so it behaves exactly as it did before -- muted until
    its TTL runs out. Nothing has to be migrated.
    """
    row = await session.get(KVSetting, key)
    if not row:
        return {}
    try:
        data = json.loads(row.value)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(data, list):  # legacy: ids only -> give each a fresh window
        return {i: {"at": _now_iso()} for i in data}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        out[k] = dict(v) if isinstance(v, dict) else {"at": str(v)}
    return out


async def _active(session: AsyncSession, key: str, ttl_days: int) -> set[str]:
    m = await _load_map(session, key)
    if not m:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    return {rid for rid, e in m.items() if _parse_dt(_at(e)) >= cutoff}


async def _mark(session: AsyncSession, key: str, rec_id: str, ttl_days: int,
                state: dict | None = None) -> None:
    """Record that this card was hidden, and WHAT IT SAID when it was.

    `state` is the card's own situation at the moment the user acted on it --
    its amount and its severity. It is supplied by the caller that had the card
    in hand, because rebuilding the whole Today list on a button press costs
    seconds on the slowest path in the app.

    A wrong or stale `state` can only ever make a card come BACK early (see
    `moved_on`). It can never extend a mute past its TTL. That asymmetry is
    deliberate: the failure mode of trusting it is a card the user sees again,
    not a card they never see.
    """
    m = await _load_map(session, key)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    m = {k: v for k, v in m.items() if _parse_dt(_at(v)) >= cutoff}  # prune expired
    entry: dict = {"at": _now_iso()}
    if isinstance(state, dict):
        if state.get("amount") is not None:
            try:
                entry["amount"] = float(state["amount"])
            except (TypeError, ValueError):
                pass
        if state.get("severity"):
            entry["severity"] = str(state["severity"])[:16]
    m[rec_id] = entry
    payload = json.dumps(m)
    row = await session.get(KVSetting, key)
    if row:
        row.value = payload
    else:
        session.add(KVSetting(key=key, value=payload))
    await session.commit()


async def _clear(session: AsyncSession, key: str, ttl_days: int) -> int:
    m = await _load_map(session, key)
    if not m:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    active = sum(1 for e in m.values() if _parse_dt(_at(e)) >= cutoff)
    row = await session.get(KVSetting, key)
    if row:
        row.value = json.dumps({})
        await session.commit()
    return active


async def load_dismissed(session: AsyncSession, user: User) -> set[str]:
    """Cards the user chose to IGNORE (7-day window, restorable)."""
    return await _active(session, _dismiss_key(user), _DISMISS_TTL_DAYS)


async def load_completed(session: AsyncSession, user: User) -> set[str]:
    """Cards the user marked as DONE (``_DONE_TTL_DAYS`` window, tracked separately).

    Ignore and Done mean different things: "not now" vs "handled". Filing them in
    one bucket made "Mark as done" look identical to "Ignore" -- the card landed
    in the ignored list, which is what the user reported.
    """
    return await _active(session, _done_key(user), _DONE_TTL_DAYS)


async def _resolve_if_rule_card(session: AsyncSession, user: User, rec_id: str,
                                outcome: str) -> None:
    """Rule cards carry their rule's id in the card id (``rule_<8 hex>``).

    A 7-day dismissal hides the *card*, but the rule itself stayed latched
    ``triggered`` forever — so the red "N trading rules triggered" banner, which
    reads the rules directly, kept counting work the user had already dealt with.
    """
    if not rec_id.startswith("rule_"):
        return
    from app.services.rules_service import resolve_rule
    try:
        await resolve_rule(session, user, rec_id[5:], outcome)
    except Exception:  # noqa: BLE001 -- hiding the card must never fail on this
        logger.warning("could not resolve rule for %s", rec_id, exc_info=False)


async def dismiss_recommendation(session: AsyncSession, user: User, rec_id: str,
                                 state: dict | None = None) -> None:
    await _mark(session, _dismiss_key(user), rec_id, _DISMISS_TTL_DAYS, state)
    await _resolve_if_rule_card(session, user, rec_id, "dismissed")


async def complete_recommendation(session: AsyncSession, user: User, rec_id: str,
                                  state: dict | None = None) -> None:
    await _mark(session, _done_key(user), rec_id, _DONE_TTL_DAYS, state)
    await _resolve_if_rule_card(session, user, rec_id, "acknowledged")


async def restore_dismissed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user ignored (does not touch completed cards)."""
    return await _clear(session, _dismiss_key(user), _DISMISS_TTL_DAYS)


async def restore_completed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user marked done."""
    return await _clear(session, _done_key(user), _DONE_TTL_DAYS)



def _reconcile(recs: list[dict], market: dict | None = None) -> list[dict]:
    """Collapse duplicate advice and drop cards that contradict each other.

    The Today list is assembled from ~10 independent agents that never consult
    one another, so a real portfolio produced, simultaneously: "Sell Cash" AND
    "Buy Equities" (two legs of one rebalance, both applying the identical
    action), "Put idle cash to work" (a third card for the same surplus), and
    "Markets look risk-off" telling the user to *raise* cash 5-10% while the
    other three told them to spend it. Acting on all four was impossible.

    Rules, in order:
      1. Merge multi-leg rebalance cards into one card for the whole move.
      2. Geographic and currency concentration collapse into one card when they
         describe the same exposure (a US-only book is also a USD-only book).
      3. Cash-drag is dropped when a rebalance already redeploys the cash.
      4. Risk-off vs deploy-cash: the allocation cards win (they are actionable
         and plan-derived); the macro card is reworded from "raise cash" to a
         timing note so the two no longer point in opposite directions.
    """
    market = market or {}
    by_id = {r.get("id"): r for r in recs}
    drop: set[str] = set()

    # 1) One rebalance, one card.
    rebal = [r for r in recs if (r.get("apply") or {}).get("kind") == "rebalance_to_objective"]
    if len(rebal) > 1:
        # Titles AND amounts captured before anything is mutated. `keep` IS
        # `rebal[0]`, so writing `keep["title"]` rewrites the first leg in place
        # -- and the action string built afterwards read the mutated list, so
        # the merged card listed itself as one of its own legs: "One rebalance
        # covers the whole move: Rebalance toward your target mix (~7,696);
        # Sell Equities (~5,694)". The first leg's real name was gone, which is
        # the one thing this card exists to tell you.
        legs = [(r["title"], abs(r.get("est_amount") or 0)) for r in rebal]
        # THE LEGS, AS DATA, ON THE CARD THAT ABSORBS THEM.
        #
        # Everything below rewrites the merged card's title, action and why into
        # prose about a move it no longer describes structurally: after this
        # block `apply.asset_class` names whichever leg sorted first, and the
        # other legs exist only as strings in `meta.merged_legs`. So a card that
        # sells Equities AND buys Commodities presented itself to every consumer
        # as one card about Equities.
        #
        # `contradictions.py` rule 2 is that consumer, and it could not see the
        # sell leg (the title no longer starts with "Sell") or a second class at
        # all. That is how "Rebalance toward your target mix" sold ~1,071 of
        # Equities beside a card buying ~3,139 of SOXL, which IS Equities, with
        # nothing noticing.
        structural_legs = [{"asset_class": (r.get("apply") or {}).get("asset_class"),
                            "direction": ((r.get("apply") or {}).get("direction")
                                          or "").lower() or None,
                            "amount_ils": abs(r.get("est_amount") or 0)}
                           for r in rebal]
        keep = rebal[0]
        # THE MERGED CARD GETS ITS OWN ID, built from every leg it absorbed.
        #
        # It used to keep `rebal[0]`'s id -- and `rebal[0]` is whichever leg
        # sorted first, on this book "SELL Equities". So completing a card whose
        # title said SELL also filed the BUY Commodities leg riding inside it,
        # and the user reported never having dismissed a buy card because he
        # never saw one. He was right. Two legs merged into one card must
        # produce one id that is neither leg's, or accepting the merge silently
        # retires something else.
        keep["id"] = _rid("rebal_merged", *sorted(
            (r.get("apply") or {}).get("asset_class")
            or r.get("title", "") for r in rebal))
        keep["title"] = "Rebalance toward your target mix"
        keep["action"] = ("One rebalance covers the whole move: "
                          + "; ".join(f"{t} (~{_ils(amt)})" for t, amt in legs) + ".")
        keep["why"] = ("Your mix has drifted from your plan's target across more than one asset "
                       "class. These move together, so they're one decision, not several.")
        # THE MERGED CARD TAKES THE WORST LEG'S SEVERITY, not the first leg's.
        #
        # This block rewrote the merged card's id, title, action, why and meta
        # and never touched its severity, so `keep` silently kept `rebal[0]`'s
        # -- and `rebal[0]` is whichever leg sorted first, which has nothing to
        # do with which leg is worst. It was latent only because every leg was
        # hardcoded MEDIUM; the moment `_drift_severity` made them differ, a
        # CRITICAL leg merged under a MEDIUM one would have been demoted by
        # sort order. Fixing the severity scale without this would have
        # introduced the bug it was meant to fix.
        _rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        keep["severity"] = max((r.get("severity") or "MEDIUM" for r in rebal),
                               key=lambda s: _rank.get(s, 1))
        keep["meta"] = {**(keep.get("meta") or {}), "merged_legs": [t for t, _ in legs]}
        # `asset_class` is left in place for existing consumers, but it is now
        # the FIRST leg's class rather than the card's, and `legs` is the honest
        # answer. Anything deciding what this card does to a class reads `legs`.
        keep["apply"] = {**(keep.get("apply") or {}), "legs": structural_legs}
        for r in rebal[1:]:
            drop.add(r["id"])

    # 2) Region and currency concentration are usually the same fact twice.
    geo, cur = by_id.get(_rid("divrisk", "geo")), by_id.get(_rid("divrisk", "cur"))
    if geo and cur:
        geo["title"] = "Diversify beyond one region and currency"
        # The merge used to REPLACE the action with generic prose. When the geo
        # card is a sized, funded buy that left a card reading "spread new money
        # across other regions" while its `apply` spec sold 37 SCHD to buy VXUS
        # -- the text describing one thing and Accept doing another, which is the
        # whole defect this branch exists to remove. The merged point is a
        # PREFIX now; the sized action keeps the last word because it is the one
        # that matches the button.
        _merged_why = ("Most of your money sits in a single region *and* the currency that goes "
                       "with it, so one country's downturn hits you twice.")
        if (geo.get("apply") or {}).get("kind") == "buy_funded":
            geo["action"] = _merged_why + " " + geo["action"]
        else:
            geo["action"] = (_merged_why + " Spread new money across other regions to fix both "
                             "at once.")
        geo["meta"] = {**(geo.get("meta") or {}), "merged": ["geographic", "currency"]}
        drop.add(cur["id"])

    # 3) A rebalance that buys into the portfolio already spends the idle cash.
    redeploys_cash = any((r.get("apply") or {}).get("kind") == "rebalance_to_objective"
                         and r["id"] not in drop for r in recs)
    cash_drag = by_id.get(_rid("cashdrag"))
    if cash_drag and redeploys_cash:
        drop.add(cash_drag["id"])

    # 4) Don't tell someone to raise and spend cash in the same breath.
    macro = by_id.get(_rid("macro", "riskoff"))
    if macro and redeploys_cash and macro["id"] not in drop:
        macro["title"] = "Markets look risk-off — phase the rebalance in"
        macro["action"] = (f"The market backdrop is risk-off ({market.get('rationale', '')}). "
                           "That's not a reason to abandon the rebalance above, but it is a reason "
                           "to phase it in over a few weeks rather than all at once.")
        macro["why"] = ("A risk-off backdrop raises the odds of a near-term drawdown, so the timing "
                        "of a large purchase matters more than usual.")
        macro["impact"] = "Spreads entry risk without leaving you off-plan."
        macro["how"] = ["Split the rebalance into 2-4 tranches over a few weeks",
                        "Start with the broadest, least volatile leg",
                        "Keep your cash floor intact while you phase in"]

    return [r for r in recs if r.get("id") not in drop]


# The three causes this card can name, most-binding first. A book can be in more
# than one of these states at once, and the order is not cosmetic: a ceiling
# below what the book already does BOUNDS every finding underneath it, so
# reporting the core or the sleeves first would be answering a question the
# reader cannot act on yet.
_CAUSE_CEILING = "ceiling"
_CAUSE_SLEEVES = "sleeves"
_CAUSE_CORE = "core"


def _ceiling_cause(c: dict, found: dict) -> dict:
    """Which of the three is actually binding, and what to say about it.

    1. THE CEILING, when the book already breaches it unchanged. Measured by
       `target_solver`, stored on the verdict. On this book a 13.3% ceiling
       survived from an era when the history provider returned a short window,
       while the same measurement now reads 58.59% over 2,513 sessions -- so
       every "no" for a week was about the number, not the portfolio. Nothing
       under this is worth reading until it is settled.
    2. THE SLEEVES, when T6b measured every candidate core and none clears.
    3. THE CORE, which is the only case where "change the core" is advice a
       measurement supports.
    """
    cc = c.get("ceiling_check") or {}
    if cc.get("known") and cc.get("book_already_breaches"):
        gap = cc.get("gap_pct")
        return {
            "kind": _CAUSE_CEILING,
            "action": (
                "Check your drawdown ceiling before anything else. Your book has "
                f"already measured {cc.get('book_measured_pct')}% over the same window "
                f"against a {cc.get('ceiling_pct')}% ceiling"
                + (f", {gap:.1f} points over" if isinstance(gap, (int, float)) else "")
                + " - so every answer below is bounded by a number the book already "
                  "breaches. The ceiling is on the Target card, next to 'WITHOUT "
                  "EXCEEDING'."),
        }
    if found.get("none_admissible") and found.get("means"):
        return {"kind": _CAUSE_SLEEVES, "action": found["means"]}
    return {"kind": _CAUSE_CORE,
            "action": ("Change the strategy managing your core, or raise the ceiling to a "
                       "number you would actually sit through.")}


def _core_when(found: dict) -> str:
    """How old the core search is, in the same words the constraint uses."""
    age = found.get("age_hours")
    if age is None:
        return "at an unknown time"
    return "just now" if age < 1 else f"{age:.0f}h ago"


async def _core_ceiling_recs(session: AsyncSession, user: User) -> list[dict]:
    """The solver's most consequential finding, which nothing on Today said.

    PR 11 stored the verdict and taught the war room to read it, and the war
    room used it to take a one-tap away. But the verdict's BIGGEST statement is
    not about any single signal: `floor_breaches_ceiling` means the core alone
    breaches the drawdown ceiling, so no sleeve size repairs it -- INCLUDING
    ZERO. On the live book that is 35.1% against a 13.3% ceiling, and Today
    showed five cards, none of which mentioned it. It was reachable only by
    opening the Target card, which is exactly the "nothing said it was missing"
    failure PR 4 existed to end.

    Three properties, each of which is the reason a line of this exists:

      IT NEVER CLAIMS A CHANGE IT DOES NOT MAKE. `apply.kind` is "none".
      Swapping a core is a whole-book change and `target_solver` is read-only
      without exception -- a return target one tap from a book change is the C5
      slider bug with higher stakes, in the solver's own words. So this card
      states a measurement and points at the Plan screen.

      IT CARRIES ITS AGE. Same rule as every other reader of a stored verdict:
      a statement about one book at one moment has to say when that moment was.

      A STALE VERDICT STILL SPEAKS. PR 11's rule is "stale stops blocking, it
      does not stop speaking", and this card blocks nothing, so staleness
      changes the wording and not whether it appears. Hiding a 21-point breach
      because the measurement is 40 hours old would be the failure mode that
      rule was written against, inverted.

    No verdict at all means no card. A book with nothing measured gets silence,
    not an asserted breach -- inventing a finding is worse than missing one.
    """
    out: list[dict] = []
    try:
        from app.services import solver_store
        c = solver_store.as_constraint(await solver_store.latest(session, user))
        # T6b. Read here rather than on its own card, because the two findings
        # are about the same decision and Today must not carry both "change the
        # core" and "changing the core does not help".
        found = solver_store.as_core_finding(await solver_store.latest_core(session, user))
    except Exception:  # noqa: BLE001
        logger.warning("core-ceiling card: could not read the solver verdict", exc_info=True)
        return out

    if not c.get("known") or not c.get("floor_breaches_ceiling"):
        return out

    ceiling = c.get("ceiling_pct")
    core_dd = c.get("core_drawdown_pct")
    age = c.get("age_hours")
    gap = (abs(core_dd) - ceiling) if (core_dd is not None and ceiling is not None) else None

    when = ("just now" if age is not None and age < 1
            else f"{age:.0f}h ago" if age is not None else "at an unknown time")
    measured = f"solved {when}" + (" and now too old to enforce" if c.get("stale") else "")

    # The id tracks the SUBSTANCE, per PR 4: a constant id means one Accept ever
    # hides the whole family for the TTL, and this finding changing from 35% to
    # 20% is a different statement that deserves to reappear.
    rid = _rid("core_breach", c.get("outcome"),
               round(float(core_dd), 1) if core_dd is not None else "?",
               round(float(ceiling), 1) if ceiling is not None else "?")

    out.append({
        "id": rid,
        "dimension": "risk",
        # The strongest finding the solver produces: not "you are off target"
        # but "this target is unreachable from here by any sleeve size". Nothing
        # else on Today can be acted on usefully while it holds.
        "severity": "CRITICAL",
        "title": "Your core alone breaches your drawdown ceiling",
        "why": (c.get("headline") or "")
               + (f" Your sleeves are not the cause: at zero sleeves the book still measures "
                  f"{abs(core_dd):.1f}% against your {ceiling:.1f}% ceiling, "
                  f"{gap:.1f} points over." if gap is not None else "")
               + f" ({measured}.)",
        # THE CAUSE, IN PRECEDENCE ORDER. This card's action has now been
        # rewritten three times in one day -- PR 12 said "change the core",
        # PR 13 measured that and said "the sleeves", PR 14 measured THAT and
        # said "check your ceiling first" -- so it stops being a branch each
        # time and becomes one ordering, stated once, in `_ceiling_cause`.
        #
        # WHAT PR 12 SAID HERE WAS "change the core", AND T6B MEASURED IT.
        #
        # Every one of the fifteen candidate cores -- the fourteen catalog
        # families plus the current holdings -- came back inadmissible against
        # this ceiling; the shallowest drawdown on offer was 31.9%. So the
        # advice this card shipped with was not wrong about the breach and was
        # wrong about the remedy, and a card that keeps giving advice a
        # measurement has already refuted is the card-claim invariant broken
        # from the inside.
        #
        # When the search has NOT run, the original wording stands: it is the
        # honest thing to say when nothing has measured the alternatives.
        "action": _ceiling_cause(c, found)["action"],
        "impact": ((f"{found['headline']} Measured {_core_when(found)}."
                    if found.get("headline") else "")
                   if found.get("none_admissible") else
                   "Until one of those changes, every sleeve-sizing answer is bounded by a "
                   "constraint the core has already broken - including reducing your sleeves "
                   "to nothing."),
        "how": ([f"Open Plan, then the Target card: the ceiling is the "
                 f"'WITHOUT EXCEEDING' field, showing {(c.get('ceiling_check') or {}).get('ceiling_pct')}%",
                 f"Your book has already measured "
                 f"{(c.get('ceiling_check') or {}).get('book_measured_pct')}% over the same "
                 f"window without changing anything",
                 "Set a number you would actually sit through, then press Solve",
                 "Everything below re-measures against whatever you set"]
                if _ceiling_cause(c, found)["kind"] == _CAUSE_CEILING else
                ["Your sleeves are 64% of the book in TQQQ and SOXL - that is where "
                 "the drawdown comes from",
                 "Open Plan and resize the sleeves, or raise the ceiling to a number "
                 "you would actually sit through",
                 f"The shallowest core measured was {abs(found['shallowest_pct']):.1f}%, "
                 f"still over your {ceiling:.1f}% ceiling"
                 if found.get("shallowest_pct") is not None and ceiling is not None
                 else "No candidate core came in under the ceiling",
                 "Open the Target card afterwards so both verdicts are re-solved"]
                if found.get("none_admissible") else
                ["Open Plan and look at the strategy managing your core",
                 "Compare its measured drawdown against your ceiling, not its description",
                 f"Or raise the ceiling above {abs(core_dd):.1f}% if that is the honest number"
                 if core_dd is not None else "Or raise the ceiling to an honest number",
                 "Re-open the Target card afterwards so the verdict is re-solved"]),
        # Advisory, permanently. See the docstring.
        "apply": {"kind": "none"},
        "notes": ([f"Solver verdict {c.get('outcome')}, {measured}."]
                  + ([(c.get("ceiling_check") or {}).get("note")]
                     if (c.get("ceiling_check") or {}).get("known") else [])
                  + ([f"Core search: {found['kind']}, {_core_when(found)}. "
                      f"{len(found.get('top') or [])} of the candidates shown on the Plan tab."]
                     if found.get("known") else
                     ["No core search on record yet, so the alternatives have not been "
                      "measured against this ceiling."])),
        "meta": {"source": "target_solver", "outcome": c.get("outcome"),
                 "age_hours": age, "stale": c.get("stale"),
                 "solver_version": c.get("solver_version"),
                 "cause": _ceiling_cause(c, found)["kind"],
                 "ceiling_check": c.get("ceiling_check"),
                 "core_search": {"known": found.get("known"), "kind": found.get("kind"),
                                 "age_hours": found.get("age_hours"),
                                 "none_admissible": found.get("none_admissible"),
                                 "shallowest_pct": found.get("shallowest_pct")}},
        "audit_trail": audit_for("risk", raw_data={
            "core_max_drawdown_pct": core_dd,
            "ceiling_pct": ceiling,
            "points_over": round(gap, 2) if gap is not None else None,
            "required_sleeve_pct": c.get("required_sleeve_pct"),
            "binding_constraint": c.get("binding_constraint"),
            "solved_at": c.get("solved_at"),
            "verdict_source": c.get("source"),
        }, formulas=[f("Points over the ceiling",
                       "over = |core_max_drawdown| - ceiling",
                       result=f"{gap:.1f} pts" if gap is not None else "not measured")]),
    })
    return out


async def _war_room_recs(session: AsyncSession, user: User, rows, ledger=None) -> list[dict]:
    """Turn approved agent signals into sized, plan-checked, executable cards.

    Two things were missing before. The war room and Today ran as separate
    pipelines, so the agents could approve a signal Today never mentioned. And
    the resulting card said "review this" with no size and no funding, which is
    advice, not an action.

    Now every promoted signal is checked against the plan first -- asset-class
    target, single-name concentration cap, available funding -- and is either
    sized concretely or dropped with the reason. A signal the plan can't
    accommodate is not a recommendation.
    """
    out: list[dict] = []
    try:
        from app.api.routes.war_room import _war_room_payload
        # narrate=False: the Gemini prose is a war-room-view flourish and is
        # never rendered on a Today card, but it cost 5.4-6.7s of the ~5s
        # endpoint -- one live LLM call per signal, on the event loop, on a
        # single-worker server. Measured: war_room 6726ms vs every other agent
        # under 50ms.
        payload = await _war_room_payload(session, user, rows, narrate=False)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations unavailable", exc_info=True)
        return out
    if not payload.get("grounded"):
        return out  # never turn sample prices into advice

    from app.services import funding_service as _fund
    from app.services.intake_service import get_cash

    plan = await get_plan(session, user)
    objective = plan.objective if plan else "Balanced"
    cap = effective_caps(plan)["concentration_cap"]
    # The BOOK's target -- sleeves plus core -- not the objective table. Passed
    # into every funding call below; see `funding_service.targets_for` for what
    # reading the wrong one did to two live cards on 2026-09-03.
    _btgt_full = await _book_target(session, user, objective)
    _btgt = _btgt_full.get("target")
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "asset_class": (p.meta or {}).get("asset_class")} for p in (rows or [])]
    snap = compute_snapshot(pdicts) if pdicts else {"nav": 0.0}
    nav = snap.get("nav") or 0.0
    # No local `mix` any more: the class weights a card quotes now come back from
    # propose_funded_buy, measured before and after the trade it is proposing.
    # A second, statically-computed copy here is exactly how the cards ended up
    # quoting a 97% that their own trade never moved.
    cash = await get_cash(session, user)
    held = {(getattr(p, "ticker", "") or "").upper(): p for p in (rows or [])}

    # Every strategy sleeve's tickers AND the core basket, off-limits as a
    # funding source for any of these cards. Fetched once rather than per
    # candidate. See `_funding_exclusions` for why the core is in here.
    sleeve_names = await _funding_exclusions(session, user, _btgt_full)

    for s in payload.get("sessions", []):
        if s.get("outcome") != "DISPLAYED" or len(out) >= 3:
            continue
        tk = (s.get("ticker") or "").upper()
        det = next((ln.get("detail", {}) for ln in s.get("transcript", [])
                    if ln.get("agent") == "Decision"), {})
        impact, confidence = det.get("impact"), det.get("confidence")
        alpha = next((ln.get("says") for ln in s.get("transcript", [])
                      if ln.get("agent") == "Alpha"), "")
        pos = held.get(tk)
        cls = classify_holding(pos) if pos is not None else classify(tk, "NYSE")
        weight = snap.get("exposure_ticker", {}).get(tk, 0.0)

        # `ActionType.REBALANCE.value` is "Rebalance", not "REBALANCE". So even
        # once war_room started emitting `action_type` (it never did, which is
        # the other half of this fix) the comparison would still have missed and
        # a trim signal would still have been sized as a purchase. Upper-case
        # both sides rather than pinning the check to one serialization of the
        # enum -- the guard should not break again if the wire format changes.
        _act = str(s.get("action_type") or "").upper()
        if _act in ("REBALANCE", "SELL") or (pos is not None and weight > cap):
            # Already held and over the cap -> a concrete trim back to the cap.
            if pos is None or not nav:
                continue
            price_ils = _sale_value_ils(1.0, float(pos.current_price or 0),
                                        float(pos.cost_basis or 0), pos.market, pos.meta)[0]
            excess_ils = max(0.0, (weight - cap) * nav)
            shares = int(excess_ils / price_ils) if price_ils else 0
            if shares <= 0:
                continue
            out.append({
                "id": _rid("warroom_trim", tk), "dimension": "signal", "severity": "MEDIUM",
                "title": f"Trim {tk} back to your cap",
                "why": (f"The agents approved this on the {s.get('outcome_label')} path, and {tk} "
                        f"is {weight:.0%} of your book against a {cap:.0%} single-name cap. {alpha}").strip(),
                "action": (f"Sell {shares} {tk} (~{_ils(excess_ils)}) to bring it from {weight:.0%} "
                           f"down to {cap:.0%}."),
                "impact": (f"Cuts single-name risk and frees ~{_ils(excess_ils)}."
                           + (f" Signal impact {impact:.0f}/100." if impact is not None else "")),
                "how": [f"Sell {shares} {tk} (~{_ils(excess_ils)})",
                        "Proceeds land in cash, visible on Holdings",
                        "Open Agents -> war room for the full reasoning"],
                "est_amount": round(excess_ils, 2),
                "apply": {"kind": "trim", "ticker": tk, "shares": shares},
                "meta": {"source": "war_room", "ticker": tk, "impact": impact,
                         "confidence": confidence},
            })
            continue

        # A buy candidate. Everything below -- sizing, funding, the claim about
        # what it does to the mix -- comes back from one call, because that is
        # the only way the prose and the arithmetic cannot drift apart.
        #
        # allow_within_class_swap: an approved signal on a name inside an
        # already-overweight class is a legitimate trade (shedding 3x leveraged
        # exposure for factor exposure de-risks a 97%-equity book). We opt into
        # the TRADE. The label is not ours to choose -- propose_funded_buy
        # classifies it from the simulated post-trade mix and narrates it as a
        # swap if that is what it is.
        buy = _fund.propose_funded_buy(
            rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
            book_target=_btgt,
            ticker=tk, buying_class=cls, cash_ils=cash,
            # Every sleeve's tickers are off-limits as a funding source, not
            # just the one being bought. The plan holds those on purpose and one
            # is not a piggy bank for another -- the rule C3 established for
            # sleeve funding, which this path was quietly ignoring while it sold
            # SOXL and TQQQ to buy QUAL and AVUV.
            exclude=sleeve_names,
            market=getattr(pos, "market", None) or "NYSE",
            allow_within_class_swap=True, ledger=ledger)
        if buy is None:
            continue  # not wanted by the plan, or not payable for -> not a card
        fund = buy.fund
        # "Add to" works in a title ("Add to SCHD") but not in a sentence
        # ("Add to 4,708 of SCHD"), which is what shipped. Two forms.
        verb = "Add" if pos is not None else "Buy"
        title_kind = "Swap into" if buy.is_swap else ("Add to" if pos is not None else "Buy")
        # T5 -- ONE TAP, NOT THE CARD. `blocked_by` is set by the war room when
        # the solver's stored verdict says this book cannot reach its target and
        # the signal would ADD risk. The card still renders, with the finding on
        # it; what it loses is the ability to execute in a single press.
        #
        # Suppressing it instead would be the PR 4 failure mode -- eleven cards
        # went missing there -- and a card deleted because of a background
        # measurement the user cannot see is strictly worse than one that
        # explains itself and refuses.
        _bb = s.get("blocked_by") or None
        out.append({
            "id": _rid("warroom_buy", tk), "dimension": "signal", "severity": "MEDIUM",
            "title": f"{title_kind} {tk} — {_ils(buy.amount_ils)}",
            "why": (f"The agents approved this on the {s.get('outcome_label')} path"
                    + (f" at {confidence:.0f}% confidence" if confidence is not None else "")
                    + f". {alpha} "
                    + (f"{cls} is {buy.class_before:.0%} against a "
                       f"{buy.class_target:.0%} {objective} target, so this does not add "
                       f"{cls.lower()} exposure — it changes which {cls.lower()} you hold."
                       if buy.is_swap else
                       f"It fits your {objective} plan: {cls} is {buy.class_before:.0%} "
                       f"against a {buy.class_target:.0%} target.")).strip(),
            "action": (f"{verb} {_ils(buy.amount_ils)} of {tk}, keeping it under your "
                       f"{cap:.0%} single-name cap. " + buy.summary),
            "impact": (buy.impact
                       + (f" Signal impact {impact:.0f}/100." if impact is not None else "")),
            "how": ([f"{verb} {_ils(buy.amount_ils)} of {tk}"]
                    + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                       for x in fund.get("sells", [])]
                    + ([f"Use {_ils(fund['from_cash_ils'])} of cash (floor of "
                        f"{_ils(fund['cash_floor_ils'])} stays untouched)"]
                       if fund.get("from_cash_ils") else [])
                    + (["This is a swap inside one asset class, not a rebalance"]
                       if buy.is_swap else [])
                    + ["Signals are trend-divergence based, not a price forecast"]),
            "est_amount": buy.amount_ils,
            # The one-tap is what the constraint takes away. `kind: "none"` makes
            # `_is_actionable` False, so the button reads "Mark as done" and the
            # apply dispatcher cannot execute it -- the same mechanism every
            # advisory card already uses, rather than a new bypassable flag.
            "apply": ({"kind": "none"} if _bb else buy.apply_spec),
            "blocked_by": _bb,
            "notes": ([f"{_bb.get('headline') or ''} {_bb.get('reason') or ''}".strip()
                       + (f" (solver ran {_bb['age_hours']:.0f}h ago)"
                          if _bb.get("age_hours") is not None else "")]
                      if _bb else []),
            "meta": {"source": "war_room", "ticker": tk, "impact": impact,
                     "kind": buy.kind,
                     "class_before": buy.class_before, "class_after": buy.class_after,
                     "confidence": confidence, "funding": fund},
        })
    return out


def _fund_action(sleeve_ids: list[str]) -> dict:
    """Where a "top this ticker up" Accept should route.

    One sleeve wants it -> fund that sleeve, which is what the button on the
    Plan tab does. TWO sleeves want it -> there is no single `strategy_id` to
    hand `fund_sleeve`, and picking one of them would fund it at that sleeve's
    share while the card was describing the SUM. The whole-plan path is the only
    one that can honour a summed target, so that is where it goes.
    """
    if len(sleeve_ids) == 1:
        return {"kind": "fund_sleeve", "strategy_id": sleeve_ids[0]}
    if not sleeve_ids:
        return {"kind": "none"}
    return {"kind": "fund_plan"}


async def build_recommendations(session: AsyncSession, user: User) -> dict:
    _tm = _Stopwatch()
    rows = await list_positions(session, user)
    if not rows:
        return {"count": 0, "recommendations": [], "message": "Add holdings to get recommendations."}
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "volatility_pct": (p.meta or {}).get("volatility_pct"),
               "liquidity_score": (p.meta or {}).get("liquidity_score"),
               "asset_class": (p.meta or {}).get("asset_class"),
               "expense_ratio_pct": (p.meta or {}).get("expense_ratio_pct")} for p in rows]
    snap = compute_snapshot(pdicts)
    nav = snap["nav"]
    plan = await get_plan(session, user)
    cap = effective_caps(plan)["concentration_cap"]
    objective = plan.objective if plan else "Balanced"
    recs: list[dict] = []
    try:
        from app.services.intake_service import get_cash as _get_cash
        _cash_ils = await _get_cash(session, user)
    except Exception:  # noqa: BLE001
        _cash_ils = 0.0
    # Resolved here, before anything reads it: the funding exclusions need the
    # core basket, and the tax block below needs the same set.
    btgt = await _book_target(session, user, objective)

    # Sleeve holdings AND the core basket are never a funding source for a Today
    # card. The plan holds both on purpose; C3 established it for sleeves, PR 37
    # established that a core is a strategy too, and every card that can sell has
    # to honour both. See `_funding_exclusions`.
    _sleeve_names = await _funding_exclusions(session, user, btgt)
    # ONE inventory for every funded card in this build. Each card is planned
    # against what earlier cards have already committed to selling, so no two
    # cards spend the same shares -- and a card whose first choice of funding is
    # gone is re-sourced from what remains rather than dropped.
    from app.services import funding_service as _fs
    _ledger = _fs.FundingLedger()
    # Which contributing agents failed this build. Each block below is defensive so a
    # data hiccup never breaks Today \u2014 but silence made a missing card
    # indistinguishable from "nothing to do", so failures are now logged and reported.
    degraded: list[str] = []

    # 1) Concentration trim
    if snap["max_weight"] > cap and nav:
        tk = max(snap["exposure_ticker"], key=snap["exposure_ticker"].get)
        w = snap["exposure_ticker"][tk]
        price = next((float(r.current_price or 0) for r in rows if r.ticker == tk), 0)
        trim = (w - cap) * nav
        shares = int(trim / price) if price else 0
        recs.append({"id": _rid("trim", tk), "dimension": "diversification", "severity": "HIGH",
                     "title": f"Trim {tk}",
                     "why": f"{tk} is {w:.0%} of your portfolio — above your {cap:.0%} single-holding limit, "
                            f"so a bad run in one name could sink the whole book.",
                     "action": f"Sell about {_ils(trim)} of {tk} (~{shares} shares) to bring it from "
                               f"{w:.0%} down to your {cap:.0%} limit.",
                     "impact": f"Cuts single-name risk: brings {tk} from {w:.0%} to your {cap:.0%} cap "
                               f"and frees ~{_ils(trim)} to spread across your plan's mix.",
                     "how": ["Open your brokerage account",
                             f"Place a SELL order for ~{shares} {tk} shares (~{_ils(trim)})",
                             "Reinvest the proceeds across your other holdings or your plan's target mix"],
                     "est_amount": round(trim, 2),
                     "apply": {"kind": "trim", "ticker": tk, "shares": shares}})
        recs[-1]["audit_trail"] = audit_for("diversification",
            raw_data={"ticker": tk, "weight": round(w, 4), "concentration_cap": round(cap, 4),
                      "nav": round(nav, 2), "price": round(price, 2)},
            formulas=[
                f("Position weight", "weight = position_value / NAV", result=f"{w:.0%}"),
                f("Trim amount", "trim = (weight - cap) * NAV",
                  substituted=f"({w:.4f} - {cap:.4f}) x {nav:,.0f}", result=f"₪{trim:,.0f}"),
                f("Shares to sell", "shares = trim / price",
                  substituted=f"{trim:,.0f} / {price:,.2f}", result=str(shares))])

    # 2) Tax-loss harvesting
    tx = tax_opportunities(pdicts)
    harvest = [o for o in tx["opportunities"] if o["trigger"] == "CAPITAL_LOSS_HARVESTING"]
    if harvest:
        losers = [r.ticker for r in rows if float(r.current_price or 0) < float(r.cost_basis)]
        # Never harvest the sleeve the plan says to hold.
        #
        # Observed live: this card offered to sell SOXL -- the aggressive leg of
        # the applied btm_trend_soxl strategy, with a max_weight cap armed on it
        # by P1 -- to realize a 12 shekel tax saving. Two agents giving opposite
        # instructions about the same position on the same screen. The tax engine
        # has no idea the position is held on purpose, and a 12 shekel tail
        # should not wag a strategy dog.
        #
        # C2: read EVERY sleeve, not the one plans.strategy happens to name. A
        # book can run two now, and an exclusion set built from one of them
        # leaves the other harvestable -- the same two-agents-disagreeing bug,
        # just on whichever sleeve was not applied most recently.
        # ONE exclusion set, shared with card funding (`_funding_exclusions`).
        # It was two: this block resolved sleeves itself and PR 41 bolted the
        # core on beside it, while funding still knew only about sleeves. Two
        # lists of what the plan holds on purpose is how they end up disagreeing
        # -- which is exactly what happened between PR 41 and PR 43, where the
        # tax card would not touch BND and a funding leg proposed selling 65% of
        # it on the same screen.
        _protected = set(_sleeve_names)
        _held_for_strategy = sorted({t for t in losers if (t or "").upper() in _protected})
        losers = [t for t in losers if (t or "").upper() not in _protected]

        # THE SAVING IS MEASURED ON WHAT THIS CARD SELLS, not on the book.
        #
        # It used to read `estimated_annual_tax_savings_currency` straight off
        # `tax_opportunities`, which scans EVERY position -- including the ones
        # the lines above just decided not to sell.
        #
        # Measured live on 2026-09-03: the card proposed selling VTI and BND and
        # claimed a saving of ILS 146.46. Selling VTI and BND realizes ILS 11.17
        # of losses, worth ILS 2.79 at the 25% CGT rate. The other ILS 143.65 --
        # 98% of the headline -- was the loss on SOXL and TQQQ, which the card
        # was not offering to touch. A 52x overstatement of its own number.
        from app.core.config import get_settings as _get_settings
        _cgt_rate = float(_get_settings().cgt_rate)
        _loss_ils = _unrealized_ils(rows, only=losers, sign=-1) if losers else 0.0
        _gain_ils = _unrealized_ils(rows, sign=+1)
        save = round(_cgt_rate * _loss_ils, 2) if losers else 0.0

    if harvest and losers:
        _note = ("" if not _held_for_strategy else
                 f" {', '.join(_held_for_strategy)} is left alone: your plan holds it "
                 f"on purpose, and a tax saving is not a reason to sell your strategy.")

        # Severity by MATERIALITY, and never CRITICAL.
        #
        # This was `"CRITICAL" if save > 0 else "MEDIUM"`, so a 12 shekel saving
        # on a 21,405 shekel book -- 0.06% -- was flagged Important and sorted
        # above everything. CRITICAL is what a firing stop-loss gets (see _SEV);
        # an optional tax optimisation is not in that class, and putting it there
        # invites a careless tap on a card that sells an entire position.
        #
        # The threshold is the app's own materiality unit rather than a number
        # picked here: MIN_TRADE_ILS is what it already considers "too small to
        # be worth the friction". A saving below that is, by the app's own
        # standard, not worth shouting about.
        from app.services.funding_service import MIN_TRADE_ILS as _MIN_TRADE
        # ILS-normalized, from the snapshot. Multiplying current_price by
        # quantity gives the position's NATIVE currency: VXUS at $87.21 x 7.673
        # is $669, and printing that as "₪667" is the FX class of error this
        # app has been bitten by twice. The snapshot's weights are already
        # FX-converted, so derive from those.
        _sell_value = sum((snap.get("exposure_ticker") or {}).get(t, 0.0) * nav
                          for t in losers)
        if save >= _MIN_TRADE:
            _sev = "HIGH"
        elif save >= _MIN_TRADE / 5:
            _sev = "MEDIUM"
        else:
            _sev = "LOW"
        # Say what it costs, not only what it saves. Selling 2,001 to save 12 is
        # a decision the user should be able to see the shape of.
        _cost_note = ("" if _sev != "LOW" or not _sell_value else
                      f" Worth weighing: that means selling {_ils(_sell_value)} of "
                      f"holdings to bank {_ils(save)}, and giving up the position "
                      f"for the wash-sale window.")

        # A LOSS IS ONLY A SAVING ONCE THERE IS A GAIN TO NET IT AGAINST, and
        # this app has no record of gains realized earlier in the year -- it
        # holds a book, not a tax return. So the card no longer says "this
        # year". `tax_opportunities` already knew: its own description reads
        # "future gains (carry-forward)" when the book carries no gains, while
        # the card built from it said "lowers this year's tax bill" regardless.
        #
        # `_gain_ils` is UNREALIZED. It is named as the thing that would have to
        # be sold too, not counted as tax already owed.
        _when = (f" You are holding about {_ils(_gain_ils)} of unrealized gains; "
                 f"realizing some of those in the same tax year is what turns this "
                 f"loss into a saving."
                 if _gain_ils > 0 else
                 " Nothing in your book is currently showing a gain, so this is a "
                 "carry-forward: it lowers a future tax bill, not this year's.")
        # The engine states this limitation itself (`_TAX_ASSUMPTIONS`) and the
        # card never repeated it. An estimate computed on the nominal gain is an
        # upper bound wherever the basis would be indexed.
        _basis_note = (" Estimated on the nominal gain, with no inflation-linked "
                       "cost-basis adjustment - treat it as an upper bound.")

        recs.append({"id": _rid("tax", *sorted(losers)), "dimension": "tax",
                     "severity": _sev,
                     "title": ("Harvest a tax loss" if _sev != "LOW"
                               else f"A small tax loss to harvest ({_ils(save)})"),
                     "why": "You hold position(s) below what you paid \u2014 selling now realizes a loss you can "
                            "use to offset taxable gains." + _note,
                     "action": f"Sell your losing position(s) ({', '.join(losers)}) to realize "
                               f"{_ils(_loss_ils)} of losses, worth about {_ils(save)} in tax "
                               f"once you have a gain to offset." + _when,
                     "impact": f"Banks {_ils(save)} of tax value against future gains; you can re-buy after "
                               f"the wash-sale window if you still want the exposure."
                               + _cost_note + _basis_note,
                     "how": ["Sell the position(s) currently below what you paid",
                             "The realized loss offsets taxable gains, in this year or a later one",
                             "If you still believe in them, re-buy after the wash-sale window"],
                     "est_amount": save,
                     "apply": {"kind": "sell_losers", "tickers": losers}})
        recs[-1]["audit_trail"] = audit_for("tax",
            raw_data={"losing_tickers": losers, "excluded_strategy_sleeve": _held_for_strategy,
                      "realizable_loss_ils": _loss_ils,
                      "unrealized_gains_ils": _gain_ils,
                      "offsets_a_gain_now": _gain_ils > 0,
                      "estimated_tax_value": round(save, 2)},
            formulas=[
                f("Realizable loss", "loss = sum over the tickers THIS CARD SELLS of "
                                     "(cost - price) x qty x fx",
                  result=f"\u20aa{_loss_ils:,.2f}"),
                f("Tax value", "tax_value = realizable_loss x CGT_rate",
                  substituted=f"{_loss_ils:,.2f} x {_cgt_rate:.2f}",
                  result=f"\u20aa{save:,.2f}")])

    # 3) Rebalance toward the BOOK's target mix -- sleeves plus core, not the
    #    core's mix measured against a book that is 64% something else.
    mix, _ = current_mix(rows)
    target = btgt["target"]
    report = AllocationEngine().compute(target_allocation=target, current_allocation=mix, nav=nav)
    # Every action past the drift threshold, not the first two. The slice meant
    # `_rid("rebal","Fixed Income")` was never GENERATED on a book drifted in
    # three classes -- not suppressed, not filtered, simply never built. A card
    # that does not exist cannot be restored, and nothing said it was missing.
    for a in report.rebalance_actions:
        # WHERE THE TARGET CAME FROM, on the card. "77%" was the number that was
        # wrong for twelve days and nothing on the screen could have caught it.
        # "77% (64% your sleeves + 13% your core)" can be checked against the
        # Plan tab in one glance.
        whence = bt_explain(btgt, a.asset_class)
        sleeve_share = btgt["sleeves"].get(a.asset_class, 0.0)
        recs.append({"id": _rid("rebal", a.asset_class), "dimension": "allocation",
                     "severity": _drift_severity(mix.get(a.asset_class, 0.0),
                                                 target.get(a.asset_class, 0.0)),
                     "title": f"{a.action_type.title()} {a.asset_class}",
                     "why": f"Your {a.asset_class} weight ({mix.get(a.asset_class, 0):.0%}) has drifted from "
                            f"your book's target of {whence}.",
                     "action": f"{a.action_type.title()} about {_ils(a.estimated_trade_value_currency)} of "
                               f"{a.asset_class} to move toward {whence}.",
                     "impact": f"Moves ~{_ils(a.net_trade_value_currency)} (net of tax & costs) to realign your "
                               f"mix with your plan."
                               + (f" Your sleeves hold {sleeve_share:.0%} of the book in {a.asset_class} and are "
                                  f"not touched by this." if sleeve_share else ""),
                     "how": [f"{a.action_type.title()} {a.asset_class} by ~{_ils(a.estimated_trade_value_currency)}"
                             + (f" — e.g. {CLASS_ETF[a.asset_class]}" if a.asset_class in CLASS_ETF else ""),
                             f"After tax & costs that's about {_ils(a.net_trade_value_currency)} moved",
                             f"Target is {whence}"],
                     "est_amount": a.net_trade_value_currency,
                     # `direction` is recorded, not left to be read back off the
                     # title. `_reconcile` merges these legs into one card and
                     # rewrites the title, after which "Sell Equities" survives
                     # only as prose inside `meta.merged_legs` -- and
                     # `contradictions.py` was reading exactly that prose,
                     # through `title.lower().startswith("sell")`, to decide
                     # which way a card pointed. A card that says what it does
                     # in a field cannot be misread by a string test.
                     "apply": {"kind": "rebalance_to_objective",
                               "asset_class": a.asset_class,
                               "direction": str(a.action_type).lower()}})
        recs[-1]["audit_trail"] = audit_for("allocation",
            raw_data={"asset_class": a.asset_class, "current_weight": round(mix.get(a.asset_class, 0.0), 4),
                      "target_weight": round(target.get(a.asset_class, 0.0), 4), "nav": round(nav, 2),
                      # Both halves, so the audit trail can be checked without
                      # re-deriving them. This is the arithmetic that was wrong.
                      "target_from_sleeves": round(btgt["sleeves"].get(a.asset_class, 0.0), 4),
                      "target_from_core": round(btgt["core"].get(a.asset_class, 0.0), 4),
                      "core_pct_of_book": btgt["core_pct"], "target_source": btgt["source"]},
            formulas=[
                f("Book target", "target = sleeves_share + core_mix x core_share",
                  substituted=f"{btgt['sleeves'].get(a.asset_class,0.0):.4f} + "
                              f"{btgt['core'].get(a.asset_class,0.0):.4f}",
                  result=f"{target.get(a.asset_class,0.0):.1%}"),
                f("Drift", "drift = current_weight - target_weight",
                  substituted=f"{mix.get(a.asset_class,0.0):.4f} - {target.get(a.asset_class,0.0):.4f}",
                  result=f"{mix.get(a.asset_class,0.0)-target.get(a.asset_class,0.0):+.1%}"),
                f("Gross trade", "trade = |drift| x NAV", result=f"₪{a.estimated_trade_value_currency:,.0f}"),
                f("Net of frictions", "net = gross - tax_drag - cost - slippage",
                  result=f"₪{a.net_trade_value_currency:,.0f}")])

    # 3a) THE CORE'S OWN BASKET, LEG BY LEG.
    #
    # Eran, 2026-09-02: *"if I'm not holding VTI as part of my portfolio --> I
    # expect to get a recommendation card to purchase it."*
    #
    # Card (3) above is right and is not that card. It works at ASSET-CLASS
    # level: "Buy Equities, about 1,215 ILS". On this book the equities target
    # is 89.2% -- 73% from two leveraged sleeves plus 16.2% from the core -- so
    # BUYING MORE TQQQ SATISFIES THAT CARD COMPLETELY and leaves the core still
    # holding no VTI at all. A class is not an instruction.
    #
    # The core is the one place where the app knows the actual instrument,
    # because a core IS a basket (PR 37): bal_6040 is 60% VTI, 40% BND, and the
    # book's target for each leg is core_pct x weight x NAV. That arithmetic is
    # already what `_book_target` folds into the class targets; here it is left
    # un-aggregated, which is the whole point.
    #
    # ADVICE, NOT AN ACTION. No `apply` block: funding a 3,417 ILS VTI purchase
    # out of a 619 ILS cash balance is not something this app can carry out, and
    # a button that reports success without doing anything is the exact defect
    # `_ACTIONABLE_KINDS` exists to prevent. The card names the ticker and the
    # amount and stops there.
    try:
        _core_id = btgt.get("core_strategy_id")
        _core_pct = float(btgt.get("core_pct") or 0.0)
        if _core_id and _core_pct > 0 and nav > 0:
            from app.services import sleeve_service as _sv_core
            from app.services.measured_assumptions import core_matches_basket
            from app.services.strategies import core_weights as _cw

            _basket = _cw(_core_id) or {}
            _cname = btgt.get("core_strategy_name") or _core_id
            # Whatever a sleeve claims belongs to that sleeve, not to the core.
            # `sleeve_tickers` is the app's existing exclusion set -- the one
            # `blend.core_weights_from` and PR 37 both work from.
            _claimed = {str(t).upper()
                        for t in await _sv_core.sleeve_tickers(session, user)}
            # THE SAME FX NORMALISATION `current_mix` AND `_portfolio_stats` USE.
            # A second way of turning a position into shekels is a second answer
            # to what the book is worth, and these numbers are compared against
            # the class-level card built from `mix` a few lines up
            # (investing-discipline S2).
            from app.services.allocation_mix import classify_holding as _ch
            from app.services.fx import fx_rate as _fxr
            from app.services.fx import price_currency as _pcur

            _held: dict[str, float] = {}
            for _p in rows:
                _tk = str(getattr(_p, "ticker", "") or "").upper()
                if not _tk or _tk in _claimed or _tk == "CASH":
                    continue
                if _ch(_p) == "Cash":
                    continue          # cash is in no basket; it is not a leg
                _meta = _p.meta if isinstance(_p.meta, dict) else None
                _v = (float(_p.quantity or 0.0) * float(_p.current_price or 0.0)
                      * _fxr(_pcur(_p.market, _meta)))
                if _v > 0:
                    _held[_tk] = _held.get(_tk, 0.0) + _v

            _ok, _ev = core_matches_basket(_held, _basket,
                                           band_pct=SLEEVE_DRIFT_BAND_PCT)
            if _basket and not _ok and not _ev.get("reason"):
                # ONE CARD, AND IT ACTS.
                #
                # PR 38 shipped three cards -- buy VTI, trim BND, sell V -- and
                # every one of them was advice, on the grounds that this app
                # cannot fund a 3,392 ILS purchase from a 619 ILS cash balance.
                # Eran, reading them: *"guidance is not enough... I want these
                # cards to act on my behalf (as other cards can do it)."*
                #
                # The reasoning was half right and the conclusion was wrong. The
                # money is already in the book, sitting in the wrong places: a
                # holding the basket does not name, a leg above its share, and
                # sleeves holding more than the sizes he set. Bringing the core
                # back is a REBALANCE, not a purchase, and the app can do those.
                #
                # `_core_plan` sizes it. THE CARD AND THE BUTTON CALL THE SAME
                # FUNCTION -- `_rebalance_to` is the cautionary tale, where the
                # card read one target, Accept applied another, and the screen
                # said "sell 4,324" while the button sold 12,139.
                _plan = await _core_plan(session, user, rows, nav, objective, btgt)
                if _plan and _plan.get("ok"):
                    from app.services.core_realign import describe as _describe
                    _steps = _describe(_plan)
                    _missing = ", ".join(_ev.get("missing") or []) or None
                    _extra = ", ".join(_ev.get("extra") or []) or None
                    _bits = []
                    if _missing:
                        _bits.append(f"you hold none of {_missing}")
                    if _extra:
                        _bits.append(f"{_extra} is in it and not in the basket")
                    _short = float(_plan.get("shortfall_ils") or 0.0)
                    _moved = float(_plan.get("buys_ils") or 0.0)
                    recs.append({
                        "id": _rid("corebasket", _core_id),
                        "dimension": "allocation",
                        "severity": _drift_severity(
                            sum(_held.values()) / nav if nav else 0.0, _core_pct),
                        "title": f"Bring your core back to {_cname}",
                        "why": (f"Your core is {_core_pct:.0%} of the book and runs {_cname}, "
                                f"and it is not holding that basket: {'; '.join(_bits) or 'its legs are off'}. "
                                f"Until it holds the basket, the measurement of {_cname} is not "
                                f"used to price your book."),
                        "action": (f"Rebalance about {_ils(_moved)} inside your own book — "
                                   f"no new money."),
                        "impact": (f"Puts the core back on {_cname}, so its measured return and "
                                   f"volatility start pricing {_core_pct:.0%} of your book."
                                   + (f" About {_ils(_short)} would still be unfunded."
                                      if _short >= _REFIRE_FLOOR_ILS else "")),
                        # EVERY LEG, BEFORE THE BUTTON. These lines come from the
                        # same plan the executor runs, so the card cannot promise
                        # a trade Apply will not make, or hide one it will.
                        "how": _steps,
                        "est_amount": round(_moved, 2),
                        "apply": {"kind": "core_to_basket", "objective": objective,
                                  "core_strategy_id": _core_id},
                        "meta": {"plan": {k: v for k, v in _plan.items()
                                          if k in ("sells_ils", "sleeve_trim_ils",
                                                   "cash_used_ils", "buys_ils",
                                                   "tax_ils", "shortfall_ils")}},
                    })
                    recs[-1]["audit_trail"] = audit_for("allocation", raw_data={
                        "core_strategy_id": _core_id,
                        "core_pct_of_book": round(_core_pct, 4),
                        "nav": round(nav, 2),
                        "want_ils": _plan.get("want_ils"),
                        "have_ils": _plan.get("have_ils"),
                        "legs": _plan.get("legs"),
                        "band_pct": SLEEVE_DRIFT_BAND_PCT},
                        formulas=[
                            f("Leg target", "target = core_pct x basket_weight x NAV",
                              result=str(_plan.get("want_ils"))),
                            f("Funding", "sells + sleeve overshoot + spendable cash - tax",
                              substituted=(f"{_plan.get('sells_ils')} + "
                                           f"{_plan.get('sleeve_trim_ils')} + "
                                           f"{_plan.get('cash_used_ils')} - "
                                           f"{_plan.get('tax_ils')}"),
                              result=f"₪{_plan.get('buys_ils'):,.0f} bought"),
                            f("Unfunded", "shortfall = wanted - funded",
                              result=f"₪{_short:,.0f}")])
    except Exception:  # noqa: BLE001
        # A core the app cannot read is not a reason to lose every other card.
        logger.warning("core-basket cards: could not build", exc_info=True)

    # 3b) Concentration in one region / currency, or a hard-to-sell book — the same
    #     vectors the "biggest risk" panel shows, surfaced here as actionable to-dos so
    #     a pushed risk alert always maps to something you can do in the app.
    try:
        from app.services.portfolio_analytics import risk_alerts as _risk_alerts
        for a in _risk_alerts(snap, cap).get("alerts", []):
            vec = a.get("vector")
            if vec == "geographic":
                # A named fund and a number, funded explicitly: "diversify" on its
                # own is a topic, not something you can act on.
                _amt = round(min(0.10, max(0.05, 1.0 - cap)) * nav, 2) if nav else 0.0
                _spec, _how = {"kind": "none"}, [
                    "Favor under-represented regions for your next contributions",
                    "A broad ex-US global ETF (VXUS) is the simplest single fix"]
                _act = ("Spread new money across more regions so one country's downturn can't sink "
                        "the whole portfolio.")
                try:
                    from app.services import funding_service as _fs
                    # Geographic concentration is a real risk and VXUS is a real
                    # answer to it -- but VXUS is Equities, so buying it out of
                    # equities moves no asset-class weight. allow_within_class_swap
                    # keeps the card (the diversification is in the geography, not
                    # the mix) and forces it to say which one it is.
                    _b = _fs.propose_funded_buy(
                        rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
                        book_target=btgt["target"],
                        ticker="VXUS", buying_class="Equities", cash_ils=_cash_ils,
                        requested_ils=_amt, exclude=_sleeve_names, market="NASDAQ",
                        allow_within_class_swap=True, ledger=_ledger)
                    if _b is not None:
                        _f = _b.fund
                        _act = (f"Buy {_ils(_b.amount_ils)} of VXUS (global ex-US equities) to "
                                f"spread beyond one region. " + _b.summary)
                        _how = ([f"Buy {_ils(_b.amount_ils)} of VXUS (Vanguard Total International Stock)"]
                                + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) "
                                   f"— {x['reason']}" for x in _f.get("sells", [])]
                                + ([f"Use {_ils(_f['from_cash_ils'])} of cash"]
                                   if _f.get("from_cash_ils") else [])
                                + (["This spreads your geography, not your asset mix"]
                                   if _b.is_swap else [])
                                + ["Alternatives: VEA (developed) or VWO (emerging)"])
                        _spec = _b.apply_spec
                except Exception:  # noqa: BLE001
                    logger.warning("geo diversification sizing failed", exc_info=True)
                recs.append({"id": _rid("divrisk", "geo"), "dimension": "diversification",
                             "severity": a.get("severity", "MEDIUM"),
                             "title": ("Diversify across regions"
                                       + (f" — {_ils(_spec['amount_ils'])} of VXUS"
                                          if _spec["kind"] == "buy_funded" else "")),
                             "why": a.get("detail") or "Most of your money sits in a single region.",
                             "action": _act,
                             "impact": ("Lowers geographic concentration risk from over-reliance "
                                        "on one region. Your equity weight is unchanged."
                                        if _spec.get("kind") == "buy_funded"
                                        else "Lowers geographic concentration risk from "
                                             "over-reliance on one region."),
                             "how": _how,
                             "est_amount": _spec.get("amount_ils"),
                             "apply": _spec})
            elif vec == "currency":
                recs.append({"id": _rid("divrisk", "cur"), "dimension": "diversification",
                             "severity": a.get("severity", "MEDIUM"), "title": "Diversify your currency exposure",
                             "why": a.get("detail") or "Most of your money sits in a single currency.",
                             "action": "Add holdings priced in other currencies so an FX swing doesn't move your "
                                       "whole net worth at once.",
                             "impact": "Reduces the FX imbalance from having most of your money in one currency.",
                             "how": ["Add assets denominated in other major currencies",
                                     "A globally diversified fund spreads currency exposure automatically",
                                     "Re-check here as the balance improves"],
                             "apply": {"kind": "none"}})
            elif vec == "liquidity":
                recs.append({"id": _rid("divrisk", "liq"), "dimension": "liquidity",
                             "severity": a.get("severity", "HIGH"), "title": "Hold more liquid assets",
                             "why": a.get("detail") or "A large share of your book may be hard to sell quickly.",
                             "action": "Shift some of the least-tradable holdings into more liquid ones so you can "
                                       "raise cash without a fire-sale if you need to.",
                             "impact": "Improves how quickly you could exit without moving the price against you.",
                             "how": ["Identify your least-liquid holdings",
                                     "Trim a portion into broadly-traded ETFs or cash equivalents",
                                     "Keep enough liquidity for near-term needs"],
                             "apply": {"kind": "none"}})
    except Exception:  # noqa: BLE001
        logger.warning("risk-alert recommendations failed", exc_info=True)
        degraded.append("risk_alerts")

    # 4) Behind your goal? Optimize across every lever to close the gap.
    #    Reconcile the "current path" projection with the app's home panel — same
    #    Monte-Carlo engine — so the card, the digest and "Where this could end up"
    #    all cite one number instead of three.
    projected_median = None
    try:
        from app.api.routes.plan import _auto_target, _portfolio_stats
        from app.engines.simulation_engine import SimulationEngine
        _pstats = _portfolio_stats(rows)
        if _pstats.get("nav"):
            _pyears = max(1, int(getattr(plan, "horizon_years", 10) or 10)) if plan else 10
            _sim = SimulationEngine(seed=7).run(
                initial_value=_pstats["nav"], expected_return_pct=_pstats.get("expected_roi") or 6.0,
                volatility_pct=_pstats.get("volatility") or 12.0, horizon_years=_pyears,
                target_value=_auto_target(_pstats["nav"], plan))
            projected_median = float(_sim.nominal.p50)
    except Exception:  # noqa: BLE001
        projected_median = None
    recs += _behind_goal_recs(plan, snap, objective, projected_median)
    recs += FeeAgent().recommendations(pdicts)  # Phase 3.2 fee optimizer

    # 5) Manage-the-holdings agents (Phase 4): per-holding Buy/Hold/Trim verdicts,
    #    sector hedging, momentum/trend and income/cost. Each is defensive - a
    #    data hiccup never breaks the Today view.
    trimmed = {(r.get("apply") or {}).get("ticker") for r in recs
               if (r.get("apply") or {}).get("kind") == "trim"}
    recs += await _offload(_holding_verdict_recs, rows, snap, cap, trimmed)
    _tm.mark("holding_verdicts")
    recs += await _offload(_hedge_recs, rows, snap)
    _tm.mark("hedge")
    # `_sleeve_floor` stops the uptrend card offering a cap BELOW the sleeve
    # target for the same ticker -- the other half of where the live 15% TQQQ
    # cap came from. Same formula as the suggestions list, same blind spot.
    from app.services import sleeve_service as _sv_mom
    try:
        _sleeve_floor = {t.upper(): round(w * 100.0, 1)
                         for t, w in (await _sv_mom.all_sleeve_targets(session, user)).items()}
    except Exception:  # noqa: BLE001 -- a floor is never worth losing the card over
        _sleeve_floor = {}
    recs += await _offload(_momentum_recs, rows, snap, _sleeve_floor)
    _tm.mark("momentum")

    # AN OPEN CIRCUIT IS A DEGRADED AGENT, and has to say so here.
    #
    # These read `guarded_history` / `_fundamentals` per ticker and swallow the
    # failure per ticker -- one bad symbol must not sink the scan. Correct, and
    # it means a breaker OPEN for EVERY symbol produces an empty list and no
    # exception, so nothing above appends to `degraded` and the response claims
    # health. Live on 2026-08-23: the history breaker was OPEN, the war-room,
    # momentum and verdict agents returned nothing, and `degraded` was `[]`.
    # The rule this project already holds is that a degraded agent means a
    # MISSING card says nothing about the underlying state -- the guarantee an
    # empty list quietly broke.
    try:
        from app.providers.registry import provider_health
        _ph = provider_health()
        _open = {n for n in ("history", "fundamentals")
                 if (_ph.get(n) or {}).get("circuit_state") == "OPEN"}
        if "history" in _open:
            for _a in ("holding_verdicts", "momentum", "war_room"):
                if _a not in degraded:
                    degraded.append(_a)
        if "fundamentals" in _open:
            for _a in ("holding_verdicts", "hedge"):
                if _a not in degraded:
                    degraded.append(_a)
        # Cards built on an expired series are not the same claim as cards built
        # on today's, even when the numbers are identical -- and for daily bars
        # they usually are. Naming it separately keeps "we served you something
        # older" distinct from "we have nothing", which are the two very
        # different things an empty `degraded` used to conflate.
        if any((_ph.get(n) or {}).get("stale_served") for n in ("history", "fundamentals")):
            if "history_stale" not in degraded:
                degraded.append("history_stale")
    except Exception:  # noqa: BLE001 -- reporting health must never break Today
        logger.warning("provider health unavailable for degraded[]", exc_info=False)

    recs += _income_cost_recs(pdicts, snap, objective)
    _tm.mark("income_cost")
    # Surplus cash gets a sized, executable home. Emitted after the income agent
    # so _reconcile can drop the old advisory "Put idle cash to work" card when
    # this one fires -- two cards about the same shekels is what the reconcile
    # pass exists to prevent.
    # `btgt["target"]` is threaded in rather than re-derived. Both of these are
    # sync and run in a worker thread, so they cannot await -- which is exactly
    # how they ended up reading OBJ_TARGET directly and quoting a commodities
    # target of 10% in the same payload where the rebalance card quoted 25%.
    # `objective` IS THE THIRD ARGUMENT, and its absence was item 2's root cause:
    # every book was ranked on DEFAULT_WEIGHTS, so the objective changed how much
    # was bought and never what.
    # `_pin_out` IS HOW THE COVERAGE SURVIVES A SCAN THAT RANKED NOTHING. The
    # pin used to be lifted off the equity ideas, so it disappeared in exactly
    # the case worth diagnosing -- see the comment on `buy_ideas_ranked_for`
    # below. A dict passed in and filled in, rather than a module global, so two
    # concurrent scans cannot hand each other the wrong coverage.
    _pin_out: dict = {}
    _ideas = await _offload(_buy_ideas, snap,   # screener; timed separately, it hits providers
                            (getattr(plan, "currency", None) or "ILS"),
                            objective, _pin_out)
    _tm.mark("buy_ideas")

    _redeploy = await _offload(_redeploy_cash_recs, rows, snap, plan, objective, cap, _cash_ils,
                               btgt["target"])
    recs += await _offload(_commodity_recs, rows, snap, objective, plan, cap, _cash_ils,
                           _sleeve_names, _ledger, btgt["target"])

    # THE SCREENER GETS A SURFACE. Ranked names become fundable cards here, and
    # deliberately LAST among the buying cards: the shared `_ledger` has already
    # been spent down by the rebalance, redeploy and commodity legs, so an
    # optional idea can only ever use what a needed trade did not. Two cards
    # spending the same shares is the defect PR 1 fixed and this is the ordering
    # that keeps it fixed.
    #
    # `_ideas` is reused rather than re-screened -- a second `screen_equities`
    # call here would double the provider fan-out on every render AND could rank
    # differently from the pin already in the payload. The screener call was
    # hoisted above the card producers for exactly this: appended after the
    # reconcile-and-sort pass, these cards would have skipped it, and a card that
    # dodges reconciliation is how two cards end up about the same shekels.
    _named = {str(r.get("meta", {}).get("chosen") or "") for r in recs}
    _named |= {str(r.get("meta", {}).get("ticker") or "") for r in recs}
    recs += await _offload(_buy_idea_recs, _ideas, rows, snap, plan, objective, cap,
                           _cash_ils, _sleeve_names, _ledger, _named, btgt["target"])
    try:
        async with _agent_tx(session):
            from app.services.performance_service import performance as _perf_fn
            recs += _benchmark_recs(await _perf_fn(session, user), objective)
    except Exception:  # noqa: BLE001 -- performance is best-effort, never break Today
        logger.warning("benchmark-lag recommendation failed", exc_info=True)
        degraded.append("performance")

    # Macro signal: factor the futures-derived market regime into the agents. A
    # risk-off backdrop surfaces a defensive action. Defensive - never break Today.
    market = {}
    try:
        from app.services.markets_service import cached_regime
        market = cached_regime()  # cache-only: never blocks Today on a network call
        if market.get("regime") == "risk-off":
            recs.append({"id": _rid("macro", "riskoff"), "dimension": "macro", "severity": "MEDIUM",
                         "title": "Markets look risk-off",
                         "why": f"The futures-derived market regime has turned risk-off ({market.get('rationale','')}).",
                         "action": (f"The market backdrop is risk-off ({market.get('rationale','')}). "
                                    "Consider keeping more cash on hand and trimming your most volatile "
                                    "positions until conditions calm."),
                         "impact": "More cash and less volatility reduce your exposure to a near-term drawdown.",
                         "how": ["Review your highest-volatility holdings",
                                 "Consider raising cash by ~5-10%",
                                 "Hold off on adding leverage or speculative names"]})
    except Exception:  # noqa: BLE001
        logger.warning("market-regime recommendation failed", exc_info=True)
        degraded.append("market_regime")
        market = {}

    # Liquidity floor: name what to sell and how much, rather than "hold more cash".
    try:
        from app.services import funding_service as _fs2
        _floor = _fs2.cash_floor_ils(nav, objective, plan)
        if nav and _cash_ils < _floor:
            _need = round(_floor - _cash_ils, 2)
            _cands = _fs2.rank_trim_candidates(rows, snap, objective, cap,
                                               book_target=btgt["target"])
            if _need >= _fs2.MIN_TRADE_ILS and _cands:
                _c = _cands[0]
                _sh = int(_need / _c["price_ils"]) if _c["price_ils"] else 0
                if _sh > 0:
                    recs.append({
                        "id": _rid("raise_cash", round(_need / 250) * 250),
                        "dimension": "liquidity", "severity": "MEDIUM",
                        "title": f"Raise {_ils(_need)} of cash",
                        "why": (f"You hold {_ils(_cash_ils)} liquid against a "
                                f"{_fs2.cash_floor_pct(objective, plan):.0%} floor for a {objective} "
                                f"plan ({_ils(_floor)}). Too little dry powder means a forced sale "
                                f"at a bad moment."),
                        "action": (f"Sell {_sh} {_c['ticker']} (~{_ils(_need)}) to rebuild your cash "
                                   f"floor. {_c['reason']}."),
                        "impact": f"Restores your liquidity floor to {_ils(_floor)}.",
                        "how": [f"Sell {_sh} {_c['ticker']} (~{_ils(_need)})",
                                _c["reason"].capitalize(),
                                "Proceeds land in cash, visible on Holdings"],
                        "est_amount": _need,
                        "apply": {"kind": "trim", "ticker": _c["ticker"], "shares": _sh},
                        "meta": {"cash_ils": _cash_ils, "floor_ils": _floor}})
    except Exception:  # noqa: BLE001
        logger.warning("cash-floor recommendation failed", exc_info=True)
        degraded.append("cash_floor")

    # A price nothing has traded against is not a price. The 30-minute refresh
    # "succeeds" every time on a delisted instrument, so the holding looks
    # healthy while its frozen number is counted in NAV -- and therefore in the
    # gain, the allocation mix, the concentration caps and every recommendation
    # sized against NAV. Surfacing it is GUIDANCE, never an automatic write-off:
    # delisted, merged and renamed all look identical from here, and deciding a
    # holding is worthless is not the app's call.
    try:
        _weights = snap.get("exposure_ticker") or {}
        for _p in rows:
            _m = _p.meta if isinstance(_p.meta, dict) else {}
            if not _m.get("price_stale"):
                continue
            _tk = _p.ticker
            _as_of = str(_m.get("price_as_of") or "")[:10] or "an unknown date"
            _days = _m.get("price_stale_days")
            _w = _weights.get(_tk, 0.0) or 0.0
            _val = _w * nav
            _aged = f"{_days} trading days" if _days else "well over a week"
            recs.append({
                "id": _rid("stale_price", _tk), "dimension": "data", "severity": "HIGH",
                "title": f"{_tk} has not traded since {_as_of}",
                "why": (f"Quotes for {_tk} still come back successfully, but the last actual "
                        f"trade was {_as_of} — {_aged} ago. A price with no trades behind it "
                        f"is a leftover, not a market price, and it looks exactly like a "
                        f"holding that simply did not move."),
                "action": (f"Check what happened to {_tk} — delisted, merged, or renamed — then "
                           f"update or remove the holding. InvestWise will not write it off for "
                           f"you, because it cannot tell which of those it is."),
                "impact": (f"{_ils(_val)} ({_w:.0%} of your book) is currently valued at that "
                           f"frozen price, so your gain, allocation mix and anything sized "
                           f"against NAV all rest partly on it."),
                "how": [f"Look up {_tk} at your broker or the exchange",
                        "If it was delisted or bought out, record the actual proceeds as cash",
                        "If it was renamed, edit the holding to the new ticker",
                        "Until then its price is held at the last real trade, not refreshed"],
                "est_amount": round(_val, 2),
                "meta": {"price_as_of": _m.get("price_as_of"),
                         "trading_days": _days, "value_ils": round(_val, 2)},
            })
    except Exception:  # noqa: BLE001
        logger.warning("stale-price recommendations failed", exc_info=True)
        degraded.append("stale_prices")

    # The stored solver verdict's structural finding, if it has one. Separate
    # from _war_room_recs on purpose: that function needs war-room SIGNALS to
    # have something to attach a constraint to, and this finding is about the
    # book itself. On the live book the war room had one displayed session and
    # it was a Rebalance, so a 21-point ceiling breach would have gone unsaid
    # for want of an unrelated BUY.
    try:
        async with _agent_tx(session):
            recs += await _core_ceiling_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("core-ceiling recommendation failed", exc_info=True)
        degraded.append("solver_verdict")

    # Signals the agent pipeline approved (same decisions the war room shows).
    try:
        async with _agent_tx(session):
            recs += await _war_room_recs(session, user, rows, ledger=_ledger)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations failed", exc_info=True)
        degraded.append("agent_signals")
    _tm.mark("war_room")

    # Trading rules that have fired -> top-priority, user-defined actions.
    try:
        async with _agent_tx(session):
            from app.services.rules_service import triggered_rule_recs
            recs += await triggered_rule_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("triggered trading-rule recommendations failed", exc_info=True)
        degraded.append("trading_rules")

    # The active rule-based strategy changing its mind is the same class of event
    # as a trading rule firing: a discipline the user chose, speaking. Acting
    # late on it is the main reason a rule underperforms its own backtest.
    try:
        async with _agent_tx(session):
            from app.services.strategy_signal_service import discipline_recs, pending_signal_recs
            recs += await pending_signal_recs(session, user)
            recs += await discipline_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("strategy signal recommendations failed", exc_info=True)
        degraded.append("strategy_signals")
    _tm.mark("strategy_signals")

    # Sleeve drift: you chose a size, the market moved you off it.
    #
    # DECIDED, and the distinction matters more than the card: Accept executes
    # the funded rebalance when you ALREADY HOLD some of the sleeve, and refuses
    # when you hold none of it. The +/-5 point band fires at 0% against a chosen
    # 20% too -- but a 20-point gap is not drift, it is never having funded the
    # sleeve, and one tap taking a book from nothing to a fifth in a 3x fund is
    # a different decision from topping up. That one routes to "Fund this
    # sleeve", which shows the whole plan.
    try:
        from app.services import sleeve_service as _sv
        from app.services.strategy_service import sleeve_targets

        # PER TICKER, AT THE SUMMED TARGET -- not per sleeve.
        #
        # The book holds ONE position per ticker. If two sleeves both want TQQQ,
        # measuring drift per sleeve produces two cards about the same holding at
        # two different targets, and acting on either makes the other wrong. That
        # is the P1 duplicate-cap bug in card form, which is why the caps are
        # summed too -- same primitive, same reason.
        _targets = await _sv.all_sleeve_targets(session, user)
        # Which sleeves want each ticker, so Accept can route to the right place.
        _wanted_by: dict[str, list[str]] = {}
        for _s in await _sv.list_sleeves(session, user):
            for _t in (sleeve_targets(_s.strategy_id, _s.sleeve_pct) or {}):
                _wanted_by.setdefault(_t.upper(), []).append(_s.strategy_id)
        if _targets and nav:
            _weights = snap.get("exposure_ticker") or {}
            for _tk, _w in sorted(_targets.items()):
                _tk = _tk.upper()
                _target_pct = _w * 100.0
                _actual_pct = (_weights.get(_tk, 0.0) or 0.0) * 100.0
                _gap = _target_pct - _actual_pct
                if abs(_gap) < SLEEVE_DRIFT_BAND_PCT:
                    continue
                _held = _actual_pct > 0.05
                _amount = abs(_gap) / 100.0 * nav
                _cold = not _held and _gap > 0
                if _cold:
                    recs.append({
                        "id": _rid("sleeve_coldstart", _tk), "dimension": "strategy",
                        "severity": "MEDIUM",
                        "title": f"You chose a {_target_pct:.0f}% {_tk} sleeve and hold none of it",
                        "why": (f"Your plan says {_target_pct:.0f}% of the book in {_tk}; "
                                f"you hold nothing. That is not drift -- the sleeve has "
                                f"never been funded."),
                        "action": ("Use \u201cFund this sleeve\u201d on the Plan tab. It shows "
                                   "which positions would be sold, for how much, and the "
                                   "estimated tax, before anything happens."),
                        "impact": (f"Closing it means moving about {_ils(_amount)} into {_tk}. "
                                   f"Starting a position is a different decision from "
                                   f"correcting a drift, so this card will not do it in one tap."),
                        "how": ["Plan tab \u2192 the strategy card \u2192 Fund this sleeve",
                                "Review every funding leg and its tax, then confirm",
                                "No brokerage order is placed either way"],
                        "est_amount": round(_amount, 2),
                        # NEVER SUPPRESSED. This card carries no `apply` key by
                        # design -- starting a position is a different decision
                        # from correcting a drift, so it deliberately will not
                        # act in one tap. But Accept still FILED it as completed,
                        # which is how a SOXL sleeve chosen at 10% and holding
                        # nothing disappeared from Today for the rest of the TTL
                        # while the Plan tab went on showing 10%.
                        #
                        # "Done" cannot be true of a card that cannot do
                        # anything. And the condition is self-clearing: this card
                        # is only generated while `_cold` holds, i.e. while you
                        # hold none of the sleeve. Hold some and it stops being
                        # emitted. That is the only honest way for it to go away.
                        "persistent": True,
                        "meta": {"ticker": _tk, "chosen_pct": round(_target_pct, 1),
                                 "actual_pct": round(_actual_pct, 1), "cold_start": True},
                    })
                    continue
                _over = _gap < 0
                recs.append({
                    "id": _rid("sleeve_drift", _tk), "dimension": "strategy",
                    "severity": "MEDIUM",
                    "title": (f"{_tk} has drifted to {_actual_pct:.0f}% of a "
                              f"{_target_pct:.0f}% sleeve"),
                    "why": (f"You chose {_target_pct:.0f}%. The market has moved you to "
                            f"{_actual_pct:.0f}%, which is "
                            f"{abs(_gap):.0f} points {'above' if _over else 'below'} it."),
                    "action": ((f"Trim about {_ils(_amount)} of {_tk} back to your "
                                f"{_target_pct:.0f}% sleeve.") if _over else
                               (f"Add about {_ils(_amount)} of {_tk} to get back to your "
                                f"{_target_pct:.0f}% sleeve.")),
                    "impact": ("Puts the sleeve back at the size you chose. Accept executes "
                               "this against your tracked book; no brokerage order is placed."),
                    "how": [f"Accept rebalances {_tk} toward {_target_pct:.0f}%",
                            "Every funding leg and its estimated tax is named first",
                            "The real trade is yours to place at your broker"],
                    "est_amount": round(_amount, 2),
                    "apply": ({"kind": "trim", "ticker": _tk,
                               "shares": max(1, int(_amount / max(
                                   1e-9, next((float(r.current_price or 0) for r in rows
                                               if (r.ticker or "").upper() == _tk), 0) or 1e-9)))}
                              if _over else _fund_action(_wanted_by.get(_tk) or [])),
                    "meta": {"ticker": _tk, "chosen_pct": round(_target_pct, 1),
                             "actual_pct": round(_actual_pct, 1), "cold_start": False},
                })

        # ---- A CAP THAT FIGHTS THE SLEEVE IT SITS ON --------------------- #
        #
        # Found live on 2026-08-25: a 15% `max_weight` on TQQQ, on a book whose
        # `btm_swing_dip` sleeve targets 38.4% and holds 38.59%. The rule was
        # armed by the uptrend card back when TQQQ was small -- both that card
        # and the suggestions list derive the level from the CURRENT weight and
        # neither knew about sleeves -- and nothing re-levelled it when the
        # sleeve grew. The result was permanent: the loudest card on Today told
        # him to sell 61% of a sleeve that was exactly on target.
        #
        # Both sources are fixed above, so this cannot start again. This card is
        # for the caps already armed, and it is deliberately HIGH: an unfixed one
        # does not merely nag, it fires the trim card that `contradictions.py`
        # then has to suppress, and a suppressed card is a card you cannot see.
        try:
            from sqlalchemy import select as _sel

            from app.models.tables import TradingRule as _TRule
            _caps = (await session.scalars(
                _sel(_TRule).where(_TRule.subject == user.email,
                                   _TRule.rule_type == "max_weight",
                                   _TRule.active.is_(True)))).all()
            _stale = []
            for _c in _caps:
                _want = (_targets or {}).get((_c.ticker or "").upper())
                if _want is None:
                    continue
                _want_pct = _want * 100.0
                if float(_c.level or 0) < _want_pct - 0.05:
                    _stale.append({"ticker": (_c.ticker or "").upper(),
                                   "level": round(float(_c.level or 0), 1),
                                   "sleeve_target_pct": round(_want_pct, 1)})
            if _stale:
                _names = ", ".join(f"{x['ticker']} capped at {x['level']:g}% against a "
                                   f"{x['sleeve_target_pct']:g}% sleeve" for x in _stale)
                recs.append({
                    "id": _rid("cap_below_sleeve", *sorted(x["ticker"] for x in _stale)),
                    "dimension": "strategy", "severity": "HIGH",
                    "title": (f"A cap on {_stale[0]['ticker']} is set below the sleeve it governs"
                              if len(_stale) == 1 else
                              f"{len(_stale)} caps are set below the sleeves they govern"),
                    "why": (f"{_names}. The cap fires every time the sleeve is at the size you "
                            f"chose, so the app ends up telling you to sell a position it is "
                            f"also telling you to hold."),
                    "action": ("Accept re-levels each cap to its sleeve target. Caps on tickers "
                               "no sleeve wants are left exactly as you set them."),
                    "impact": ("Stops the false trim alerts. Nothing is bought or sold -- a cap "
                               "is an alert threshold, not an instruction."),
                    "how": [f"Re-levels: {_names}",
                            "Hand-set caps on non-sleeve tickers are never touched",
                            "Change a sleeve size later and the cap follows it"],
                    "apply": {"kind": "relevel_sleeve_caps"},
                    "meta": {"stale_caps": _stale,
                             "tickers": [x["ticker"] for x in _stale]},
                })
        except Exception:  # noqa: BLE001 -- never break Today over a cap audit
            logger.warning("stale-cap audit failed", exc_info=False)
    except Exception:  # noqa: BLE001
        logger.warning("sleeve-drift recommendation failed", exc_info=True)
        degraded.append("sleeve_drift")

    # Protective rules that are suggested but not yet armed ARE "what to do now"
    # by this app's own definition, so they belong on Today. They were reachable
    # only through GET /rules/suggestions, which feeds a panel on a
    # settings-shaped page -- found only by someone already looking for it. One
    # card summarising the set, never one card per rule.
    try:
        async with _agent_tx(session):
            from app.services.rules_service import suggest_rules_for_holdings
            _sugg = await suggest_rules_for_holdings(session, user)
        _flat = [{**r, "ticker": r.get("ticker") or h["ticker"]}
                 for h in (_sugg or []) for r in (h.get("rules") or [])]
        if _flat:
            _specs = [{"ticker": r["ticker"], "rule_type": r["rule_type"],
                       "mode": r.get("mode", "pct"), "level": r["level"],
                       "note": r.get("why")} for r in _flat]
            _names = sorted({r["ticker"] for r in _flat})
            _lines = [f"{r['ticker']}: {r.get('label') or r['rule_type']}" for r in _flat[:8]]
            recs.append({
                # NOT dimension "rule". That is reserved for a TradingRule that
                # has actually FIRED -- the server tracks those by a `rule_<id>`
                # card id, and the banner reconciliation counts them. This card
                # is a suggestion, nothing has fired, and labelling it "rule"
                # made it count toward "N rules triggered" while the server
                # correctly ignored it: banner 1, cards 2, and a FAIL in three
                # separate smoke sections.
                "id": _rid("rules_suggested", *sorted(r.get("ticker", "") for r in _flat)),
                "dimension": "discipline", "severity": "MEDIUM",
                "title": (f"{len(_flat)} protective rules ready to arm"
                          if len(_flat) > 1 else "1 protective rule ready to arm"),
                "why": ("Every level below is derived from the holding's own realized "
                        "volatility, not a round number — a stop inside the noise is churn, "
                        "not discipline. Unarmed, none of them does anything."),
                "action": (f"Arm {len(_flat)} suggested rule(s) across "
                           f"{', '.join(_names)}. Accept arms all of them at once; "
                           f"the Rules tab lets you fine-tune or add them one by one."),
                "impact": ("Each rule then watches its holding on every price refresh and "
                           "raises an alert plus a Today card when it fires."),
                "how": _lines + ["Accept arms all of these as real trading rules",
                                 "No brokerage order is placed — a firing is an alert, "
                                 "and acting on it is yours to do"],
                "apply": {"kind": "create_rules", "rules": _specs},
                "meta": {"count": len(_flat), "tickers": _names},
            })
    except Exception:  # noqa: BLE001
        logger.warning("suggested-rule recommendations failed", exc_info=True)
        degraded.append("rule_suggestions")

    # Cash reconciliation runs HERE, after every agent has contributed -- not at
    # the point the redeploy card is built. Placing it earlier meant the filter
    # ran before _war_room_recs existed, so production showed "Redeploy ₪3,884"
    # (₪971 into SCHD) alongside "Add to SCHD — ₪7,899" and left the user to
    # work out which one to follow. One pot of money, one card.
    if _redeploy:
        _legs = {x["ticker"] for x in _redeploy[0]["apply"]["legs"]}

        def _competes(r: dict) -> bool:
            if r.get("id") == _rid("cashdrag"):
                return True
            _ap = r.get("apply") or {}
            if _ap.get("kind") == "rebalance_to_objective":
                return True
            # Any sized buy of a ticker the redeploy card already funds, whoever
            # emitted it (war room, commodities, momentum...).
            return _ap.get("kind") in ("buy_funded", "buy") and _ap.get("ticker") in _legs
        recs = [r for r in recs if not _competes(r)]
        recs += _redeploy

    # Independent agents can contradict each other; reconcile before display.
    recs = _reconcile(recs, market)


    # Every card carries a plain-language "why" and "impact" (safety net for the
    # holding/hedge/momentum/fee agents that don't set them explicitly).
    _ensure_why_impact(recs)

    # Drop anything the user dismissed (server-side, so push + Today stay in sync).
    dismissed = await load_dismissed(session, user)
    completed = await load_completed(session, user)
    suppressed = done_count = 0
    _hidden_cards: list[dict] = []
    if dismissed or completed:
        # The two mark maps, id -> when-it-was-marked. `load_dismissed` and
        # `load_completed` throw the timestamp away and return bare id sets, and
        # "which id" alone cannot answer "is this the same card I marked" --
        # which is the only question that matters below.
        _dis_map = await _load_map(session, _dismiss_key(user))
        _done_map = await _load_map(session, _done_key(user))
        _marks = {**_dis_map, **_done_map}

        # Self-heal a deadlock before suppressing: a rule can sit latched
        # `triggered` while its card is hidden by an earlier dismissal, so the
        # red "N trading rules triggered" banner counts work with nothing left to
        # click. Any rule acted on before rule-clearing shipped is in exactly
        # that state, and would stay there for the life of the rule. Being
        # suppressed means the user already dealt with it, so resolve it.
        _refired: set[str] = set()
        _hidden_rules = {r.get("id") for r in recs
                         if str(r.get("id", "")).startswith("rule_")} & (dismissed | completed)
        if _hidden_rules:
            from app.services.rules_service import resolve_rule

            # ONLY heal a rule that has not fired AGAIN since you dealt with it.
            #
            # The self-heal was unconditional, and `build_recommendations` runs
            # on every hourly push evaluation -- so a max_weight rule that keeps
            # breaching was re-resolved as `acknowledged` within a second of each
            # firing, forever. Observed live: V at 38% against a 30% cap, MSFT 24
            # against 20, both firing every 30 minutes, both marked handled, and
            # `rule_banner.triggered` rendering empty. One dismissal in August
            # had become permanent consent to a condition that recurs.
            #
            # Dismissing a card says "I have dealt with THIS firing". A later
            # firing is new information and has to reach the screen.
            for _hid in _hidden_rules:
                try:
                    _fired = next(((r.get("meta") or {}).get("triggered_at")
                                   for r in recs if r.get("id") == _hid), None)
                    _dealt = _at(_marks.get(_hid))
                    if _fired and _dealt and _parse_dt(str(_fired)) > _parse_dt(_dealt):
                        # Fired again since. Do not resolve it, and do not
                        # suppress its card either -- healing it while still
                        # hiding it is the deadlock this block exists to break.
                        _refired.add(_hid)
                        continue
                    await resolve_rule(session, user, _hid[5:], "acknowledged")
                except Exception:  # noqa: BLE001 -- never break Today over cleanup
                    logger.warning("could not self-heal rule %s", _hid, exc_info=False)
        # A signal the user already dealt with must not stay pending, or it
        # reappears every morning until it is acted on -- the exact nagging the
        # flip-only design exists to avoid.
        _hidden_sig = {r.get("id") for r in recs
                       if str(r.get("id", "")).startswith("stratsig_")} & (dismissed | completed)
        if _hidden_sig:
            from app.services.strategy_signal_service import (
                active_strategy_ids, resolve_signal, signal_card_id)
            try:
                # Resolve the sleeve whose card was actually dismissed. This used
                # to clear `active_strategy_id` -- the FIRST sleeve -- whichever
                # card had been handled, so on a two-sleeve book dismissing the
                # factor sleeve's flip silently consumed the SOXL sleeve's
                # pending signal and left the factor one still armed. Exactly
                # backwards, and silent.
                for _sid in await active_strategy_ids(session, user):
                    if signal_card_id(_sid) in _hidden_sig:
                        await resolve_signal(session, user, _sid)
            except Exception:  # noqa: BLE001 -- never break Today over cleanup
                logger.warning("could not clear a handled strategy signal", exc_info=False)
        # A MUTE MUST NOT OUTLIVE THE CONDITION.
        #
        # Two ways it did. First, a rule that fired again after you dealt with
        # it: same card id, new event, and the old mark went on hiding it --
        # collected in `_refired` above.
        #
        # Second, "Done" on a HIGH card. Done is the stronger statement and gets
        # the longer window, which is right for a MEDIUM nudge and wrong for a
        # HIGH one: HIGH means the app thinks this is hurting you now, and if it
        # is STILL saying that a week after you filed it, the filing was
        # premature and you should see it again. So a HIGH card gets the ignore
        # window, not the done window.
        #
        # Note what this deliberately does NOT do: make HIGH cards
        # undismissable. That was on the table and it is worse -- a button that
        # visibly does nothing teaches you to distrust every other button. The
        # structural fix for a mute landing on the wrong card is the
        # content-bearing id, which is elsewhere in this change; this is only
        # about a mute on the RIGHT card outliving the thing it muted.
        # A card that declares itself persistent describes a standing condition
        # it cannot itself resolve, so filing it as "done" states something
        # untrue. It goes away when the condition does -- by not being generated.
        _persistent = {r.get("id") for r in recs if r.get("persistent")}
        _high = {r.get("id") for r in recs if r.get("severity") == "HIGH"}
        _high_cut = datetime.now(timezone.utc) - timedelta(days=_DISMISS_TTL_DAYS)
        _stale_high = {rid for rid in (completed & _high)
                       if _parse_dt(_at(_done_map.get(rid)) or _now_iso()) < _high_cut}

        # THE THIRD WAY A MUTE OUTLIVED ITS CONDITION, and the general one: the
        # card still describes the same category and the same ticker, so it
        # keeps the same id and the same mark -- while the number it is asking
        # for has moved somewhere else entirely. See `moved_on`.
        _moved: dict[str, str] = {}
        for _r in recs:
            _rid_ = _r.get("id")
            if _rid_ not in dismissed and _rid_ not in completed:
                continue
            # WHICH MAP THE MARK CAME FROM IS THE WHOLE QUESTION for PR 38's
            # branch: "done" is a claim about the book and can be contradicted
            # by it, "ignored" is a claim about your attention and cannot.
            _why = moved_on(_marks.get(_rid_), _r, done=(_rid_ in completed))
            if _why:
                _moved[_rid_] = _why

        dismissed = dismissed - _refired - _persistent - set(_moved)
        completed = completed - _refired - _stale_high - _persistent - set(_moved)

        # A CARD THAT COMES BACK MUST SAY WHY IT CAME BACK. Reappearing without
        # explanation reads as the app having forgotten you dealt with it, which
        # is the complaint the mute exists to prevent.
        for _r in recs:
            if _r.get("id") in _moved:
                _r["refired_because"] = _moved[_r["id"]]

        # WHAT IS HIDDEN, NOT JUST HOW MANY. The card already carried a count
        # and the screen already showed it -- "4 other suggestions are ignored".
        # A count cannot answer the question the user actually had, which was
        # "why is there no card telling me to fix my mix": the rebalance pair
        # was inside that 4 and nothing on the screen said so.
        for _r in recs:
            _rid_ = _r.get("id")
            if _rid_ in dismissed or _rid_ in completed:
                _hidden_cards.append({
                    "id": _rid_, "title": _r.get("title"),
                    "severity": _r.get("severity"), "dimension": _r.get("dimension"),
                    "how": ("done" if _rid_ in completed else "ignored"),
                    "hidden_at": _at(_marks.get(_rid_)) or None,
                    "est_amount": _r.get("est_amount")})
        _hidden_cards.sort(key=lambda x: _SEV.get(x.get("severity") or "", 9))

        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in dismissed]
        suppressed = _n - len(recs)
        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in completed]
        done_count = _n - len(recs)

    # FINAL RECONCILIATION. The red banner counts rules whose `triggered` flag is
    # set; the cards come from triggered_rule_recs. Two sources, so they can
    # drift for any reason -- and every reason ends the same way: a banner
    # counting work with nothing left to click, forever, because only a card can
    # clear it. The heal above fixes the case where a card existed and was
    # hidden. This catches every other case, whatever caused it, by asserting
    # the invariant directly: a triggered rule with no visible card is a
    # contradiction, so resolve it.
    #
    # Guarded on the rules agent having succeeded. If it degraded, the absence
    # of cards says nothing about the rules and retiring them would destroy real
    # pending work over a transient provider failure.
    rule_banner = {"triggered": [], "carded": [], "healed": [], "skipped_reason": None}
    if "trading_rules" in degraded:
        # Say so, rather than leaving a silent no-op. A banner that disagrees
        # with the cards while the rules agent is down looks identical to a
        # reconciliation that ran and failed, and that ambiguity cost several
        # wrong hypotheses about exactly this failure.
        rule_banner["skipped_reason"] = (
            "the trading-rules agent degraded, so missing cards say nothing "
            "about the rules and nothing was retired")
    if "trading_rules" not in degraded:
        try:
            from app.services.rules_service import resolve_rule
            from sqlalchemy import select as _select

            from app.models.tables import TradingRule as _TR
            _live = (await session.scalars(
                _select(_TR).where(_TR.subject == user.email, _TR.active.is_(True),
                                   _TR.triggered.is_(True)))).all()
            _carded = {str(r.get("id", ""))[5:] for r in recs
                       if str(r.get("id", "")).startswith("rule_")}
            for _r in _live:
                _short = str(_r.id)[:8]
                rule_banner["triggered"].append(_short)
                if _short in _carded:
                    rule_banner["carded"].append(_short)
                    continue
                try:
                    await resolve_rule(session, user, _short, "acknowledged",
                                       {"reason": "triggered with no visible card"})
                    rule_banner["healed"].append(_short)
                    logger.info("healed a rule triggered with no card: %s %s",
                                _r.ticker, _short)
                except Exception:  # noqa: BLE001 -- never break Today over cleanup
                    logger.warning("could not heal orphaned rule %s", _short, exc_info=False)
        except Exception:  # noqa: BLE001
            logger.warning("rule banner reconciliation failed", exc_info=False)

    recs.sort(key=lambda r: _SEV.get(r["severity"], 9))
    _tm.mark("reconcile_and_filter")
    # Phase 3.3: validate the Risk Agent's beta against history before surfacing.
    bt_holdings = [{"ticker": d["ticker"], "asset_class": d.get("asset_class") or "Equities",
                    "value_ils": d["quantity"] * d["current_price"]} for d in pdicts]
    bt = BacktestEngine().run(bt_holdings, portfolio_vol_pct=snap["avg_volatility_pct"])
    _tm.mark("backtest")

    # ---- WHICH STRATEGY DOES EACH CARD SERVE, AND DO ANY OF THEM FIGHT? ----
    #
    # Both run AFTER reconciliation and sorting, on the cards that will actually
    # be served. Scoping a merged card from whichever leg happened to be first
    # would label it wrong, and checking contradictions before the merge would
    # spend the work on duplicates `_reconcile` was about to remove anyway.
    #
    # Neither may break Today: a card with a missing label is worse than no
    # labels, and a Today screen that 500s because a label failed is worse than
    # both. So the fallback stamps every card `book` and carries on.
    contradictions: list[dict] = []
    try:
        from app.services import sleeve_service as sv
        from app.services.card_scope import annotate as _scope
        from app.services.contradictions import check as _contra

        recs = await _scope(session, user, recs)
        _sleeve_tgts = await sv.all_sleeve_targets(session, user)
        # FX-NORMALISED, from the ORM rows, exactly as `current_mix` does it.
        # `pdicts` carries quantity x current_price in the PRICE's currency, and
        # comparing a USD-denominated weight against an ILS NAV is the bug that
        # minted shares in PR 1. A sleeve-target comparison built on it would
        # under-report every USD holding's weight by the FX rate and suppress
        # trims that were correct.
        from app.services.fx import fx_rate as _fxr2, price_currency as _pcy2
        _w = {}
        if nav:
            for _p in rows:
                _meta = _p.meta if isinstance(_p.meta, dict) else {}
                _v = (float(_p.quantity) * float(_p.current_price or 0)
                      * _fxr2(_pcy2(_p.market, _meta)))
                _tk = str(_p.ticker or "").upper()
                _w[_tk] = _w.get(_tk, 0.0) + _v / nav
        recs, contradictions = _contra(recs, sleeve_targets=_sleeve_tgts, nav=nav,
                                       weights=_w, book_target=btgt["target"])
        if contradictions:
            logger.info("today: %d contradiction(s), %d card(s) suppressed",
                        len(contradictions), sum(1 for c in contradictions if c.get("suppressed")))
    except Exception:  # noqa: BLE001
        logger.warning("card scope / contradiction pass failed", exc_info=False)
        for _r in recs:
            _r.setdefault("scope", {"kind": "book", "strategy_id": None,
                                    "name": None, "label": "Whole book"})
    _tm.mark("scope_and_contradictions")

    return {"count": len(recs), "objective": objective, "recommendations": recs[:12],
            "market": market,
            # What the book is managed to, and where each half came from. On the
            # payload rather than only inside a card, so a smoke test can assert
            # sleeves + core == target without parsing English.
            "book_target": btgt,
            # Cards that argued with a sleeve or with each other. Empty is the
            # expected state; non-empty is either a real conflict for the user
            # to arbitrate or a bug in a generator, and each entry says which.
            "contradictions": contradictions,
            # Honesty signals for the Today empty state: "nothing to do",
            # "you ignored everything" and "an agent failed" are three different things.
            "dismissed_count": suppressed,
            "completed_count": done_count,
            # ...and WHICH ones. The count was always here and the screen always
            # showed it; a count cannot tell you the rebalance pair is inside it.
            "hidden_cards": _hidden_cards,
            "degraded": degraded, "rule_banner": rule_banner,
            # Where the time actually went, per agent (ms). Cheap to compute and
            # it turns "the endpoint is slow" into a specific culprit.
            "timings_ms": {**_tm.marks, "slowest": _tm.slowest()},
            "buy_ideas": _ideas,
            # The tilt these ideas were ranked under, at the top level where a
            # reader will actually see it -- and the tilt_disclosure is None when
            # nothing was tilted, so a Balanced book is not told about a change
            # it did not get.
            #
            # The pin is lifted off the ideas rather than recomputed, so it
            # carries the COVERAGE the scan actually achieved. Recomputing it
            # here would produce a pin that describes the intent instead of the
            # run, which is the difference that matters when someone asks why a
            # name never appears.
            #
            # AND IT HAS TO SURVIVE A SCAN THAT PRODUCED NO EQUITIES. `_pin` was
            # attached only to equity picks, so when `screen_equities` returned
            # nothing this `next(...)` fell through to `audit_block`, which has
            # no `coverage` key at all -- the diagnostic vanished in precisely
            # the situation it exists to explain. Reproduced against PR 25's own
            # bug shape: the agent held {"requested": 58, "ranked": 0} and the
            # payload said nothing. PR 25's smoke script names this as a known
            # defect ("it rides on equity ideas, and there may be none").
            # `_pin_out` is filled by the scan itself, so it is there whether or
            # not anything was ranked.
            "buy_ideas_ranked_for": (
                next((i["ranked_for"] for i in (_ideas or []) if i.get("ranked_for")), None)
                or _pin_out.get("ranked_for")
                or _obj_weights.audit_block(objective)),
            "buy_ideas_tilt_disclosure": _obj_weights.tilt_disclosure(objective),
            "risk_validation": {"beta_validated": bt.beta_validated,
                                "structural_beta": bt.structural_beta,
                                "risk_implied_beta": bt.risk_implied_beta,
                                "worst_event": bt.worst_event,
                                "worst_portfolio_drawdown_pct": bt.worst_portfolio_drawdown_pct,
                                "critique": bt.critique}}


# expected annual return by objective (rough asset-class blend; used to size the gap)
_OBJ_RETURN = {"Grow": 8.5, "Balanced": 6.5, "Preserve": 4.0, "Income": 5.0}


def _behind_goal_recs(plan, snap, objective, projected_override=None) -> list[dict]:
    """When the plan won't reach the target on the current path, recommend the
    concrete levers to close the gap (each with a machine-applyable spec).

    projected_override: when provided (the Monte-Carlo median from the app's home
    panel), it is used as the "current path" projection so the card, the digest and
    "Where this could end up" all agree on one number instead of diverging."""
    out: list[dict] = []
    if plan is None or not getattr(plan, "target_amount", None):
        return out
    nav = snap["nav"]
    target = float(plan.target_amount)
    r = (_OBJ_RETURN.get(objective, 6.5)) / 100.0
    years = max(1, int(getattr(plan, "horizon_years", 10) or 10))
    # try to use the deadline year if present
    try:
        import datetime as _dt
        yr = int(str(getattr(plan, "target_date", "") or "")[:4])
        years = max(1, yr - _dt.date.today().year) or years
    except (ValueError, TypeError):
        pass
    deterministic = nav * (1 + r) ** years
    if projected_override and projected_override > 0:
        projected, proj_basis = float(projected_override), "montecarlo"
    else:
        projected, proj_basis = deterministic, "fixed-return"
    if projected >= target:
        return out  # on track on the current path

    gap = target - projected
    behind_pct = round((1 - projected / target) * 100) if target else 0
    sev = "CRITICAL" if behind_pct >= 40 else "HIGH"

    # Lever A — add money: required monthly contribution (future value of an annuity)
    rm = r / 12.0
    n = years * 12
    pmt = gap * rm / (((1 + rm) ** n) - 1) if rm else gap / n
    out.append({"id": _rid("behind", "contrib"), "dimension": "goal", "severity": sev,
                "title": "You're behind — add a monthly contribution",
                "why": f"On the current path you'd reach about {_ils(projected)} of your {_ils(target)} goal "
                       f"(~{behind_pct}% short), so nothing changes unless you add to it.",
                "action": f"On the current path you'd reach about {_ils(projected)} of your {_ils(target)} goal "
                          f"(~{behind_pct}% short). Investing about {_ils(pmt)}/month would close the gap by your deadline.",
                "impact": f"About {_ils(pmt)}/month closes the ~{_ils(gap)} gap and puts your {_ils(target)} goal "
                          f"back within reach by your deadline.",
                "how": [f"Set up a standing order of ~{_ils(pmt)}/month into this portfolio",
                        "Keep the same mix — regular contributions do the heavy lifting",
                        "Re-check here as your balance grows"],
                "est_amount": round(pmt, 2),
                "apply": {"kind": "none"}})
    _proj_formula = (f("Projection", "projected = Monte-Carlo median (10,000 sims)",
                       substituted=f"NAV {nav:,.0f}, {years}y", result=f"₪{projected:,.0f}")
                     if proj_basis == "montecarlo" else
                     f("Projection", "projected = NAV x (1+r)^years",
                       substituted=f"{nav:,.0f} x (1+{r:.3f})^{years}", result=f"₪{projected:,.0f}"))
    out[-1]["audit_trail"] = audit_for("goal",
        raw_data={"nav": round(nav, 2), "target": round(target, 2), "projected": round(projected, 2),
                  "projection_basis": proj_basis, "behind_pct": behind_pct,
                  "annual_return_pct": round(r * 100, 2), "years": years},
        formulas=[
            _proj_formula,
            f("Monthly contribution", "pmt = gap x (r/12) / ((1+r/12)^(12*years) - 1)",
              result=f"₪{pmt:,.0f}/mo")])

    # Lever B — shift to a higher-growth mix (only if not already Grow)
    if objective != "Grow":
        proj_grow = nav * (1 + _OBJ_RETURN["Grow"] / 100.0) ** years
        out.append({"id": _rid("behind", "grow"), "dimension": "goal", "severity": "HIGH",
                    "title": "Shift to a higher-growth mix",
                    "action": f"Switching from {objective} to a Grow mix raises expected growth, lifting the "
                              f"projection to about {_ils(proj_grow)} — but with bigger swings along the way.",
                    "how": ["Change your objective to Grow (more equities, fewer bonds)",
                            "Accepting this rebalances your holdings toward the Grow target",
                            "Make sure the extra volatility still fits your risk tolerance"],
                    "est_amount": round(proj_grow - projected, 2),
                    "apply": {"kind": "set_objective_and_rebalance", "objective": "Grow"}})

    # Lever C — extend the horizon
    import math
    need_years = math.log(target / nav) / math.log(1 + r) if nav > 0 and r > 0 else years + 5
    if need_years > years:
        new_years = int(math.ceil(need_years))
        out.append({"id": _rid("behind", "horizon"), "dimension": "goal", "severity": "MEDIUM",
                    "title": "Give it more time",
                    "action": f"At your current mix you'd need about {new_years} years (vs {years}) to reach "
                              f"{_ils(target)}. Extending the deadline makes the goal realistic without extra risk.",
                    "how": [f"Push your horizon out to ~{new_years} years",
                            "Accepting this updates your plan's horizon",
                            "Your projection and odds update immediately"],
                    "est_amount": None,
                    "apply": {"kind": "set_plan", "fields": {"horizon_years": new_years}}})

    # Lever D — set a realistic target
    out.append({"id": _rid("behind", "target"), "dimension": "goal", "severity": "LOW",
                "title": "Set a target you'll actually hit",
                "action": f"A realistic target on the current path is about {_ils(projected)} by your deadline.",
                "how": [f"Lower your target to ~{_ils(projected)}",
                        "Accepting this updates your plan's target amount",
                        "You can always raise it again later"],
                "est_amount": round(projected, 2),
                "apply": {"kind": "set_plan", "fields": {"target_amount": round(projected, 2)}}})
    return out


# ---------------------------------------------------------------------------
# Phase 4 - "manage my holdings" agents
# ---------------------------------------------------------------------------

def _fundamentals(ticker: str):
    """Best-effort fundamentals; None on any provider issue (never raises)."""
    try:
        from app.providers.registry import guarded_fundamentals
        return guarded_fundamentals(ticker)
    except Exception:
        return None


def _fund_score(f) -> float | None:
    """A compact 0-100 quality-of-fundamentals score for a single name."""
    if f is None:
        return None
    s, n = 0.0, 0
    if f.pe is not None:
        s += 100.0 if 0 < f.pe <= 15 else (60.0 if 0 < f.pe <= 30 else (20.0 if f.pe > 0 else 0.0))
        n += 1
    if f.earnings_growth_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.earnings_growth_pct * 2.0))
        n += 1
    if f.roe_pct is not None:
        s += max(0.0, min(100.0, f.roe_pct * 2.5))
        n += 1
    if f.profit_margin_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.profit_margin_pct * 1.5))
        n += 1
    if f.debt_to_equity is not None:
        s += max(0.0, min(100.0, 100.0 - f.debt_to_equity / 2.5))
        n += 1
    return round(s / n, 1) if n else None


def _holding_verdict_recs(rows, snap, cap: float, trimmed: set) -> list[dict]:
    """A Buy-more / Hold / Trim verdict on each position you already own."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav:
        return out
    weights = snap["exposure_ticker"]
    for p in rows:
        tk = p.ticker
        f = _fundamentals(tk)
        score = _fund_score(f)
        if score is None:
            continue
        w = weights.get(tk, 0.0)
        price = float(p.current_price or 0)
        cost = float(p.cost_basis or 0)
        gain = price > cost
        if score >= 65 and w < cap * 0.6:
            verdict, sev = "Buy more", "LOW"
            action = (f"{tk} screens well (fundamentals {score:.0f}/100) and is only {w:.0%} of your book "
                      f"— adding on weakness is reasonable if it fits your plan.")
            how = [f"Consider topping up {tk} toward your target weight",
                   "Use limit orders and average in rather than buying all at once",
                   "Keep it under your concentration limit"]
        elif score < 40 and tk not in trimmed:
            verdict, sev = "Trim", "MEDIUM"
            action = (f"{tk} screens poorly on fundamentals ({score:.0f}/100)"
                      f"{' and you are sitting on a gain' if gain else ''} "
                      f"— consider trimming and redeploying into stronger names.")
            how = [f"Sell part of {tk} (start with ~25-50% of the position)",
                   "Redeploy into higher-scoring holdings or your target mix",
                   "Mind the tax on any realized gain"]
        else:
            verdict, sev = "Hold", "LOW"
            action = (f"{tk} looks fairly valued on fundamentals ({score:.0f}/100) at {w:.0%} of your book "
                      f"— no action needed; keep holding.")
            how = [f"Keep {tk} as-is",
                   "Re-check if the thesis or fundamentals change",
                   "Rebalance only if it drifts past your limit"]
        out.append({"id": _rid("verdict", tk), "dimension": "holding", "severity": sev,
                    "title": f"{verdict}: {tk}", "action": action, "how": how,
                    "est_amount": None, "apply": {"kind": "none"},
                    # `ticker` on meta so the card can be SCOPED. Without it a
                    # "Hold: TQQQ" verdict reads as whole-book advice about a
                    # position that is in fact 38.4% of a sleeve you chose --
                    # the one card where knowing whose position it is changes
                    # how you read the verdict.
                    "meta": {"ticker": tk, "verdict": verdict, "fundamental_score": score,
                             "metrics": (f.model_dump() if f else None)}})
        out[-1]["audit_trail"] = audit_for("holding",
            raw_data={"ticker": tk, "weight": round(w, 4), "fundamental_score": score},
            formulas=[_fml("Fundamental score",
                        "score = mean(value, growth, quality, leverage)",
                        result=f"{score:.0f}/100")])
    return out


def _hedge_recs(rows, snap) -> list[dict]:
    """Flag sector/factor concentration and suggest a diversifier or hedge."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav or len(rows) < 2:
        return out
    sector_w: dict[str, float] = {}
    for p in rows:
        f = _fundamentals(p.ticker)
        sector = (f.sector if f and f.sector else "Unknown")
        val = float(p.quantity) * float(p.current_price or 0)
        sector_w[sector] = sector_w.get(sector, 0.0) + val / nav
    sector_w.pop("Unknown", None)
    if not sector_w:
        return out
    top_sector = max(sector_w, key=sector_w.get)
    w = sector_w[top_sector]
    if w >= 0.40:
        out.append({"id": _rid("hedge", top_sector), "dimension": "risk", "severity": "MEDIUM",
                    "title": f"Reduce {top_sector} concentration",
                    "action": (f"About {w:.0%} of your equity sits in {top_sector}. A shock to that one "
                               f"sector would hit you hard — diversify or add a hedge."),
                    "how": [f"Trim your most expensive {top_sector} names",
                            "Rotate the proceeds into under-represented sectors (e.g. an XLV/XLE/XLF sleeve)",
                            "Or add a low-correlation diversifier (bonds/BND, gold/GLD, or BTAL)"],
                    "est_amount": None, "apply": {"kind": "none"},
                    "meta": {"sector": top_sector, "weight": round(w, 4)}})
        out[-1]["audit_trail"] = audit_for("risk",
            raw_data={"sector": top_sector, "weight": round(w, 4)},
            formulas=[_fml("Sector weight", "w = sector_value / NAV", result=f"{w:.0%}")])
    return out


def _daily_vol_pct(closes: list[float], window: int = 21) -> float | None:
    """Std-dev of daily returns over the last `window` sessions, in percent.

    Returns None when there isn't enough clean history to be meaningful — callers
    fall back to a conservative fixed buffer rather than inventing a number.
    """
    tail = closes[-(window + 1):]
    rets = [tail[i] / tail[i - 1] - 1.0 for i in range(1, len(tail)) if tail[i - 1]]
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * 100.0


def stop_buffer_pct(closes: list[float], *, horizon_days: int = 10, k: float = 1.5,
                    lo: float = 8.0, hi: float = 20.0) -> float:
    """A volatility-scaled buffer = k std-devs of a `horizon`-day move, clamped to
    a sane band. Grounded in the holding's own realized volatility (not a guess);
    falls back to the low bound when volatility can't be computed."""
    v = _daily_vol_pct(closes)
    if v is None:
        return lo
    raw = k * v * math.sqrt(horizon_days)
    return float(min(hi, max(lo, round(raw))))


def _momentum_recs(rows, snap, sleeve_floor: dict | None = None) -> list[dict]:
    """Surface holdings in a strong down- or up-trend and turn each into a
    concrete, one-click discipline rule (stop-loss / trailing stop). Accept arms
    the rule — the app never trades; a hit raises an alert + a Today to-do."""
    out: list[dict] = []
    weights = (snap or {}).get("exposure_ticker") or {}
    for p in rows:
        try:
            from app.providers.registry import guarded_history
            hist = guarded_history(p.ticker, days=200)
        except Exception:
            hist = None
        closes = [c for _d, c in hist] if hist else []
        if len(closes) < 60:
            continue
        long_ma = sum(closes[-150:]) / len(closes[-150:]) if len(closes) >= 150 else sum(closes) / len(closes)
        short_ma = sum(closes[-30:]) / 30.0
        last = closes[-1]
        ret = (last / closes[-min(120, len(closes))] - 1.0) * 100.0
        weight_pct = round((weights.get(p.ticker, 0) or 0) * 100, 1)
        if short_ma < long_ma * 0.95 and ret < -10:
            buf = stop_buffer_pct(closes, k=1.5, lo=8.0, hi=15.0)
            stop = round(last * (1 - buf / 100.0), 2)
            rule = {"ticker": p.ticker, "rule_type": "stop_loss", "mode": "price", "level": stop,
                    "note": f"From downtrend alert (~{buf:.0f}% below {last:.2f})"}
            out.append({"id": _rid("mom_dn", p.ticker), "dimension": "momentum", "severity": "MEDIUM",
                        "title": f"{p.ticker} is in a downtrend",
                        "action": (f"{p.ticker} is down ~{abs(ret):.0f}% and trading below its trend. "
                                   f"Cap further downside with a concrete stop instead of drifting."),
                        "why": (f"{p.ticker} has broken below its longer-term trend, so the odds of further "
                                f"downside are higher — a pre-set stop makes the exit decision now, not in a panic."),
                        "impact": (f"Accept arms a stop-loss on {p.ticker} at {stop:.2f} (~{buf:.0f}% below today's "
                                   f"{last:.2f}). If it falls to there you get an alert + a Today to-do to sell."),
                        "how": [f"Accept → arms a stop-loss at {stop:.2f} (~{buf:.0f}% below today)",
                                f"You get an alert + a Today to-do if {p.ticker} reaches it",
                                "Adjust or remove it any time under Trading rules"],
                        "est_amount": None,
                        "apply": {"kind": "create_rules", "rules": [rule]},
                        "meta": {"return_pct": round(ret, 1), "trend": "down", "stop": stop, "buffer_pct": buf}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "down",
                          "last": round(last, 2), "buffer_pct": buf},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%"),
                          _fml("Buffer", "buffer = clamp(1.5 x daily_vol x sqrt(10), 8%, 15%)", result=f"{buf:.0f}%"),
                          _fml("Stop level", "stop = last x (1 - buffer)", result=f"{stop:.2f}")])
        elif short_ma > long_ma * 1.05 and ret > 12:
            buf = stop_buffer_pct(closes, k=2.0, lo=10.0, hi=20.0)
            rules = [{"ticker": p.ticker, "rule_type": "trailing_stop", "mode": "pct", "level": buf,
                      "note": f"From uptrend alert (protect gains, {buf:.0f}% trail)"}]
            cap_line = ""
            if weight_pct >= 12:
                cap = float(max(15.0, 5 * math.ceil(weight_pct / 5)))
                # NEVER BELOW THE SLEEVE TARGET. This line, and its twin in
                # rules_service, wrote the 15% TQQQ cap on a book whose sleeve
                # targets 38.4% -- so accepting an "uptrend, protect your gains"
                # card armed a rule that fought a strategy, permanently, and put
                # "TQQQ is 39% of your portfolio - trim" at the top of Today.
                _floor = (sleeve_floor or {}).get(p.ticker.upper())
                if _floor and _floor > cap:
                    cap = float(5 * math.ceil(_floor / 5))
                    rules.append({"ticker": p.ticker, "rule_type": "max_weight", "mode": "pct",
                                  "level": cap,
                                  "note": f"Sleeve targets {_floor:g}%; caps {p.ticker} at {cap:.0f}%"})
                    cap_line = (f" Your sleeve targets {_floor:g}% of the book in {p.ticker}, so the cap "
                                f"goes at {cap:.0f}% — above the target, not against it.")
                else:
                    rules.append({"ticker": p.ticker, "rule_type": "max_weight", "mode": "pct", "level": cap,
                                  "note": f"Keep {p.ticker} under {cap:.0f}% of the book"})
                    cap_line = f" It's already {weight_pct:.0f}% of your book, so this also caps it at {cap:.0f}%."
            how = [f"Accept → arms a {buf:.0f}% trailing stop (protects gains, lets it run)"]
            if len(rules) > 1:
                how.append(f"Also caps {p.ticker} at {rules[-1]['level']:.0f}% of your portfolio")
            how.append("You get an alert + a Today to-do if either triggers")
            out.append({"id": _rid("mom_up", p.ticker), "dimension": "momentum", "severity": "LOW",
                        "title": f"{p.ticker} is in an uptrend",
                        "action": (f"{p.ticker} is up ~{ret:.0f}% and trending higher. Let the winner run, "
                                   f"but lock in a floor so a reversal doesn't give it all back."),
                        "why": (f"Trends tend to persist, so the play is to stay in {p.ticker} while protecting "
                                f"the gain — not to sell early on strength."),
                        "impact": (f"Accept arms a {buf:.0f}% trailing stop on {p.ticker}: it keeps running with the "
                                   f"trend, but if it drops {buf:.0f}% from its peak you get an alert to take "
                                   f"profit.{cap_line}"),
                        "how": how,
                        "est_amount": None,
                        "apply": {"kind": "create_rules", "rules": rules},
                        "meta": {"return_pct": round(ret, 1), "trend": "up", "trail_pct": buf,
                                 "weight_pct": weight_pct}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "up", "weight_pct": weight_pct},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%"),
                          _fml("Trailing stop", "trail = clamp(2 x daily_vol x sqrt(10), 10%, 20%)", result=f"{buf:.0f}%")])
    return out[:3]


def _redeploy_cash_recs(rows, snap, plan, objective, cap, cash_ils,
                        book_mix: dict | None = None) -> list[dict]:
    """Surplus cash -> one sized, executable redeployment plan.

    After a stop-loss fires and you accept it, the proceeds land in cash and the
    portfolio drifts away from its plan. The old "Put idle cash to work" card
    noticed this but was `apply: none` -- it said "move the rest into your target
    mix" without naming what, sizing it, or being able to do it.

    Allocation is derived, never invented:
      * spendable  = cash above the objective's floor (Preserve 10% .. Grow 3%);
      * candidates = every asset class under its target weight, filled first by
        the holdings you already own that sit below their share of that class,
        then by screener picks for classes with no representation at all;
      * each leg is clipped by the single-name concentration cap, and legs below
        the minimum trade size are dropped rather than rounded up.
    """
    from app.services import funding_service as _fund

    nav = snap.get("nav") or 0.0
    if not nav or cash_ils <= 0:
        return []
    # The book's currency decides whether US municipal bonds are a real option.
    _ccy = (getattr(plan, "currency", None) or "ILS")
    spendable = _fund.spendable_cash(cash_ils, nav, objective, plan)
    if spendable < _fund.MIN_TRADE_ILS:
        return []

    mix, _ = current_mix(rows)
    # The BOOK's target when the caller has one. Reading OBJ_TARGET here meant a
    # book with a chosen core redeployed its cash against the objective while
    # the rebalance card in the same payload used the core -- two answers, one
    # screen. OBJ_TARGET stays as the fallback for a book with neither.
    target = book_mix or OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"])
    # Weights are computed from the rows themselves rather than read out of
    # snap["exposure_ticker"]. That lookup silently returned 0 for every ticker,
    # so size_purchase saw "current weight 0" for a name at 29% and handed every
    # leg an identical unclipped slice -- proposing to buy MORE of a holding
    # already past the concentration cap. A self-contained calculation cannot
    # drift from whatever keys the caller's snapshot happens to use.
    from app.services.fx import fx_rate as _fxr, price_currency as _pcy
    weights: dict[str, float] = {}
    for r in rows:
        _m = getattr(r, "meta", None)
        _rate = _fxr(_pcy(getattr(r, "market", None), _m if isinstance(_m, dict) else None))
        _v = float(getattr(r, "quantity", 0) or 0) * float(getattr(r, "current_price", 0) or 0) * _rate
        weights[r.ticker] = weights.get(r.ticker, 0.0) + (_v / nav if nav else 0.0)
    held_by_class: dict[str, list] = {}
    for r in rows:
        # The meta asset_class must be passed: classify() falls back to ticker
        # heuristics otherwise, and "CASH" matches none of them -- so the cash
        # row itself would land in Equities and become a top-up target, i.e. the
        # app proposing to buy cash with cash.
        cls = classify_holding(r)
        if cls and cls.lower() != "cash":
            held_by_class.setdefault(cls, []).append(r)

    # Gaps per asset class, largest shortfall first.
    gaps = []
    for cls, tw in (target or {}).items():
        if cls.lower() == "cash" or not tw:
            continue
        gap_ils = max(0.0, (float(tw) - float(mix.get(cls, 0.0)))) * nav
        if gap_ils >= _fund.MIN_TRADE_ILS:
            gaps.append((cls, gap_ils, float(tw)))
    if not gaps:
        return []
    gaps.sort(key=lambda g: g[1], reverse=True)

    total_gap = sum(g[1] for g in gaps)
    legs: list[dict] = []
    unplaced: list[dict] = []
    remaining = spendable
    for cls, gap_ils, tw in gaps:
        if remaining < _fund.MIN_TRADE_ILS:
            break
        # Proportional share of the surplus, never more than the class actually needs.
        budget = min(gap_ils, round(spendable * (gap_ils / total_gap), 2), remaining)
        held = held_by_class.get(cls) or []
        if held:
            # Top up what you already own, weakest weight first.
            held.sort(key=lambda p: weights.get(p.ticker, 0.0))
            per = round(budget / len(held), 2)
            for p in held:
                if remaining < _fund.MIN_TRADE_ILS:
                    break
                room = _fund.name_room_ils(nav, weights.get(p.ticker, 0.0), cap)
                amt = round(min(per, room, remaining), 2)
                if amt < _fund.MIN_TRADE_ILS:
                    continue
                legs.append({"ticker": p.ticker, "amount_ils": amt, "asset_class": cls,
                             "reason": f"{cls} is {mix.get(cls, 0.0):.0%} vs a {tw:.0%} target"})
                remaining = round(remaining - amt, 2)
        else:
            # The plan wants this class and you hold none of it -- a real gap, so
            # a new name is warranted rather than concentrating further.
            #
            # TILTED TOO, and this is the site where it matters most: this pick
            # becomes a NEW POSITION in a class the book is empty of. Ranking
            # that on a growth-weighted default while the plan says Preserve is
            # the contradiction item 2 is about, arriving as an actual holding.
            pick = next((b for b in _buy_ideas(snap, _ccy, objective)
                         if classify(b.get("ticker"), None) == cls), None)
            amt = round(min(budget, remaining), 2)
            if pick and amt >= _fund.MIN_TRADE_ILS:
                # THE DISCLOSURE RIDES WITH THE LEG, not just the audit trail.
                # This leg names a security to buy, chosen by a factor tilt that
                # nobody has signed off. Recording that in a nested audit block
                # nobody opens is how "12%/yr, 55% vol, buy-and-hold" reached
                # four screens looking measured. It goes in the sentence.
                _why = f"you hold no {cls}; the plan targets {tw:.0%}"
                _tilt = _obj_weights.tilt_disclosure(objective)
                if _tilt:
                    _why += f" -- {_tilt}"
                legs.append({"ticker": pick["ticker"], "amount_ils": amt, "asset_class": cls,
                             "reason": _why, "new_position": True,
                             "ranked_for": _obj_weights.audit_block(objective)})
                remaining = round(remaining - amt, 2)
            else:
                # Nothing to buy for a class the plan wants but you don't hold
                # and the screener can't fill. Record it -- the budget must not
                # just evaporate into idle cash without explanation.
                unplaced.append({"asset_class": cls, "amount_ils": round(min(budget, remaining), 2),
                                 "reason": f"you hold no {cls} and no candidate was available"})

    # Split thin, every leg can land under the minimum and the card vanishes --
    # reporting nothing to do while the cash sits idle. Seen live: NAV 22,006,
    # spendable 1,929, the equities share divided four ways at ~138 each, all
    # below the 250 minimum, so no legs and no card.
    #
    # Concentrating is the honest fallback: one trade that clears the minimum
    # beats four that do not, and it still respects the single-name cap.
    if not legs and remaining >= _fund.MIN_TRADE_ILS:
        best = None
        for cls, gap_ils, tw in gaps:
            for p in (held_by_class.get(cls) or []):
                room = _fund.name_room_ils(nav, weights.get(p.ticker, 0.0), cap)
                amt = round(min(remaining, room), 2)
                if amt >= _fund.MIN_TRADE_ILS and (best is None or amt > best["amount_ils"]):
                    best = {"ticker": p.ticker, "amount_ils": amt, "asset_class": cls,
                            "reason": (f"{cls} is {mix.get(cls, 0.0):.0%} vs a {tw:.0%} target; "
                                       f"split across your holdings every leg would be under "
                                       f"the {_fund.MIN_TRADE_ILS:,.0f} minimum, so this "
                                       f"concentrates it into one trade"),
                            "concentrated": True}
        if best:
            legs.append(best)
            remaining = round(remaining - best["amount_ils"], 2)

    # Second pass: a leg that could not be placed left its share of the surplus
    # unspent. Live, the Fixed Income leg was undeployable (plan targets 10%,
    # nothing held, no screener pick), so ~1,940 stayed in cash while the card
    # reported success -- 12% cash against a 3% floor. Push what is left into the
    # classes that CAN absorb it, still respecting the single-name cap.
    if remaining >= _fund.MIN_TRADE_ILS and legs:
        topups = [x for x in legs if not x.get("new_position")]
        for x in sorted(topups, key=lambda leg: weights.get(leg["ticker"], 0.0)):
            if remaining < _fund.MIN_TRADE_ILS:
                break
            already = x["amount_ils"]
            room = _fund.name_room_ils(nav, weights.get(x["ticker"], 0.0), cap) - already
            extra = round(min(remaining, max(0.0, room)), 2)
            if extra < _fund.MIN_TRADE_ILS:
                continue
            x["amount_ils"] = round(already + extra, 2)
            x["reason"] += " (incl. budget reallocated from an unfillable class)"
            remaining = round(remaining - extra, 2)

    if not legs:
        return []
    deployed = round(sum(x["amount_ils"] for x in legs), 2)
    kept = round(cash_ils - deployed, 2)
    floor_pct = _fund.cash_floor_pct(objective, plan)
    new_names = [x["ticker"] for x in legs if x.get("new_position")]

    return [{
        "id": _rid("redeploy", round(deployed)), "dimension": "allocation", "severity": "HIGH",
        "title": f"Redeploy {_ils(deployed)} of idle cash",
        "why": (f"You're holding {_ils(cash_ils)} ({cash_ils / nav:.0%} of the portfolio) against a "
                f"{floor_pct:.0%} floor for a {objective or 'Balanced'} plan. Cash earns nothing "
                f"toward your goal, and the sale proceeds left your mix off-plan."),
        "action": ("Put it back to work: "
                   + "; ".join(f"{_ils(x['amount_ils'])} into {x['ticker']}" for x in legs)
                   + f". Keeps {_ils(kept)} as your buffer."),
        "impact": (f"Moves every underweight asset class toward its target and cuts cash from "
                   f"{cash_ils / nav:.0%} to {kept / nav:.0%}."
                   + (f" Adds {', '.join(new_names)} to fill a gap the plan needs."
                      if new_names else "")),
        "how": ([f"Buy {_ils(x['amount_ils'])} of {x['ticker']} — {x['reason']}" for x in legs]
                + [f"⚠ {_ils(u['amount_ils'])} could not be placed — {u['reason']}"
                   for u in unplaced if u["amount_ils"] >= _fund.MIN_TRADE_ILS]
                + [f"Keep {_ils(kept)} in cash ({floor_pct:.0%} floor for {objective or 'Balanced'})",
                   "Accept executes every leg at live prices, funded entirely from cash",
                   "Tracked book only — no brokerage order is placed"]),
        "est_amount": deployed,
        "apply": {"kind": "redeploy_cash", "legs": legs},
        "meta": {"cash_before_ils": round(cash_ils, 2), "cash_after_ils": kept,
                 "floor_pct": floor_pct,
                 # What the plan asked for but the book could not absorb. Silence
                 # here is what let ~1,940 sit idle while the card read as a
                 # complete answer.
                 "unplaced": [u for u in unplaced if u["amount_ils"] >= _fund.MIN_TRADE_ILS]},
    }]


def _income_cost_recs(pdicts, snap, objective) -> list[dict]:
    """Cash drag and dividend-income opportunities (fees handled by the FeeAgent)."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav:
        return out
    # NAV is FX-normalized, so the numerator must be too -- a USD money-market
    # sleeve was otherwise counted at ~a third of its real weight.
    from app.services.fx import fx_rate as _fx, price_currency as _pc
    cash_w = 0.0
    for d in pdicts:
        cls = (d.get("asset_class") or "").lower()
        if cls == "cash" or d["ticker"].upper() in {"BIL", "SHV", "SGOV", "CASH"}:
            _r = _fx(_pc(d.get("market"), None))
            cash_w += (d["quantity"] * d["current_price"] * _r) / nav
    if cash_w >= 0.15:
        out.append({"id": _rid("cashdrag"), "dimension": "income", "severity": "MEDIUM",
                    "title": "Put idle cash to work",
                    "action": (f"About {cash_w:.0%} of your portfolio is in cash. Even a short-term bond or "
                               f"money-market ETF would earn yield on it instead of drifting."),
                    "how": ["Keep only your real emergency buffer in cash",
                            "Move the rest into a T-bill/money-market ETF (e.g. SGOV/BIL) or your target mix",
                            "Re-check after the next contribution"],
                    "est_amount": round(cash_w * nav, 2), "apply": {"kind": "none"},
                    "meta": {"cash_weight": round(cash_w, 4)}})
        out[-1]["audit_trail"] = audit_for("income",
            raw_data={"cash_weight": round(cash_w, 4), "nav": round(nav, 2)},
            formulas=[_fml("Cash weight", "cash_weight = cash_value / NAV", result=f"{cash_w:.0%}")])
    if (objective or "") == "Income":
        yields = []
        for d in pdicts:
            f = _fundamentals(d["ticker"])
            if f and f.dividend_yield_pct is not None:
                yields.append(f.dividend_yield_pct)
        if yields and (sum(yields) / len(yields)) < 2.0:
            avg = sum(yields) / len(yields)
            out.append({"id": _rid("income_yield"), "dimension": "income", "severity": "LOW",
                        "title": "Lift your portfolio yield",
                        "action": (f"Your objective is Income but your holdings average only {avg:.1f}% yield. "
                                   f"Tilting toward dividend payers would raise your cash income."),
                        "how": ["Add a dividend-focused sleeve (e.g. SCHD/VYM)",
                                "Favor profitable, low-payout-risk dividend names",
                                "Keep total-return in mind — don't chase the highest yield"],
                        "est_amount": None, "apply": {"kind": "none"},
                        "meta": {"avg_yield_pct": round(avg, 2)}})
            out[-1]["audit_trail"] = audit_for("income",
                raw_data={"avg_yield_pct": round(avg, 2)},
                formulas=[_fml("Average yield", "avg = mean(dividend_yield_pct)", result=f"{avg:.1f}%")])
    return out


def _benchmark_recs(perf, objective) -> list[dict]:
    """Turn the performance-vs-benchmark read into an improvement action when the
    portfolio is trailing its benchmark by a meaningful margin (grounded in the
    real excess-return number, never invented)."""
    out: list[dict] = []
    if not isinstance(perf, dict) or not perf.get("ok"):
        return out
    ex = perf.get("excess_return_pct")
    if ex is None or ex > -3.0:
        return out
    bench = perf.get("benchmark", "the benchmark")
    bret, pret = perf.get("benchmark_return_pct"), perf.get("total_return_pct")
    sev = "HIGH" if ex <= -10.0 else "MEDIUM"
    detail = f" ({pret:.1f}% vs {bench} {bret:.1f}%)" if (bret is not None and pret is not None) else ""
    out.append({"id": _rid("benchmark_lag", bench), "dimension": "performance", "severity": sev,
                "title": f"You're trailing {bench}",
                "action": (f"Over this window your portfolio is about {abs(ex):.1f}% behind {bench}{detail}. "
                           f"Worth checking what's dragging before the gap compounds."),
                "why": (f"Sustained underperformance vs {bench} usually traces to one of three things: a few big "
                        f"laggards, paying too much in fund fees, or a mix that has drifted off target."),
                "impact": f"Closing a {abs(ex):.1f}% annual gap compounds into a large sum over your horizon.",
                "how": ["Check your biggest laggards in Holdings — trim or re-confirm the thesis",
                        "Review fund fees — cheaper index exposure tracks the benchmark more closely",
                        "Rebalance toward your target mix if you have drifted"],
                "est_amount": None, "apply": {"kind": "none"},
                "meta": {"excess_pct": round(ex, 1), "benchmark": bench}})
    out[-1]["audit_trail"] = audit_for("performance",
        raw_data={"excess_pct": round(ex, 1), "benchmark": bench,
                  "portfolio_return_pct": pret, "benchmark_return_pct": bret},
        formulas=[_fml("Excess return", "excess = portfolio_return - benchmark_return", result=f"{ex:.1f}%")])
    return out


#: At most this many screener names become cards on one render. Today is a list
#: of things to do, not a leaderboard: eleven ranked ideas would bury the risk
#: and drift cards under a wall of optional purchases.
_MAX_BUY_IDEA_CARDS = 2


def _buy_idea_recs(ideas, rows, snap, plan, objective, cap=0.25, cash_ils=0.0,
                   sleeve_names=None, ledger=None, already_named=None,
                   book_target: dict | None = None) -> list[dict]:
    """Turn the screener's ranked names into fundable cards on Today.

    THE SCREENER HAD NO SURFACE. `buy_ideas` has been in the /recommendations
    payload for months and **nothing in the app reads it** -- `grep buy_ideas
    app/static_app/ frontend/` returns nothing, and `/api/v1/screener` is never
    called by the UI either. Its only consumer was one `next()` at
    `_redeploy_cash_recs`, which lifts the single top-ranked name in a class the
    book holds none of. So PR 24, 25 and 27 fixed a ranking that reached the user
    through exactly one ticker, on one card, in one situation.

    WHY CARDS AND NOT A SECTION. Eran's call, and it is the right one. A separate
    "buy ideas" list would be a SECOND SURFACE PROPOSING TRADES, which is the
    thing `contradictions.py` and the card-claim invariant exist to police --
    and the only one of them with no `apply` spec to check its claim against.
    STATUS records three cards arming three trailing stops on one ticker because
    three producers could not see each other; an unpoliced list would be a fourth
    producer and the least verifiable one.

    So these are ordinary cards, and they inherit every guard that implies:

    * `propose_funded_buy` decides whether a card exists at all. It returns None
      when the plan does not want more of that class, when the purchase cannot
      be funded, or when it would push a class further from target. A ranked
      name is a candidate, never an instruction.
    * The shared `FundingLedger` means these cards cannot spend the same shares
      as the rebalance or commodity cards -- the defect PR 1 fixed.
    * `already_named` keeps them off tickers another card is already proposing.
    * The claim is simulated, not asserted: `buy.impact` and `buy.summary` come
      from the post-trade mix.

    WHAT THE CARD ADDS OVER THE OLD SILENT PICK is the reason. The score, the
    plain-English reasons the engine derived, and -- the payoff for PR 27 --
    `data_gaps` when the composite was built from fewer than eight factors. A
    name ranked without its leverage measured says so on the card, because a
    reader comparing two scores is entitled to know one of them rests on less.
    """
    out: list[dict] = []
    sleeve_names = set(sleeve_names or set())
    already_named = {str(t).upper() for t in (already_named or set())}
    if not ideas:
        return out
    try:
        from app.services import funding_service as _fund
        from app.services.allocation_mix import classify
    except Exception:  # noqa: BLE001
        return out

    held = {str(getattr(r, "ticker", "") or "").upper() for r in (rows or [])}

    for idea in ideas:
        if len(out) >= _MAX_BUY_IDEA_CARDS:
            break
        tk = str(idea.get("ticker") or "").upper()
        if not tk or tk in already_named or tk in sleeve_names:
            continue
        # ALREADY HELD IS NOT A REASON TO SAY NOTHING, but it is a reason to let
        # the rebalance and drift cards own that ticker. Topping up a position
        # the book already has is a sizing decision those cards are built to
        # make; this card exists to surface a name the book does NOT hold.
        if tk in held:
            continue

        cls = idea.get("sector_class") or classify(tk, None, idea.get("asset_class"))
        if not cls:
            continue
        buy = _fund.propose_funded_buy(
            rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
            ticker=tk, buying_class=cls, cash_ils=cash_ils,
            exclude=sleeve_names, market=str(idea.get("market") or "NYSE"),
            ledger=ledger, book_target=book_target)
        if buy is None:
            # Not a failure and not worth logging as one. The plan does not want
            # this purchase right now, and the honest response is silence.
            continue

        name = str(idea.get("name") or "").strip()
        label = f"{tk} ({name})" if name and name != tk else tk
        reasons = [str(r) for r in (idea.get("reasons") or [])][:3]
        gaps = [str(g) for g in (idea.get("data_gaps") or [])]
        score = idea.get("score")

        why = f"It ranks {('%.0f' % float(score)) if score is not None else 'well'}/100 on the "
        why += f"{objective or 'Balanced'} screen of the candidate universe"
        why += (" — " + "; ".join(reasons).lower() + ".") if reasons else "."
        if gaps:
            # THE HONESTY CLAUSE, and it is the whole reason PR 27 recorded gaps
            # as data rather than dropping them. Without this sentence a score
            # built from five factors reads exactly like one built from eight.
            why += (f" Ranked WITHOUT {len(gaps)} of its eight factors "
                    f"({', '.join(g.split(':')[0] for g in gaps)}), which the "
                    f"provider did not report — so this score rests on less than "
                    f"the others.")

        how = ([f"Buy {_ils(buy.amount_ils)} of {label}"]
               + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                  for x in (buy.fund or {}).get("sells", [])]
               + ([f"Use {_ils((buy.fund or {}).get('from_cash_ils'))} of cash"]
                  if (buy.fund or {}).get("from_cash_ils") else []))

        out.append({
            "id": _rid("buy_idea", tk, round(buy.amount_ils / 250) * 250),
            "dimension": "opportunity",
            # LOW on purpose. Nothing is wrong with the book if this is ignored;
            # a ranked name is an option, and severity is how Today sorts risk.
            "severity": "LOW",
            "title": f"Consider {label} — {_ils(buy.amount_ils)}",
            "action": f"Buy {_ils(buy.amount_ils)} of {label} — {buy.impact} {buy.summary}",
            "why": why,
            "impact": buy.impact,
            "how": how,
            "est_amount": buy.amount_ils,
            "apply": buy.apply_spec,
            "meta": {"ticker": tk, "score": score, "asset_class": cls,
                     "data_gaps": gaps, "flags": idea.get("flags") or [],
                     "metrics": idea.get("metrics") or {},
                     # The reproducibility pin rides with the card, so a
                     # recommendation read next month traces to the weights and
                     # the COVERAGE that produced it rather than to whatever the
                     # table says by then.
                     "ranked_for": idea.get("ranked_for")},
        })
        already_named.add(tk)

    return out


def _commodity_recs(rows, snap, objective, plan=None, cap=0.25, cash_ils=0.0,
                    sleeve_names=None, ledger=None, book_mix: dict | None = None) -> list[dict]:
    """Recommend a specific commodity, sized to the plan gap and funded explicitly."""
    out: list[dict] = []
    sleeve_names = sleeve_names or set()
    try:
        from app.services import funding_service as _fund
    except Exception:  # noqa: BLE001
        _fund = None
    # THE 10%-VS-25% CONTRADICTION. This read the objective's commodity target
    # while the rebalance card, three cards above it in the same response, read
    # the core's. Live on 2026-08-25: "Buy Commodities ~5,293" (25%) sitting
    # beside "Add a commodities sleeve - 2,120 of USO ... against your 10%
    # target". Both cards were internally consistent and the pair was nonsense.
    target = (book_mix or OBJ_TARGET.get(objective or "Balanced", {}) or {}).get("Commodities", 0.0)
    if target <= 0:
        return out
    mix, nav = current_mix(rows)
    if not nav:
        return out
    com_w = mix.get("Commodities", 0.0)
    if com_w >= max(0.03, target * 0.5):
        return out  # already holds a meaningful sleeve
    picks = []
    try:
        from app.agents.screener_agent import OpportunityAgent
        picks = OpportunityAgent().screen_commodities(top_n=3)
    except Exception:  # noqa: BLE001
        picks = []
    # NO SCREEN, NO CARD. This fell back to a hardcoded "DBC" whenever the
    # screener returned nothing -- so a card headed "Add a commodities sleeve"
    # could name a ticker that had been chosen by no process at all, while
    # reading exactly like the screened recommendation it usually is. Now that
    # candidates without price history are dropped rather than scored, an empty
    # result is the normal signal that nothing could be evaluated, and the
    # honest response to that is to say nothing.
    if not picks:
        logger.info("no rankable commodity candidates; abstaining from the sleeve card")
        return out
    names = [(f"{p.ticker} ({p.name})" if getattr(p, "name", "") else p.ticker) for p in picks][:3]
    pick = picks[0]
    pick_tk = pick.ticker
    pick_name = getattr(pick, "name", "") or "a broad commodity basket"
    alts = ", ".join(names[1:3]) if len(names) > 1 else "GLD (gold) or DBA (agriculture)"
    gap_ils = round(max(0.0, target - com_w) * nav, 2)
    action = (f"Buy {_ils(gap_ils)} of {pick_tk} ({pick_name}) — that lifts commodities from "
              f"{com_w:.0%} to your {target:.0%} target. Alternatives if you'd rather: {alts}.")
    how = [f"Buy {_ils(gap_ils)} of {pick_tk} ({pick_name})"]
    apply_spec = {"kind": "none"}
    if _fund is not None and gap_ils >= _fund.MIN_TRADE_ILS:
        # The control case for the honesty clause: this buys Commodities out of
        # (usually) Equities, so BOTH weights genuinely move and the disclaimer
        # must stay silent. If it ever fires here, the clause is over-firing.
        buy = _fund.propose_funded_buy(
            rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
            ticker=pick_tk, buying_class="Commodities", cash_ils=cash_ils,
            requested_ils=gap_ils, exclude=sleeve_names, market="NYSE", ledger=ledger,
            book_target=book_mix)
        if buy is not None:
            fund = buy.fund
            affordable = buy.amount_ils
            action = (f"Buy {_ils(affordable)} of {pick_tk} ({pick_name}) — "
                      + buy.impact + " " + buy.summary
                      + f" Alternatives: {alts}.")
            how = ([f"Buy {_ils(affordable)} of {pick_tk} ({pick_name})"]
                   + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                      for x in fund.get("sells", [])]
                   + ([f"Use {_ils(fund['from_cash_ils'])} of cash"] if fund.get("from_cash_ils") else []))
            apply_spec = buy.apply_spec
            gap_ils = affordable
    out.append({"id": _rid("commodity_add", pick_tk, round(gap_ils / 250) * 250),
                "dimension": "diversification", "severity": "LOW",
                "title": f"Add a commodities sleeve — {_ils(gap_ils)} of {pick_tk}",
                "action": action,
                "why": (f"Commodities tend to move differently from equities and bonds, so a small "
                        f"allocation cushions the portfolio when stocks and bonds fall together — "
                        f"your {objective or 'Balanced'} target holds ~{target:.0%} and you hold {com_w:.0%}."),
                "impact": "Adds a low-correlation diversifier, smoothing your overall swings.",
                "how": how,
                "est_amount": gap_ils, "apply": apply_spec,
                "meta": {"commodity_weight": round(com_w, 4), "target": target,
                         "picks": [p.ticker for p in picks], "chosen": pick_tk}})
    out[-1]["audit_trail"] = audit_for("diversification",
        raw_data={"commodity_weight": round(com_w, 4), "target": target, "picks": [p.ticker for p in picks]},
        formulas=[_fml("Commodity gap", "gap = target - current", result=f"{(target - com_w):.0%}")])
    return out


def _buy_ideas(snap, currency: str = "USD", objective: str | None = None,
               pin_out: dict | None = None) -> list[dict]:
    """Top fundamentals-ranked equity buy ideas plus screener-ranked commodity
    picks from the Opportunity Agent (informational).

    `objective` TILTS THE EQUITY RANKING, and until this parameter existed it
    could not. `screen_equities` was called with no weights, so ScreenerEngine
    used DEFAULT_WEIGHTS for every book and Grow and Preserve ranked the
    identical candidates in the identical order.

    MEASURED on the same universe, top 5 (builtin provider, sandbox):

        today / Balanced   CRM  V    LLY  AAPL PFE
        Grow               CRM  LLY  V    AVGO BAC   <- AVGO and BAC are names
        Income             V    AAPL PFE  CRM  LLY      today never surfaces
        Preserve           V    CRM  AAPL PFE  LLY

    Balanced is identical to the old behaviour on purpose -- it is the one
    objective DEFAULT_WEIGHTS fits, and it is the control for the change.

    The commodity and bond legs are deliberately NOT tilted. They rank on
    duration fit, cost and measured trend; there is no factor model there to
    tilt, and inventing one would be the thing this change exists to stop.
    """
    try:
        from app.agents.screener_agent import OpportunityAgent
        from app.services import objective_weights as _ow
        ag = OpportunityAgent()
        _w = _ow.weights_for(objective)   # None for Balanced/unknown -> default

        _picks = ag.screen_equities(weights=_w, top_n=5)
        # HOW MUCH OF THE UNIVERSE THIS RANKING ACTUALLY SAW, read straight after
        # the equity scan and BEFORE the commodity and bond legs below. A
        # percentile is only meaningful against the set it was computed over, so
        # "top 5" over 20 of 58 candidates is a different claim from "top 5" over
        # 58 -- and until the screener started counting, the two were
        # indistinguishable from out here.
        _pin = {**_ow.audit_block(objective),
                "coverage": dict(getattr(ag, "last_coverage", {}) or {})}
        # Published to the caller BEFORE anything can shorten the list, so a scan
        # that ranked zero names still reports what it tried. This assignment is
        # the whole fix for "the coverage pin rides on equity ideas, so it
        # vanishes when there are none".
        if pin_out is not None:
            pin_out["ranked_for"] = _pin
        out = [{"ticker": p.ticker, "name": p.name, "score": p.score, "kind": p.kind,
                "sector": p.sector, "reasons": p.reasons, "flags": p.flags,
                "metrics": p.metrics, "data_gaps": p.data_gaps, "ranked_for": _pin}
               for p in _picks]
        try:
            out += [{"ticker": p.ticker, "name": p.name, "score": p.score, "kind": p.kind,
                     "sector": "Commodities", "reasons": p.reasons, "flags": p.flags,
                     "metrics": p.metrics} for p in ag.screen_commodities(top_n=3)]
        except Exception:  # noqa: BLE001
            pass
        try:
            # WITHOUT THIS LINE THE FIXED INCOME LEG CANNOT BE PLACED. This
            # function is what `_redeploy_cash_recs` searches for a candidate in
            # a class the book holds none of; it returned equities and
            # commodities only, so `classify(...) == "Fixed Income"` never
            # matched and the money went to `unplaced` with the note "you hold
            # no Fixed Income and no candidate was available" -- on every scan,
            # for every book, since the app was written.
            out += [{"ticker": p.ticker, "name": p.name, "score": p.score, "kind": p.kind,
                     "sector": "Fixed Income", "reasons": p.reasons, "flags": p.flags,
                     "metrics": p.metrics} for p in ag.screen_bonds(top_n=3, currency=currency)]
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception:
        return []


async def _rebalance_to(session, user, rows, objective: str) -> None:
    """Scale holdings so each asset class hits the BOOK's target weight (NAV held constant).

    Reads the same `_book_target` the card does, and that is not a tidiness
    point: this is the function Accept runs. When it read the CORE's mix while
    the card quoted the core's mix too, both were wrong together and at least
    agreed. Fixing only the card would have been worse than fixing neither --
    the screen would have said "sell 4,324" and the button would have sold
    12,139.

    **Every value here is ILS.** It used to sum ``quantity * current_price`` raw and
    call that NAV -- adding USD-priced holdings to an ILS cash row as if they were
    the same unit. The class factors were then computed against that mixed number
    and written straight back into share counts, so an Accept on a rebalance card
    minted shares out of the arithmetic: +4.8% of NAV per tap on a USD book, no
    trade, no cash movement. The docstring said "NAV held constant"; only the
    docstring did. It is now asserted below rather than claimed here.
    """
    from collections import defaultdict

    from app.services.fx import book_nav_ils, fx_rate, position_value_ils, price_currency
    from app.services.intake_service import is_cash_position

    target = (await _book_target(session, user, objective))["target"]
    _ils = position_value_ils

    # CASH IS NOT A CLASS THIS FUNCTION MAY REBALANCE.
    #
    # `_book_target` returns investable weights only -- Grow is
    # {Equities .80, Fixed Income .10, Commodities .10} and there is no `Cash`
    # key on any objective. The cash row classifies as "Cash", so
    # `target.get("Cash", 0.0)` was 0.0, `desired` was 0.0, and accepting the
    # card set the balance to zero and spread it across the equity legs.
    #
    # Measured on the seeded USD book: 621.79 ILS of cash -> 0.00, silently.
    # NAV was conserved, so the guard below passed and so did every test. What
    # it actually did was BUY 621.79 ILS of equities -- with no trade legs, no
    # ledger entry, no tax, and a card that says only "Sell about X of Equities
    # to move toward 80%" and never mentions cash. That is the card-claim
    # invariant: a card may not produce a change it does not claim. It also
    # drove the balance straight through the app's own cash floor
    # (`funding_service.cash_floor_ils` -> 419.69 ILS on that book), a policy
    # this function never consulted.
    #
    # A rebalance is a reshuffle of INVESTED value between investable classes.
    # Turning cash into securities is a purchase and belongs to the funded-buy
    # path, which writes legs and says what it bought. So cash is excluded from
    # both the classes being scaled and the NAV they are scaled against.
    investable = [p for p in rows
                  if not is_cash_position(p.ticker, p.meta if isinstance(p.meta, dict) else None)]
    nav = book_nav_ils(investable)
    if not nav:
        return
    byc = defaultdict(list)
    for p in investable:
        byc[classify_holding(p)].append(p)
    present_weight = sum(w for c, w in target.items() if c in byc) or 1.0
    for c, rowsc in byc.items():
        w = target.get(c, 0.0)
        desired = (w / present_weight) * nav  # classes absent from target -> 0 (sold down)
        cur = sum(_ils(p) for p in rowsc)
        for p in rowsc:
            price = float(p.current_price or 0)
            if price <= 0:
                continue
            rate = fx_rate(price_currency(p.market, p.meta if isinstance(p.meta, dict) else {}))
            share = (_ils(p) / cur) if cur > 0 else 1.0 / len(rowsc)
            # desired * share is ILS; convert back to shares through the same rate.
            # NOT rounded here: `update_position` quantises to the MONEY column's
            # own scale. Rounding to six places first was worse than useless --
            # the column keeps four, so the extra two digits lived in the
            # identity-mapped object, where the check below could see them, and
            # nowhere else. The check measured a book the database never had.
            await update_position(session, user, str(p.id),
                                  quantity=desired * share / (price * rate))

    # A reshuffle is not a source of money. Quantising each leg to the stored
    # scale is the only licensed drift; anything larger means the arithmetic
    # moved value and the book must not be left holding it.
    #
    # This is an assertion rather than a restatement only because every writer
    # of a MONEY column now quantises to the column's own scale before the
    # assignment (`app.models.tables.money`). The identity-mapped object and the
    # stored row therefore hold the same number, so re-reading through the
    # session measures the book the database has -- which, before that change,
    # it did not: the extra two decimal places lived only in memory.
    await session.flush()
    fresh = await list_positions(session, user)
    after = book_nav_ils([p for p in fresh
                          if not is_cash_position(p.ticker,
                                                  p.meta if isinstance(p.meta, dict) else None)])
    if abs(after - nav) > max(1.0, nav * 1e-6):
        raise ValueError(
            f"rebalance would have moved NAV by {after - nav:+.2f} ILS with no trade "
            f"({nav:.2f} -> {after:.2f}); refusing to write it")


def _sale_value_ils(qty: float, price: float, basis: float, market, meta) -> tuple[float, float]:
    """Value a full sale of `qty` shares in ILS: (gross_ils, estimated_tax_ils).

    Tax is Israeli CGT on the realized *gain* only (losses owe nothing — they are
    what a harvest realizes), so proceeds land net of tax as spendable liquidity.
    """
    from app.core.config import get_settings
    from app.services.fx import fx_rate, price_currency
    rate = fx_rate(price_currency(market, meta))
    gross = qty * price * rate
    gain = (price - basis) * qty * rate
    tax = max(0.0, gain) * float(get_settings().cgt_rate)
    return gross, tax


# One shekel. The same figure `test_accept_conserves_nav` asserts with, stated
# once here so the app and the test cannot drift apart on what "conserved" means.
_NAV_TOLERANCE_ILS = 1.0


def _book_snapshot(rows) -> dict:
    """ticker -> (shares, price, value_ils). Enough to diff two books by eye."""
    from app.services.fx import position_value_ils
    return {p.ticker: (float(p.quantity), float(p.current_price or 0.0),
                       round(position_value_ils(p), 2)) for p in rows}


async def _conservation_report(session, user, kind: str, rec_id: str,
                               nav_before: float, book_before: dict,
                               detail: dict) -> dict:
    """Measure what the accept actually did to NAV, and say so on every accept.

    The invariant, from `test_accept_conserves_nav`: after an accept, NAV equals
    NAV before minus the tax the card itself reported. Everything else is a swap.

    It was enforced inside exactly ONE of the twelve actionable handlers
    (`_rebalance_to`). The other eleven commit money changes with nothing
    checking them, so the only thing standing behind the invariant in production
    was a test sweeping whatever cards the engine happened to emit that run --
    and which cards it emits varies with process-global provider state. A card
    shape that never appears in a test run is a handler nothing has ever checked.

    This does not yet REFUSE a breach, and the reason is worth stating rather
    than leaving to be discovered: several handlers commit part-way through
    (`update_position` commits; the funding paths commit), so by the time the
    drift is measurable there is nothing left to roll back. Making the accept
    atomic is a change to the commit boundaries and is deliberately not smuggled
    in here. What this does do is make a breach ATTRIBUTABLE at the moment it
    happens -- kind, card, drift, and the positions that moved -- instead of
    surfacing days later as an unexplained NAV step, which is exactly how the
    +739.21 ILS sighting arrived with nothing to diagnose it from.
    """
    from app.services.fx import book_nav_ils
    try:
        await session.flush()
        # Trustworthy only because every MONEY writer quantises to the column's
        # scale first (`app.models.tables.money`); otherwise this would re-read
        # the handler's own arithmetic out of the identity map rather than the
        # row the database will actually return on the next request.
        rows_after = await list_positions(session, user)
        nav_after = book_nav_ils(rows_after)
    except Exception as exc:  # noqa: BLE001 -- measurement must never cost a trade
        logger.warning("conservation: could not measure %s (%s): %s", kind, rec_id, exc)
        return {}

    tax = float(detail.get("tax_ils") or 0.0)
    drift = nav_after - nav_before + tax
    if abs(drift) > max(_NAV_TOLERANCE_ILS, abs(nav_before) * 1e-6):
        book_after = _book_snapshot(rows_after)
        moved = {t: (book_before.get(t), book_after.get(t))
                 for t in sorted(set(book_before) | set(book_after))
                 if book_before.get(t) != book_after.get(t)}
        logger.error(
            "NAV CONSERVATION BREACH: %s card %s moved NAV by %+.2f ILS "
            "(%.2f -> %.2f, card reported tax %.2f). Positions that changed "
            "(ticker: before -> after, as shares/price/value_ils): %s",
            kind, rec_id, drift, nav_before, nav_after, tax, moved)
    return {"nav_before_ils": round(nav_before, 2),
            "nav_after_ils": round(nav_after, 2),
            "nav_drift_ils": round(drift, 2)}


async def apply_recommendation(session: AsyncSession, user: User, rec_id: str) -> dict | None:
    """Accept = apply the recommendation to holdings/plan immediately.

    Sales don't just delete holdings — the net-of-tax proceeds are credited to a
    visible CASH position, so accepting a 'sell' shows up as liquidity you can see
    and redeploy (rather than value silently vanishing).
    """
    built = await build_recommendations(session, user)
    rec = next((r for r in built.get("recommendations", []) if r["id"] == rec_id), None)
    if rec is None:
        return None
    spec = rec.get("apply") or {"kind": "none"}
    kind = spec.get("kind")
    rows = await list_positions(session, user)
    by_ticker = {p.ticker: p for p in rows}
    detail: dict = {}
    # The book as it stands the instant before the handler touches it, plus a
    # per-position snapshot so a breach can name WHICH holding moved rather than
    # only by how much. Both are cheap; the alternative is a bare number in a
    # test failure and a session spent guessing, which is what happened.
    from app.services.fx import book_nav_ils
    nav_before = book_nav_ils(rows)
    book_before = _book_snapshot(rows)

    if kind == "trim":
        p = by_ticker.get(spec["ticker"])
        if p:
            shares = min(float(spec["shares"]), float(p.quantity))
            gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                         float(p.cost_basis or 0), p.market, p.meta)
            await update_position(session, user, str(p.id),
                                  quantity=max(0.0, float(p.quantity) - shares))
            credited = await credit_cash(session, user, gross - tax)
            detail = {"sold": [spec["ticker"]], "cash_added_ils": round(credited, 2),
                      "tax_ils": round(tax, 2)}
    elif kind == "sell_position":
        # A stop-loss / trailing stop / take-profit is a full exit by definition.
        # The position leaves the book and the net-of-CGT proceeds land in cash,
        # so the money is visible and redeployable rather than vanishing.
        p = by_ticker.get(spec["ticker"])
        if p:
            shares = min(float(spec.get("shares") or 0) or float(p.quantity), float(p.quantity))
            gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                         float(p.cost_basis or 0), p.market, p.meta)
            remaining = max(0.0, float(p.quantity) - shares)
            if remaining <= 0:
                await delete_position(session, user, p.ticker, p.market)
            else:
                await update_position(session, user, str(p.id), quantity=remaining)
            credited = await credit_cash(session, user, gross - tax)
            detail = {"sold": [spec["ticker"]], "shares": round(shares, 6),
                      "cash_added_ils": round(credited, 2), "tax_ils": round(tax, 2),
                      "broker_note": "Tracked book updated — no brokerage order was placed."}
        else:
            detail = {"sold": [], "note": f"{spec['ticker']} is no longer held."}
    elif kind == "sell_losers":
        sold, proceeds, tax_total = [], 0.0, 0.0
        for tk in spec.get("tickers", []):
            p = by_ticker.get(tk)
            if p:
                gross, tax = _sale_value_ils(float(p.quantity), float(p.current_price or 0),
                                             float(p.cost_basis or 0), p.market, p.meta)
                await delete_position(session, user, tk, p.market)
                proceeds += (gross - tax)
                tax_total += tax
                sold.append(tk)
        credited = await credit_cash(session, user, proceeds)
        detail = {"sold": sold, "cash_added_ils": round(credited, 2), "tax_ils": round(tax_total, 2)}
    elif kind == "fee_swap":
        detail = await _apply_fee_swap(session, user, by_ticker, spec)
    elif kind == "buy_funded":
        detail = await _apply_buy_funded(session, user, spec)
    elif kind == "core_to_basket":
        detail = await _apply_core_to_basket(session, user, spec)
    elif kind == "redeploy_cash":
        detail = await _apply_redeploy_cash(session, user, spec)
    elif kind == "fund_sleeve":
        # Reuses the P0.1 funding path, so there is ONE implementation of "raise
        # this much and buy the sleeve" rather than a second that could size it
        # differently from the button on the Plan tab.
        from app.services.strategy_service import load_basket
        detail = await load_basket(session, user, spec["strategy_id"],
                                   sleeve_pct=spec.get("sleeve_pct"), mode="fund")
    elif kind == "fund_plan":
        # A ticker two sleeves both want. Only the whole-plan path can honour a
        # SUMMED target -- funding one of the two sleeves would buy that sleeve's
        # share while the card described the total, so the card would still be
        # right and the action would still be wrong.
        from app.services.strategy_service import fund_plan
        detail = await fund_plan(session, user, dry_run=False)
    elif kind == "rebalance_to_objective":
        plan = await get_plan(session, user)
        await _rebalance_to(session, user, rows, plan.objective if plan else "Balanced")
    elif kind == "set_objective_and_rebalance":
        await upsert_plan(session, user, objective=spec["objective"])
        await session.commit()
        rows = await list_positions(session, user)
        await _rebalance_to(session, user, rows, spec["objective"])
    elif kind == "set_plan":
        await upsert_plan(session, user, **spec.get("fields", {}))
        await session.commit()
    elif kind == "relevel_sleeve_caps":
        # RE-LEVEL, NEVER SILENTLY. `_arm_sleeve_caps` already does exactly the
        # right thing -- one cap per ticker at the summed sleeve target, hand-set
        # caps on sleeve tickers ADOPTED, caps on other tickers untouched. It had
        # simply never run against caps armed AFTER the last apply, which is how
        # a 15% TQQQ cap outlived a resize to 38.4%.
        #
        # Behind a tap rather than on the Today read, because /recommendations is
        # a GET and a GET that quietly writes makes "does this book have a stale
        # cap?" depend on whether anyone opened the page. The card names every
        # correction before you take it.
        from app.services.strategy_service import _arm_sleeve_caps
        detail = {"caps": await _arm_sleeve_caps(session, user)}
    elif kind in ("create_rule", "create_rules"):
        specs = spec.get("rules") if kind == "create_rules" else [spec]
        created = []
        for rspec in (specs or []):
            made = await _create_rule_from_spec(session, user, rspec)
            if made:
                created.append(made)
        detail = {"rules_created": created}
    # A rule-driven card closes the loop on its own audit trail: the firing that
    # produced this card is stamped with what was actually done.
    rule_id = spec.get("rule_id")
    if rule_id:
        from app.services.rules_service import resolve_rule
        # Stamps the audit event AND clears/consumes the rule, so the "N trading
        # rules triggered" banner reflects what's still outstanding rather than
        # everything that has ever fired.
        await resolve_rule(
            session, user, rule_id,
            "executed" if kind in _ACTIONABLE_KINDS else "acknowledged",
            {"kind": kind, **detail} if detail else {"kind": kind})

    # kind == "none" -> acknowledged only; say so rather than claiming an edit
    if kind not in _ACTIONABLE_KINDS:
        return {"applied": "none", "advisory": True, "title": rec["title"],
                "note": "Marked as done. This one is guidance -- nothing was bought or sold."}

    # ONE ledger call, at the boundary, for every actionable kind. Deliberately
    # here rather than inside each branch: `Transaction` went unwritten for the
    # life of the app precisely because "each handler records its own" is a rule
    # that a new handler silently opts out of. Anything that reaches this line
    # changed the book, so anything that reaches this line is recorded.
    await _record_apply(session, user, kind, rec_id, detail)
    conservation = await _conservation_report(
        session, user, kind, rec_id, nav_before, book_before, detail)
    return {"applied": kind, "title": rec["title"], **detail, **conservation}


async def _record_apply(session, user, kind: str, rec_id: str, detail: dict) -> None:
    """Translate whatever the handler returned into ledger legs.

    The apply handlers grew independently and describe their work in three
    different shapes -- a list of ticker strings, a list of dicts, a single
    bought ticker with its share count. Normalising here keeps that history out
    of the ledger, and keeps a handler from having to know the ledger exists.
    """
    from app.services import ledger_service as ledger

    src = kind[:40]
    note = f"card {rec_id}"[:255]
    try:
        sold_raw = detail.get("sold") or []
        sells: list[dict] = []
        for s in sold_raw:
            if isinstance(s, dict):
                sells.append(s)
            elif isinstance(s, str):
                # "trim"/"sell_losers" report ticker strings, sometimes as
                # "4 V" from the funded-buy path.
                parts = s.split()
                if len(parts) == 2 and parts[0].replace(".", "", 1).isdigit():
                    sells.append({"ticker": parts[1], "shares": float(parts[0])})
                else:
                    sells.append({"ticker": s, "shares": detail.get("shares") or 0})
        if sells:
            # A single reported tax figure covers the whole card; attribute it to
            # the first leg rather than repeating it on each, so the ledger's tax
            # column still sums to what the card actually charged.
            tax = float(detail.get("tax_ils") or 0.0)
            for i, s in enumerate(sells):
                s.setdefault("tax_ils", tax if i == 0 else 0.0)
        buys: list[dict] = []
        if isinstance(detail.get("bought"), list):
            buys = [b for b in detail["bought"] if isinstance(b, dict)]
        elif isinstance(detail.get("bought"), str):
            buys = [{"ticker": detail["bought"], "shares": detail.get("shares") or 0,
                     "amount_ils": detail.get("value_ils") or 0}]
        await ledger.record_legs(session, user, sold=sells, bought=buys,
                                 source=src, note=note)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not cost a trade
        logger.warning("ledger: could not record %s (%s): %s", kind, rec_id, exc)



async def _apply_redeploy_cash(session: AsyncSession, user: User, spec: dict) -> dict:
    """Buy every leg straight out of cash, at live prices.

    Cash is debited leg by leg as we go, so a price move between building the
    card and accepting it can only shorten the list -- it can never spend money
    that isn't there.
    """
    from app.providers.registry import guarded_quote
    from app.services.fx import fx_rate
    from app.services.intake_service import buy_into_book, get_cash, set_cash

    cash = await get_cash(session, user)

    bought, skipped = [], []
    for leg in (spec.get("legs") or []):
        tk, amount = leg.get("ticker"), float(leg.get("amount_ils") or 0)
        if not tk or amount <= 0:
            continue
        if amount > cash:
            skipped.append({"ticker": tk, "reason": "not enough cash left"})
            continue
        try:
            q = guarded_quote(tk)
        except Exception:  # noqa: BLE001
            q = None
        if q is None or not q.price:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        price_ils = float(q.price) * fx_rate(getattr(q, "currency", None) or "USD")
        if price_ils <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        shares = round(amount / price_ils, 4)
        if shares <= 0:
            skipped.append({"ticker": tk, "reason": "amount too small for one share"})
            continue
        # One shared implementation: blends into a held position, creates one
        # otherwise. The `else` branch here used to call `upsert_positions` with
        # six arguments against its three-argument signature -- a TypeError that
        # only fires on a leg for a ticker you do not already own.
        is_new = await buy_into_book(
            session, user, ticker=tk, market=getattr(q, "market", None) or "NASDAQ",
            price=float(q.price), shares=shares, depth=3)
        cash = round(cash - amount, 2)
        bought.append({"ticker": tk, "shares": shares, "amount_ils": round(amount, 2),
                       "price": float(q.price), "new_position": is_new})
    await set_cash(session, user, cash)
    await session.commit()
    return {"bought": bought, "skipped": skipped, "cash_remaining_ils": round(cash, 2),
            "broker_note": "Tracked book updated — no brokerage order was placed."}


async def _apply_buy_funded(session: AsyncSession, user: User, spec: dict) -> dict:
    """Execute a sized buy, funding it from cash and/or the named trims.

    PRICES BEFORE IT SELLS. The buy leg is quoted first and the whole action
    refuses, untouched, if it cannot be.

    This used to run the other way round: every sell executed, and only then was
    a price asked for. When the quote failed the proceeds were credited to cash
    with a note saying so -- but the positions were already gone, the capital
    gains tax was already realized, and the card had claimed a purchase that
    never happened. `_apply_core_to_basket` was written with this order from the
    start and described the older behaviour as "survivable for a single swap".
    Survivable is not correct, and the rule that a card may not claim a portfolio
    change it does not produce has no size threshold.

    Ordering IS the fix: `delete_position` commits, so once the first sell lands
    there is nothing left to roll back. Before it is the only place this can
    refuse safely.

    Nothing is bought that can't be funded, and nothing is sold to fund a buy
    that cannot happen.
    """
    from app.providers.registry import guarded_quote
    from app.schemas.state_machine import Market
    from app.services.fx import fx_rate
    from app.services.intake_service import buy_into_book, get_cash, set_cash

    ticker = (spec.get("ticker") or "").upper()
    try:
        q = guarded_quote(ticker)
        price, ccy = float(q.price), (getattr(q, "currency", None) or "USD")
    except Exception:  # noqa: BLE001 - no live price: refuse before anything moves
        price, ccy = 0.0, "USD"
    if price <= 0:
        return {"bought": None, "aborted": True, "unpriced": ticker,
                "note": (f"Could not get a live price for {ticker}, so nothing was "
                         f"sold or bought. Your book is unchanged.")}

    sold, tax_total, raised = [], 0.0, 0.0
    rows = await list_positions(session, user)
    by_ticker = {(p.ticker or "").upper(): p for p in rows}
    for leg in spec.get("sells") or []:
        p = by_ticker.get((leg.get("ticker") or "").upper())
        if p is None:
            continue
        shares = min(float(leg.get("shares") or 0), float(p.quantity))
        if shares <= 0:
            continue
        gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                     float(p.cost_basis or 0), p.market, p.meta)
        remaining = float(p.quantity) - shares
        if remaining <= 0:
            await delete_position(session, user, p.ticker, p.market)
        else:
            await update_position(session, user, str(p.id), quantity=remaining)
        raised += max(0.0, gross - tax)
        tax_total += tax
        sold.append(f"{int(shares)} {p.ticker}")

    cash_before = await get_cash(session, user)
    # Spend only cash that exists. The card was built against an earlier book, so
    # `from_cash_ils` can outrun the balance; the old code clamped the debit at
    # zero AFTER buying the full amount, which is a purchase funded by arithmetic.
    from_cash = min(float(spec.get("from_cash_ils") or 0.0), max(0.0, cash_before))
    budget = round(from_cash + raised, 2)
    if budget <= 0:
        return {"bought": None, "note": "Nothing could be funded, so nothing was bought."}

    native = budget / (fx_rate(ccy) or 1.0)
    qty = native / price
    mk = spec.get("market") if spec.get("market") in {m.value for m in Market} else "NYSE"
    # Blend, never overwrite. This used to call `upsert_positions`, which SETS
    # quantity -- so "Swap into SCHD" on a book already holding SCHD replaced the
    # whole position with just the shares bought and deleted the rest.
    await buy_into_book(session, user, ticker=ticker, market=mk, price=price,
                        shares=qty, asset_class=spec.get("asset_class"))
    await session.commit()
    # Cash pays only its share; sale proceeds went straight into the buy.
    await set_cash(session, user, round(cash_before - from_cash, 2))
    return {"sold": sold, "tax_ils": round(tax_total, 2), "bought": ticker,
            "shares": round(qty, 4), "value_ils": round(budget, 2),
            "from_cash_ils": round(from_cash, 2)}


async def _core_plan(session: AsyncSession, user: User, rows, nav: float,
                     objective: str | None, btgt: dict) -> dict | None:
    """THE ONE SIZING OF "bring the core back to its basket".

    Called by the CARD that offers the action and by the EXECUTOR that performs
    it, which is the whole point. `_rebalance_to` records what happens when a
    card reads one target and the button applies another: the screen said "sell
    4,324" and Accept sold 12,139. One function, two callers, no second opinion.

    Everything ORM-shaped is resolved here; the arithmetic lives in
    `core_realign.plan_realign`, which is pure and testable without a database.
    """
    from app.services import core_realign
    from app.services.allocation_mix import classify_holding as _ch
    from app.services.funding_service import cash_floor_ils
    from app.services.fx import fx_rate, price_currency
    from app.services.sleeve_service import sleeve_occupancy, sleeve_tickers
    from app.services.strategies import core_weights

    core_id = btgt.get("core_strategy_id")
    core_pct = float(btgt.get("core_pct") or 0.0)
    basket = core_weights(core_id) if core_id else None
    if not core_id or not basket or core_pct <= 0 or nav <= 0:
        return None

    claimed = {str(t).upper() for t in await sleeve_tickers(session, user)}

    def _val(p):
        meta = p.meta if isinstance(p.meta, dict) else None
        return (float(p.quantity or 0.0) * float(p.current_price or 0.0)
                * fx_rate(price_currency(p.market, meta)))

    def _gain_frac(p):
        """The share of a position's value that is unrealised GAIN.

        Tax is owed on the gain, not the proceeds, so a plan that estimated tax
        on the whole sale would overstate the cost of every trim and under-fund
        every buy. A loss owes nothing, hence the floor at zero.
        """
        px, basis = float(p.current_price or 0.0), float(p.cost_basis or 0.0)
        return (max(0.0, px - basis) / px) if px > 0 else 0.0

    held: dict[str, float] = {}
    gain: dict[str, float] = {}
    cash_ils = 0.0
    sleeve_val: dict[str, float] = {}
    for p in rows:
        tk = str(getattr(p, "ticker", "") or "").upper()
        if not tk:
            continue
        v = _val(p)
        if v <= 0:
            continue
        if tk == "CASH" or _ch(p) == "Cash":
            cash_ils += v
            continue
        if tk in claimed:
            sleeve_val[tk] = sleeve_val.get(tk, 0.0) + v
            gain[tk] = _gain_frac(p)
            continue
        held[tk] = held.get(tk, 0.0) + v
        gain[tk] = _gain_frac(p)

    # HOW MUCH THE SLEEVES HOLD ABOVE THE SIZES THE USER SET. This is the only
    # money the core may take from them, and it comes from `sleeve_occupancy` --
    # the one function that answers "target versus held" -- rather than from a
    # second subtraction here that could disagree with the Sleeves card.
    occ = await sleeve_occupancy(session, user)
    over = 0.0
    for s in (occ.get("sleeves") or []):
        held_pct = float(s.get("held_pct") or 0.0) * 100.0   # held_pct is a fraction
        want_pct = float(s.get("sleeve_pct") or 0.0)
        if held_pct > want_pct:
            over += (held_pct - want_pct) / 100.0 * nav
    if sleeve_val:
        tot = sum(sleeve_val.values())
        gain["__sleeves__"] = sum(gain.get(t, 0.0) * v for t, v in sleeve_val.items()) / tot

    from app.core.config import get_settings
    plan_row = await get_plan(session, user)   # module-level import; one loader
    out = core_realign.plan_realign(
        basket=basket, core_pct=core_pct, nav=nav, held=held,
        cash_ils=cash_ils, cash_floor_ils=cash_floor_ils(nav, objective, plan_row),
        sleeve_over_ils=over, cgt_rate=float(get_settings().cgt_rate),
        gain_frac=gain)
    out["core_strategy_id"] = core_id
    out["core_strategy_name"] = btgt.get("core_strategy_name") or core_id
    out["sleeve_values_ils"] = {t: round(v, 2) for t, v in sorted(sleeve_val.items())}
    return out


async def _apply_core_to_basket(session: AsyncSession, user: User, spec: dict) -> dict:
    """Bring the core back to the basket it names. Sells, sleeve trim, then buys.

    PRICES BEFORE IT SELLS: every buy leg is priced FIRST and the entire action
    refuses before anything is sold. This was written here first, because
    selling three positions and leaving the whole core in cash on one timed-out
    quote is not survivable. `_apply_buy_funded` and `_apply_fee_swap` sold
    first and priced afterwards until PR 40 gave them the same order -- the
    difference was only ever how much of the book one failure could strand.

    RE-PLANNED AT APPLY TIME, from live rows, rather than executing the spec the
    card was built with. The card may be minutes old and the book may have moved;
    a spec carried from the render is a claim about a book that no longer exists.
    The card's own numbers are still shown to the user as an estimate, which is
    what `_core_plan` being the one sizing function guarantees.
    """
    from app.providers.registry import guarded_quote
    from app.schemas.state_machine import Market
    from app.services.fx import book_nav_ils, fx_rate, price_currency
    from app.services.intake_service import buy_into_book, credit_cash

    objective = spec.get("objective")
    rows = await list_positions(session, user)
    nav = book_nav_ils(rows)
    btgt = await _book_target(session, user, objective)
    plan = await _core_plan(session, user, rows, nav, objective, btgt)
    if not plan or not plan.get("ok"):
        return {"note": ((plan or {}).get("detail")
                         or "There is nothing to realign in your core right now.")}

    buys = [leg for leg in plan["legs"] if leg["action"] == "buy"]
    priced: dict[str, tuple[float, str]] = {}
    for leg in buys:
        tk = leg["ticker"]
        try:
            q = guarded_quote(tk)
            px, ccy = float(q.price), (getattr(q, "currency", None) or "USD")
        except Exception:  # noqa: BLE001
            px, ccy = 0.0, "USD"
        if px <= 0:
            return {"note": (f"Could not get a live price for {tk}, so nothing was "
                             f"sold or bought. Your book is unchanged."),
                    "aborted": True, "unpriced": tk}
        priced[tk] = (px, ccy)

    by_ticker = {(p.ticker or "").upper(): p for p in rows}
    sold, tax_total, raised = [], 0.0, 0.0

    async def _sell_ils(p, amount_ils: float, full: bool) -> None:
        nonlocal tax_total, raised
        meta = p.meta if isinstance(p.meta, dict) else None
        rate = fx_rate(price_currency(p.market, meta)) or 1.0
        px = float(p.current_price or 0.0)
        if px <= 0:
            return
        shares = float(p.quantity) if full else min(float(p.quantity),
                                                    amount_ils / (px * rate))
        if shares <= 0:
            return
        gross, tax = _sale_value_ils(shares, px, float(p.cost_basis or 0.0),
                                     p.market, p.meta)
        remaining = float(p.quantity) - shares
        if remaining <= 1e-9:
            await delete_position(session, user, p.ticker, p.market)
        else:
            await update_position(session, user, str(p.id), quantity=remaining)
        tax_total += tax
        raised += max(0.0, gross - tax)
        sold.append({"ticker": p.ticker, "ils": round(gross, 2),
                     "tax_ils": round(tax, 2), "full_exit": bool(full)})

    for leg in plan["legs"]:
        if leg["action"] == "sell":
            p = by_ticker.get(leg["ticker"])
            if p is not None:
                await _sell_ils(p, float(leg["ils"]), bool(leg.get("full_exit")))
        elif leg["action"] == "trim_sleeves":
            # PRO-RATA ACROSS THE SLEEVE HOLDINGS. Taking it all from one ticker
            # would silently re-weight the sleeves against each other, which is a
            # decision this action was never given.
            vals = plan.get("sleeve_values_ils") or {}
            total = sum(vals.values())
            if total > 0:
                for tk, v in vals.items():
                    p = by_ticker.get(tk)
                    if p is not None:
                        await _sell_ils(p, float(leg["ils"]) * (v / total), False)

    bought = []
    budget = raised
    # Cash may top the buys up, but never below the floor the app enforces
    # everywhere else -- a realignment that leaves the book unable to pay for
    # anything is not an improvement.
    cash_top = float(plan.get("cash_used_ils") or 0.0)
    if cash_top > 0:
        credited = await credit_cash(session, user, -cash_top)
        budget += cash_top if credited is not None else 0.0

    for leg in sorted(buys, key=lambda x: -float(x["ils"])):
        tk = leg["ticker"]
        spend = min(float(leg["ils"]), budget)
        if spend < core_realign_min_leg():
            continue
        px, ccy = priced[tk]
        native = spend / (fx_rate(ccy) or 1.0)
        mk = leg.get("market") if leg.get("market") in {m.value for m in Market} else "NYSE"
        await buy_into_book(session, user, ticker=tk, market=mk, price=px,
                            shares=native / px, asset_class=leg.get("asset_class"))
        budget -= spend
        bought.append({"ticker": tk, "ils": round(spend, 2),
                       "shares": round(native / px, 4)})

    if budget > 0:
        await credit_cash(session, user, budget)
    await session.commit()
    return {"sold": sold, "bought": bought, "tax_ils": round(tax_total, 2),
            "cash_used_ils": round(cash_top, 2),
            "left_as_cash_ils": round(max(0.0, budget), 2),
            "shortfall_ils": plan.get("shortfall_ils"),
            "broker_note": "Tracked book updated — no brokerage order was placed."}


def core_realign_min_leg() -> float:
    from app.services.core_realign import MIN_LEG_ILS
    return MIN_LEG_ILS


async def _create_rule_from_spec(session: AsyncSession, user: User, spec: dict) -> dict | None:
    """Arm a trading rule from an Accept spec. Returns a compact summary for the
    'what changed' confirmation, or None if the spec was malformed."""
    from app.services.rules_service import (
        conflicting_rule_level, create_rule as _mk)
    try:
        # Read the level BEFORE the write: create_rule re-levels a protective
        # rule that already exists rather than stacking a second one, and a
        # confirmation saying "armed" when it actually replaced a 12% stop with
        # a 15% one is claiming something that did not happen.
        was = await conflicting_rule_level(
            session, user, spec["ticker"], spec["rule_type"],
            spec.get("mode", "pct"), spec.get("strategy_id"))
        rule = await _mk(session, user, ticker=spec["ticker"], rule_type=spec["rule_type"],
                         mode=spec.get("mode", "pct"), level=float(spec["level"]),
                         note=spec.get("note"))
    except (KeyError, ValueError, TypeError):
        return None
    unit = "₪" if rule.mode == "price" else "%"
    pretty = rule.rule_type.replace("_", " ")
    label = f"{rule.ticker} {pretty} {rule.level:g}{unit}"
    if was is not None and abs(float(was) - float(rule.level)) > 1e-9:
        label += f" (was {float(was):g}{unit})"
    return {"ticker": rule.ticker, "rule_type": rule.rule_type, "mode": rule.mode,
            "level": rule.level, "previous_level": was,
            "action": "relevelled" if was is not None else "armed",
            "label": label}


async def _apply_fee_swap(session: AsyncSession, user: User, by_ticker: dict, spec: dict) -> dict:
    """Sell the high-fee fund and buy the cheaper equivalent for the same value.

    PRICES BEFORE IT SELLS, for the same reason `_apply_buy_funded` does, and
    with more at stake: this sells the WHOLE position. The old order deleted the
    holding, then asked for a price on the replacement, and on failure credited
    the net proceeds to cash -- turning "swap into a cheaper fund" into a full
    exit the card never proposed, with the tax realized. A swap that cannot buy
    its replacement is not a swap, so it refuses and sells nothing.

    The replacement is still priced live so the 30-min reprice job keeps it
    correct; what changed is only when the price is asked for.
    """
    from app.providers.registry import guarded_quote
    from app.schemas.state_machine import Market
    from app.services.fx import fx_rate
    from app.services.intake_service import buy_into_book

    sell_tk, buy_tk = spec.get("sell"), spec.get("buy")
    p = by_ticker.get(sell_tk)
    if not p or not buy_tk:
        return {"swapped": False}
    try:
        q = guarded_quote(buy_tk)
        price, ccy = float(q.price), (getattr(q, "currency", None) or "USD")
    except Exception:  # noqa: BLE001 - no live price: refuse before anything moves
        price, ccy = 0.0, "USD"
    if price <= 0:
        return {"swapped": False, "aborted": True, "unpriced": buy_tk,
                "note": (f"Could not get a live price for {buy_tk}, so {sell_tk} was "
                         f"not sold. Your book is unchanged.")}
    gross, tax = _sale_value_ils(float(p.quantity), float(p.current_price or 0),
                                 float(p.cost_basis or 0), p.market, p.meta)
    net_ils = max(0.0, gross - tax)
    await delete_position(session, user, sell_tk, p.market)
    native = net_ils / (fx_rate(ccy) or 1.0)  # ILS proceeds -> the buy's trading currency
    qty = native / price
    mk = p.market if p.market in {m.value for m in Market} else "NYSE"
    # Blend if the cheaper fund is already held -- a fee swap into something you
    # own must add to it, not replace it.
    await buy_into_book(session, user, ticker=buy_tk.upper(), market=mk, price=price,
                        shares=qty, asset_class=spec.get("asset_class"))
    await session.commit()
    return {"swapped": True, "sold": [sell_tk], "bought": buy_tk.upper(),
            "value_ils": round(net_ils, 2), "tax_ils": round(tax, 2)}
