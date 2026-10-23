"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time

from app.services import thresholds as _thr
import uuid
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
SLEEVE_DRIFT_BAND_PCT = 5.0       # default; the setting is `sleeve_drift_band_pts` (PR 115)


def _setting(key: str, default):
    """PR 115: a decision the owner can change (`plans.thresholds`), else its default."""
    from app.services import thresholds
    return thresholds.get(key, default)

# Apply kinds that genuinely mutate holdings/plan/rules. Anything else is advice
# the app can't execute for you -- Accept on those used to fall through every
# branch, report "Done -- applied." and silently dismiss the card, so guidance
# looked like it had been carried out when nothing had happened.
_ACTIONABLE_KINDS = {"trim", "sell_losers", "fee_swap", "rebalance_to_objective",
                     "set_objective_and_rebalance", "set_plan", "create_rule", "create_rules",
                     "buy_funded", "sell_position", "redeploy_cash", "fund_sleeve",
                     "fund_plan", "relevel_sleeve_caps", "core_to_basket",
                     # PR 90: resize sleeves toward the plan's Kelly fraction.
                     "frontier_resize",
                     # PR 94: switch the core to the one that best serves the goal.
                     "frontier_core",
                     # PR 96: retune the protective rules for the goal.
                     "frontier_rules",
                     "set_standing_order",
                     # PR 72: a rule that fights a sleeve. Accept carries a
                     # choice -- sell the slice and lower the sleeve, or keep
                     # the position and move the rule. Either changes something.
                     "rule_vs_sleeve",
                     # PR 81: a tuning card edits a rule; the one-time card
                     # converts caps into rebalance bands.
                     "edit_rule", "caps_to_bands",
                     # PR 140: switch paused downside rules back on.
                     "rearm_rules",
                     # PR 83: cash a buy-back hold is keeping back -- a choice.
                     "held_cash",
                     # PR 135b (wave 4): set the sleeve sizes a scored move proposes.
                     "sleeve_move",
                     # PR 135c: an overweight position -- trim, or raise the sleeve.
                     "overweight_choice"}


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


async def plan_ticker_targets(session, user, objective: str | None = None,
                              btgt: dict | None = None) -> dict[str, float]:
    """PR 79 -- what YOUR PLAN wants of each ticker, as a share of NAV.

    Sleeve targets (summed across sleeves, as planned -- not the buy-back-held
    view) plus the core basket scaled to the core's share of the book. This is
    the number a sell rule must not trim through: before PR 79 only the sleeve
    half was checked, so a 15% cap on VTI trimmed straight through the core's
    18.2% target and the realign card bought it back an hour later (09-26,
    09-29 -- measured in the ledger).
    """
    from app.services.strategies import core_weights
    try:
        if btgt is None:
            if objective is None:
                plan = await get_plan(session, user)
                objective = getattr(plan, "objective", None) or "Balanced"
            btgt = await _book_target(session, user, objective)
    except Exception:  # noqa: BLE001 - no target is "no conflict known", never a crash
        return {}
    out: dict[str, float] = {}
    planned = btgt.get("planned_sleeve_targets")
    if planned is None:
        try:
            from app.services import sleeve_service as _sv
            planned = await _sv.all_sleeve_targets(session, user)
        except Exception:  # noqa: BLE001
            planned = {}
    for tk, w in (planned or {}).items():
        out[str(tk).upper()] = out.get(str(tk).upper(), 0.0) + float(w or 0.0)
    core_id, core_pct = btgt.get("core_strategy_id"), float(btgt.get("core_pct") or 0.0)
    basket = core_weights(core_id) if core_id else None
    if basket and core_pct > 0:
        bsum = sum(w for w in basket.values() if w > 0)
        for tk, w in basket.items():
            if w > 0 and bsum > 0:
                out[tk.upper()] = out.get(tk.upper(), 0.0) + (w / bsum) * core_pct
    return out


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
def first_sentences(text: str, n: int) -> str:
    """The first `n` sentences of `text`, verbatim."""
    import re as _re
    parts = _re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u20aa])", (text or "").strip())
    return " ".join(parts[:n]).strip()


def _ensure_why_impact(recs: list[dict]) -> list[dict]:
    """Every recommendation carries an 'audit_trail'; 'why' and 'impact' are
    the card's OWN text or empty.

    PR 100: a missing why/impact used to be filled per dimension ("Moves your
    portfolio closer to your plan."), so SOXL's gain card, 33% of the book
    against a 40% target, told Eran that trimming it moved him toward his plan.
    A sentence the card did not compute is a claim it cannot back, so the card
    now says nothing there instead."""
    for r in recs:
        dim = r.get("dimension", "")
        r.setdefault("apply", {"kind": "none"})
        r["actionable"] = _is_actionable(r)
        r["why"] = r.get("why") or ""
        r["impact"] = r.get("impact") or ""
        # PR 103: the card body is one or two plain sentences -- the card's own
        # first ones; the full why moves behind Details.
        if not r.get("summary"):
            r["summary"] = first_sentences(r["why"], 2)
        r.setdefault("chips", [])
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


def _sleeve_name(strategy_id: str | None) -> str:
    if not strategy_id:
        return ""
    try:
        from app.services.strategy_catalog import CATALOG
        for c in CATALOG:
            if c.get("id") == strategy_id:
                return str(c.get("name") or "")
    except Exception:  # noqa: BLE001 -- a name is decoration, never a reason to fail
        pass
    return ""


def sleeve_blockers(headsups: list[dict]) -> list[dict]:
    """Notices that must not be quiet: a sleeve the app cannot top up.

    Built only from what the funder's own preview said (`meta.blocker`), so the
    banner can never claim more than the refusal does. `choices` are the two things
    the user can actually do; neither moves money (DELETE /plan/sleeves/{id} stops
    steering toward the sleeve and sells nothing).
    """
    out: list[dict] = []
    for h in headsups or []:
        card = h.get("card") or {}
        b = (card.get("meta") or {}).get("blocker")
        if not b:
            continue
        sid = b.get("strategy_id")
        name = _sleeve_name(sid)
        tk = b.get("ticker") or ""
        label = f"{name} ({tk})" if name else tk
        out.append({
            "id": h.get("id"), "kind": "sleeve_unfundable", "severity": "HIGH",
            "title": f"You can't act on the {label} sleeve",
            "line": (f"{tk} is {b.get('actual_pct'):.0f}% of the book against the {b.get('chosen_pct'):.0f}% "
                     f"you chose, and the app cannot top it up: buying about {b.get('amount_ils'):,.0f} ILS "
                     f"would move your book away from your plan."),
            "reason": b.get("reason") or "",
            "strategy_id": sid, "ticker": tk, "sleeve_name": name,
            "amount_ils": b.get("amount_ils"),
            "choices": [
                {"kind": "open_plan", "label": "Open my plan"},
                *([{"kind": "drop_sleeve", "label": "Drop this sleeve",
                    "strategy_id": sid,
                    "confirm": (f"Stop running the {label} sleeve?\n\nNothing is sold and nothing is bought. "
                                f"The app stops steering your book toward this sleeve.")}] if sid else []),
            ],
        })
    return out


def settle_mutes(dismissed: set, completed: set, *, refired: set, persistent: set,
                 stale_high: set, moved: set) -> tuple[set, set]:
    """Which marks still hide a card, after every reason a mute can lapse.

    PR 138 (2026-10-22). `persistent` marks a card that describes a STANDING
    condition it cannot itself resolve, so filing it as DONE says something
    untrue and must not bury it. That protection is for the DONE set only.

    It used to be subtracted from the ignored set as well, which made Ignore a
    button that returned 200, wrote its mark, and changed nothing on screen --
    observed live on the "Safer, lower chance" sleeve-move card (rec_0164ed):
    `POST .../dismiss -> 200`, card still on Today, not listed as hidden. Ignore
    is a statement about the user's attention, not about the book, so no
    property of the card may override it. The only things that un-mute an
    ignored card are a re-fired rule, a card that moved on (amount / severity),
    and the TTL.
    """
    dismissed = set(dismissed) - set(refired) - set(moved)
    completed = set(completed) - set(refired) - set(stale_high) - set(persistent) - set(moved)
    return dismissed, completed


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
    # PR 100: only a card that ASKED for a trade has an amount "to move". QQQ's
    # near-cap card carries its headroom and SOXL's gain card its unrealised
    # gain; "you marked this done ... still 2,155 ILS to move" read those as
    # trades nobody was asked to make.
    if (card.get("apply") or {}).get("kind") == "none":
        return None
    try:
        then_amt = float(entry["amount"])
        now_amt = float(card["est_amount"])
    except (TypeError, ValueError):
        return None
    base = max(abs(then_amt), _REFIRE_FLOOR_ILS)
    if abs(now_amt - then_amt) / base > float(_setting("refire_change_pct", _REFIRE_DELTA * 100.0)) / 100.0:
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


def resolve_marks(dis_map: dict, done_map: dict) -> tuple[dict, set]:
    """One mark per card id: the NEWEST of its ignore mark and its done mark.

    THE DEFECT THIS ANSWERS (Eran, 2026-10-22: "it doesn't matter if I click
    Accept or Ignore, this card always comes back"). The two maps used to be
    merged as `{**ignored, **done}`, so a DONE mark beat an IGNORE mark for the
    same id however old the done was. Observed on production: the TQQQ sleeve
    card was last accepted on 10-20 for 1,423 ILS, then ignored four times on
    10-21, and every Ignore lost -- the done mark (14-day TTL) kept being the one
    compared against today's 2,261 ILS ask, `moved_on` fired on it, and the card
    was un-muted out of BOTH sets. The banner quoted 1,423, the amount of an
    Accept from two days earlier, not of the Ignore he had just pressed.

    The newest action is the user's current statement about the card, so it is
    the one compared. Returns (marks, ids whose winning mark is a DONE) -- the
    second matters because "done" is a claim the book can contradict (PR 38) and
    "ignored" is not, so `moved_on` must be told which one it is holding.
    """
    marks: dict = {}
    done_wins: set = set()
    for rid in set(dis_map) | set(done_map):
        d, c = dis_map.get(rid), done_map.get(rid)
        if d is not None and c is not None:
            use_done = _parse_dt(_at(c)) >= _parse_dt(_at(d))   # a tie goes to done
        else:
            use_done = c is not None
        marks[rid] = c if use_done else d
        if use_done:
            done_wins.add(rid)
    return marks, done_wins


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
    return await _active(session, _dismiss_key(user), int(_setting("ignore_mute_days", _DISMISS_TTL_DAYS)))


async def load_completed(session: AsyncSession, user: User) -> set[str]:
    """Cards the user marked as DONE (``_DONE_TTL_DAYS`` window, tracked separately).

    Ignore and Done mean different things: "not now" vs "handled". Filing them in
    one bucket made "Mark as done" look identical to "Ignore" -- the card landed
    in the ignored list, which is what the user reported.
    """
    return await _active(session, _done_key(user), int(_setting("done_mute_days", _DONE_TTL_DAYS)))


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
    await _mark(session, _dismiss_key(user), rec_id, int(_setting("ignore_mute_days", _DISMISS_TTL_DAYS)), state)
    await _resolve_if_rule_card(session, user, rec_id, "dismissed")


async def complete_recommendation(session: AsyncSession, user: User, rec_id: str,
                                  state: dict | None = None) -> None:
    await _mark(session, _done_key(user), rec_id, int(_setting("done_mute_days", _DONE_TTL_DAYS)), state)
    await _resolve_if_rule_card(session, user, rec_id, "acknowledged")


async def restore_dismissed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user ignored (does not touch completed cards)."""
    return await _clear(session, _dismiss_key(user), int(_setting("ignore_mute_days", _DISMISS_TTL_DAYS)))


async def restore_completed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user marked done."""
    return await _clear(session, _done_key(user), int(_setting("done_mute_days", _DONE_TTL_DAYS)))


async def restore_one(session: AsyncSession, user: User, rec_id: str) -> dict:
    """Remove ONE card's ignored/done mark. Returns which marks it removed.

    Nothing else in either map is touched. Restoring a done card does not undo
    what Accept did -- the trade stays in the book; the card simply becomes
    eligible to show again if the situation still calls for it.
    """
    removed = []
    for how, key in (("ignored", _dismiss_key(user)), ("done", _done_key(user))):
        m = await _load_map(session, key)
        if rec_id in m:
            m.pop(rec_id, None)
            row = await session.get(KVSetting, key)
            if row:
                row.value = json.dumps(m)
            removed.append(how)
    if removed:
        await session.commit()
    return {"id": rec_id, "removed": removed}



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
        from app.services.regime_service import describe as _describe_regime
        _mwhy = _describe_regime(market) if market.get("components") else market.get("rationale", "")
        macro["action"] = (f"The market backdrop is risk-off ({_mwhy}). "
                           "That's not a reason to abandon the rebalance above, but it is a reason "
                           "to phase it in over a few weeks rather than all at once.")
        macro["why"] = ("A risk-off backdrop raises the odds of a near-term drawdown, so the timing "
                        "of a large purchase matters more than usual.")
        macro["impact"] = "Spreads entry risk without leaving you off-plan."
        macro["how"] = ["Split the rebalance into 2-4 tranches over a few weeks",
                        "Start with the broadest, least volatile leg",
                        "Keep your cash target intact while you phase in"]

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


_WAR_ROOM_CACHE: dict = {}


async def _war_room_payload_cached(session, user, rows, compute) -> dict:
    """PR 104c.1: the war room was 2.8 s of the 3.5 s Today build, recomputed on
    every load. Its signals move with prices (a 30-minute job) and holdings, so
    the same book at the same prices within 10 minutes gets the same answer.
    Only a grounded answer is kept: a sample-price one is never reused."""
    key = (user.email, tuple(sorted(((p.ticker or "").upper(), round(float(p.quantity or 0), 6),
                                     round(float(p.current_price or 0), 4)) for p in rows)))
    from app.core.config import get_settings
    ttl = float(get_settings().war_room_cache_ttl_sec)
    hit = _WAR_ROOM_CACHE.get(key) if ttl > 0 else None
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    payload = await compute(session, user, rows, narrate=False)
    if payload.get("grounded") and ttl > 0:
        if len(_WAR_ROOM_CACHE) > 64:
            _WAR_ROOM_CACHE.clear()
        _WAR_ROOM_CACHE[key] = (time.monotonic(), payload)
    return payload


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
        from app.services.war_room import war_room_payload as _war_room_payload
        # narrate=False: the Gemini prose is a war-room-view flourish and is
        # never rendered on a Today card, but it cost 5.4-6.7s of the ~5s
        # endpoint -- one live LLM call per signal, on the event loop, on a
        # single-worker server. Measured: war_room 6726ms vs every other agent
        # under 50ms.
        payload = await _war_room_payload_cached(session, user, rows, _war_room_payload)
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
    # The caps the user set on individual names, so an approved signal cannot
    # size a buy through one. Same read as the Today builder does for the other
    # card producers; this path has its own session. PR 65.
    try:
        from app.services.rules_service import name_caps as _nc
        _name_caps = await _nc(session, user)
    except Exception:  # noqa: BLE001 - a missing cap must not silence the war room
        logger.warning("per-ticker caps unavailable for war-room cards", exc_info=True)
        _name_caps = {}
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
            book_target=_btgt, name_caps=_name_caps,
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
                    + ([f"Use {_ils(fund['from_cash_ils'])} of cash (your "
                        f"{_ils(fund['cash_target_ils'])} cash target stays untouched)"]
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


async def build_recommendations(session: AsyncSession, user: User,
                                simulate_cash_ils: float | None = None,
                                ignore_cash_reserve: bool = False) -> dict:
    """`simulate_cash_ils` (PR 75): the cash card is sized as if you held that
    much cash, NAV raised by the difference. Only the cash card reads it; the
    route that passes it returns only that card, marked simulated, with no
    Accept. Nothing about it is written."""
    _tm = _Stopwatch()
    rows = await list_positions(session, user)
    if not rows:
        # PR 136b (owner: a new user with nothing in the book must still be told
        # what to start with): the optimiser's starter book is the one card an
        # empty portfolio can have. Accept sets up the plan; nothing is bought.
        starter = []
        try:
            from app.services import book_optimizer
            starter = [c for c in await book_optimizer.card(session, user) if (c.get("meta") or {}).get("starter")]
        except Exception:  # noqa: BLE001 -- an empty book still answers
            logger.warning("starter card failed", exc_info=True)
        return {"count": len(starter), "recommendations": starter, "message": "Add holdings to get recommendations."}
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "volatility_pct": (p.meta or {}).get("volatility_pct"),
               "liquidity_score": (p.meta or {}).get("liquidity_score"),
               "asset_class": (p.meta or {}).get("asset_class"),
               "expense_ratio_pct": (p.meta or {}).get("expense_ratio_pct")} for p in rows]
    snap = compute_snapshot(pdicts)
    nav = snap["nav"]
    plan = await get_plan(session, user)
    _thr.use(plan)          # PR 104d: this build honours the user's card thresholds
    cap = effective_caps(plan)["concentration_cap"]
    # THE CAPS THE USER SET, ONE READ, HANDED TO EVERY CARD THAT SIZES A TRADE.
    #
    # A `max_weight` rule has always fired a card once a position was ALREADY
    # over it, and played no part in deciding how much to buy or what to trim.
    # Measured 2026-09-06: NVDA at 10% of the book with a hand-set 12% cap and
    # Equities under its Grow target, so the buy was otherwise legitimate --
    # the funder sized 4,500 and took NVDA to 40.0%, stopping only at the
    # plan-wide cap, 28 points past the user's own limit. The rule then fired a
    # card telling him the cap had been breached. A cap that alerts after the
    # trade it could have prevented is not a cap. PR 65.
    #
    # Read here, at the top, because the geographic card at ~1650 sizes a buy
    # too -- the first version of this loaded it beside the screener and left
    # that one reading the plan-wide cap alone. One read rather than one per
    # producer, for the reason `_ledger` exists: three cards asking the same
    # question separately is how they come to disagree about the answer.
    _name_caps: dict[str, float] = {}
    try:
        from app.services.rules_service import name_caps as _nc
        _name_caps = await _nc(session, user)
    except Exception:  # noqa: BLE001 - a missing cap must not take Today down
        logger.warning("per-ticker caps unavailable this build", exc_info=True)
    objective = plan.objective if plan else "Balanced"
    recs: list[dict] = []
    try:
        from app.services.intake_service import get_cash as _get_cash
        _cash_ils = await _get_cash(session, user)
    except Exception:  # noqa: BLE001
        _cash_ils = 0.0
    # Resolved here, before anything reads it: the funding exclusions need the
    # core basket, and the tax block below needs the same set.
    # PR 75 -- take-profit holds. Released first (a retreated price ends the
    # hold for good), then read once for every buyer in this build.
    from app.services import buyback_hold as _bh
    try:
        if await _bh.release_retreated(session, user, rows):
            # Committed HERE: nothing later in the build commits, so a release
            # left in the session was lost and the hold came back (review,
            # CONFIRMED) -- "released for good" has to be written.
            await session.commit()
        _holds_active = _bh.active(await _bh.load(session, user), rows)
    except Exception:  # noqa: BLE001 - a hold must never cost the Today view
        logger.warning("buy-back holds unavailable", exc_info=True)
        _holds_active = {}
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
    # PR 87: a buy-back hold's reserve is cash already spoken for. Before this
    # only the cash card subtracted it, so every funded buy saw the reserve as
    # free -- production 2026-09-30: "Add to BND ₪841" funded with ₪553.49 from
    # cash, the exact ₪553 Eran had kept for SOXL. Committing it to the shared
    # ledger up front makes every card in this build plan around it (the cash
    # card takes no ledger and subtracts the reserve itself, so no double count).
    _hold_reserve = round(sum((btgt.get("held_back") or {}).values())
                          * float(snap.get("nav") or 0.0), 2)
    if _hold_reserve > 0:
        _ledger.cash_ils = _hold_reserve
    # Which contributing agents failed this build. Each block below is defensive so a
    # data hiccup never breaks Today \u2014 but silence made a missing card
    # indistinguishable from "nothing to do", so failures are now logged and reported.
    degraded: list[str] = []

    # 1) Concentration trim
    # PR 105: over HOLDINGS, not the cash row. `exposure_ticker` includes CASH,
    # so a cash-heavy book got "Trim CASH: sell about ₪13,225 of CASH (~13225
    # shares)". Found the first time `test_card_claims` measured trims instead of
    # skipping them.
    from app.services.intake_service import is_cash_position as _is_cash
    _exp = {t: w for t, w in (snap.get("exposure_ticker") or {}).items() if not _is_cash(t)}
    if _exp and max(_exp.values()) > cap and nav:
        tk = max(_exp, key=_exp.get)
        w = _exp[tk]
        price = next((float(r.current_price or 0) for r in rows if r.ticker == tk), 0)
        trim = (w - cap) * nav
        # PR 102: the share of the POSITION that goes, not ILS over a native
        # price (`int(trim / price)` divided shekels by dollars for a US ticker).
        _qty = sum(float(r.quantity or 0) for r in rows if r.ticker == tk)
        shares = round(_qty * (w - cap) / w, 6) if w > 0 else 0.0
        recs.append({"id": _rid("trim", tk), "dimension": "diversification", "severity": "HIGH",
                     "title": f"Trim {tk}",
                     "why": f"{tk} is {w:.0%} of your portfolio — above your {cap:.0%} single-holding limit, "
                            f"so a bad run in one name could sink the whole book.",
                     "action": f"Sell about {_ils(trim)} of {tk} (~{shares:g} shares) to bring it from "
                               f"{w:.0%} down to your {cap:.0%} limit.",
                     "impact": f"Cuts single-name risk: brings {tk} from {w:.0%} to your {cap:.0%} cap "
                               f"and frees ~{_ils(trim)} to spread across your plan's mix.",
                     "how": [f"Accept sells ~{shares:g} {tk} (~{_ils(trim)}); the proceeds land in cash",
                             "No brokerage order is placed - mirror it at your broker"],
                     "est_amount": round(trim, 2),
                     # PR 102 (Execution plan v2, row 1, approved): a card asks
                     # and Accept does it, so the plan-cap trim sells the excess.
                     # It was guidance-only since 2026-09-22.
                     "apply": {"kind": "trim", "ticker": tk, "shares": shares},
                     "meta": {"ticker": tk, "weight_pct": round(w * 100, 2),
                              "limit_pct": round(cap * 100, 2), "source": "plan_cap"}})
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
    # PR 73: Accept on ANY of these cards rebalances the whole book with real
    # trades, sized by `_rebalance_plan` -- the function Accept runs. The card
    # quotes that plan's sales, buys and tax, so the tax is on screen before the
    # tap rather than discovered in the ledger afterwards.
    try:
        _rp = _rebalance_plan(rows, target)
    except Exception:  # noqa: BLE001 -- a missing quote must not cost the card
        _rp = None
    _rp_line = (f"Accept rebalances the whole book with real trades: sells about "
                f"{_ils(_rp['gross_sold_ils'])}, estimated tax {_ils(_rp['tax_ils'])}, "
                f"buys about {_ils(_rp['buy_ils'])} with what the sales raise"
                if _rp and (_rp["sells"] or _rp["buys"]) else None)
    # Every action past the drift threshold, not the first two. The slice meant
    # `_rid("rebal","Fixed Income")` was never GENERATED on a book drifted in
    # three classes -- not suppressed, not filtered, simply never built. A card
    # that does not exist cannot be restored, and nothing said it was missing.
    for a in report.rebalance_actions:
        # CASH IS NOT A CLASS THIS CARD MAY TRADE, for the same reason
        # `_rebalance_to` excludes it from the classes it moves. PR 54 put a
        # `Cash` key in the book target for the first time, and this renderer --
        # which had never been asked to draw a class that is not investable --
        # produced "Buy Cash ... e.g. BIL". Two things wrong with that card, live
        # on 6044124:
        #
        #   * BIL is a T-bill ETF. BUYING IT SPENDS CASH. The card's stated
        #     purpose is to raise the cash weight. `CLASS_ETF["Cash"]` had sat
        #     unreachable since it was written, because no book target had ever
        #     carried a Cash key.
        #   * Its apply spec is {"kind": "rebalance_to_objective",
        #     "asset_class": "Cash"}, and `_rebalance_to` drops cash rows from
        #     `byc`. Pressing Accept reshuffles Equities and Fixed Income and
        #     moves the cash weight by NOTHING -- a card claiming a change it
        #     cannot produce.
        #
        # The Cash target still reaches `AllocationEngine.compute` above, and
        # must: it is what shrinks the investable legs. What is dropped is the
        # CARD, because raising cash already has an owner that does it properly
        # -- the liquidity card names the ticker, sizes the lot and explains the
        # tax choice, none of which this renderer can do for a class you cannot
        # buy.
        if a.asset_class == "Cash":
            continue
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
                             f"Target is {whence}"] + ([_rp_line] if _rp_line else []),
                     "rebalance_tax_ils": (_rp["tax_ils"] if _rp else None),
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
                if float(btgt.get("commodities_pct") or 0.0) > 0 and _ch(_p) == "Commodities":
                    continue          # PR 71: carved out of the core, own target
                _meta = _p.meta if isinstance(_p.meta, dict) else None
                _v = (float(_p.quantity or 0.0) * float(_p.current_price or 0.0)
                      * _fxr(_pcur(_p.market, _meta)))
                if _v > 0:
                    _held[_tk] = _held.get(_tk, 0.0) + _v

            _ok, _ev = core_matches_basket(_held, _basket,
                                           band_pct=_setting("sleeve_drift_band_pts", SLEEVE_DRIFT_BAND_PCT))
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
                    # THE MONEY THAT MOVES, not the buy leg. This read
                    # `buys_ils`, so a card whose step 1 sold 7,476 of TQQQ was
                    # headlined "Rebalance about 422" -- the buys were all the
                    # basket's legs were short. Understating a trade by 17x is
                    # the card-claim invariant failing at the headline while
                    # every individual leg stayed honest. PR 60.
                    _sold = float(_plan.get("sells_ils") or 0.0)
                    _to_cash = float(_plan.get("to_cash_ils") or 0.0)
                    _moved = max(_sold, float(_plan.get("buys_ils") or 0.0))
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
                                   + (f" About {_ils(_to_cash)} of the proceeds would sit in "
                                      f"cash — the basket does not need it."
                                      if _to_cash >= _REFIRE_FLOOR_ILS else "")
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
                                                   "sleeve_trim_from_ils",   # C15: from where
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
                        "band_pct": _setting("sleeve_drift_band_pts", SLEEVE_DRIFT_BAND_PCT)},
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
                        allow_within_class_swap=True, ledger=_ledger,
                        name_caps=_name_caps)
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
    # The standing order is money the app itself will record every month, so
    # the "current path" includes it. Without this the card kept telling a user
    # who had just set up ₪1,067/month to "add ₪1,067/month".
    try:
        from app.services.standing_order_service import monthly_total as _so_monthly
        _standing = await _so_monthly(session, user)
    except Exception:  # noqa: BLE001
        _standing = 0.0
    # PR 74 -- ONE goal (goal_service) and ONE return basis. Before, this card
    # read the stored `plan.target_amount` (a different number from /plan's live
    # one), counted years from the deadline where /plan counted the horizon, and
    # projected at the static objective table while /plan used the measured
    # return. Now it asks the same questions of the same functions as /plan and
    # /plan/projection, so all three quote one target, one deadline, one return.
    _goal: dict = {}
    _pstats: dict = {}
    _levers = None
    try:
        from app.services.plan_view import book_measured as _measured
        from app.services.plan_view import portfolio_stats as _portfolio_stats
        from app.services.goal_service import compute_goal
        _pstats = _portfolio_stats(rows, await _measured(session, user))
        _goal = await compute_goal(session, user, plan, _pstats.get("nav"))
        if _pstats.get("nav") and _goal.get("target") and _goal.get("years_left"):
            _er = _pstats.get("expected_roi")
            _levers = await _offload(
                _goal_levers, nav=_pstats["nav"],
                rate=(_er if _er is not None else 6.0),
                vol=_pstats.get("volatility") or 12.0,
                years=_goal["years_left"], target=_goal["target"],
                standing_monthly=_standing,
                cons_rate=_OBJ_RETURN.get(objective, 6.5),
                cons_vol=_pstats.get("static_volatility") or _pstats.get("volatility") or 12.0)
            projected_median = _levers["projected"]
    except Exception:  # noqa: BLE001
        logger.warning("goal projection for the behind card failed", exc_info=True)
        projected_median, _levers = None, None
    _er = _pstats.get("expected_roi")
    # PR 92: while a fresh goal verdict speaks for the goal, this card stands
    # down -- it projects at the app-wide measured return, the goal cards at
    # the user's own return confidence, and two goal stories on two bases
    # contradict each other on one screen.
    try:
        from app.services.frontier_service import goal_story_active
        _goal_owned = await goal_story_active(session, user)
    except Exception:  # noqa: BLE001 -- the older card is the safe fallback
        _goal_owned = False
    if not _goal_owned:
        recs += _behind_goal_recs(plan, snap, objective, projected_median, standing_monthly=_standing,
                                  goal=_goal, expected_roi_pct=(_er if _er is not None else None),
                                  return_basis=_pstats.get("assumption_basis"),
                                  measured_pct=_pstats.get("measured_pct_of_book"),
                                  volatility_pct=_pstats.get("volatility"), levers=_levers)
    recs += FeeAgent().recommendations(pdicts)  # Phase 3.2 fee optimizer

    # 5) Manage-the-holdings agents (Phase 4): per-holding Buy/Hold/Trim verdicts,
    #    sector hedging, momentum/trend and income/cost. Each is defensive - a
    #    data hiccup never breaks the Today view.
    trimmed = {(r.get("apply") or {}).get("ticker") for r in recs
               if (r.get("apply") or {}).get("kind") == "trim"}
    recs += await _offload(_holding_verdict_recs, rows, snap, cap, trimmed)
    _tm.mark("holding_verdicts")

    # TAKING MONEY OFF THE TABLE. Built from the levels the user set himself,
    # so the app is not inventing an opinion about when to sell -- it is telling
    # him his own is getting close. See `_profit_recs`. Defensive like every
    # agent around it: a rules read that fails costs the cards, never the view.
    try:
        from app.services.rules_service import list_rules as _lr
        _user_rules = await _lr(session, user)
    except Exception:  # noqa: BLE001
        _user_rules = []
    try:
        _targets = await plan_ticker_targets(session, user)
    except Exception:  # noqa: BLE001 -- without targets no gain card suggests a trim
        _targets = {}
    recs += await _offload(_profit_recs, rows, snap, _user_rules, _targets)
    _tm.mark("profit")
    recs += await _offload(_hedge_recs, rows, snap)
    _tm.mark("hedge")
    # `_sleeve_floor` stops the uptrend card offering a cap BELOW the sleeve
    # target for the same ticker -- the other half of where the live 15% TQQQ
    # cap came from. Same formula as the suggestions list, same blind spot.
    from app.services import sleeve_service as _sv_mom
    try:
        _sleeve_share = {t.upper(): float(w)
                         for t, w in (await _sv_mom.all_sleeve_targets(session, user)).items()}
    except Exception:  # noqa: BLE001 -- a floor is never worth losing the card over
        _sleeve_share = {}
    _sleeve_floor = {t: round(w * 100.0, 1) for t, w in _sleeve_share.items()}
    recs += await _offload(_momentum_recs, rows, snap, _sleeve_floor)
    # PR 77 -- THE HOLDING REVIEW (plan item 6). Reads the nightly measurements;
    # never backtests here. A holding gets at most one trend card: where the
    # review speaks, the older momentum card for the same ticker steps aside.
    _review: list[dict] = []
    try:
        from app.agents.screener_agent import OpportunityAgent as _OA
        from app.services import holding_review as _hr
        from app.services import sleeve_service as _sv_rev
        from app.services.intake_service import is_cash_position as _is_cash
        _rtks = sorted({p.ticker.upper() for p in rows
                        if not _is_cash(p.ticker, p.meta if isinstance(p.meta, dict) else None)})

        def _closes(tks):
            ag, out = _OA(), {}
            for t in tks:
                cl, st = ag._history(t, 260)
                out[t] = cl if st == "ok" else []
            return out
        _cl = await _offload(_closes, _rtks)
        _meas = await _hr.stored(session, _rtks)
        _sleeve_notes: dict[str, str] = {}
        for _t in _rtks:
            if _t in {k.upper() for k in (_sleeve_share or {})} or _t in {
                    k.upper() for k in (await _sv_rev.all_sleeve_targets(session, user))}:
                _hs = await _sv_rev.sleeves_holding(session, user, _t)
                if _hs:
                    _c, _w = await _decision_recommendation(session, user, _hs[0]["strategy_id"], _t)
                    _sleeve_notes[_t] = f"Sleeve holding: its own rule decides. {_w}"
        recs += _off_stop_and_big_move_recs(rows, snap, _user_rules, _cl)
        _trig = getattr(plan, "cash_redeploy_at_ils", None) if plan is not None else None
        _review, _rcards = _holding_review_recs(
            rows, snap, _cl, _meas, _sleeve_notes, _cash_ils,
            float(_trig) if _trig is not None else None,
            armed_stops={str(r.get("ticker") or "").upper() for r in (_user_rules or [])
                         if r.get("active") and r.get("rule_type") == "stop_loss"})
        # Eran, 2026-09-29 (option a): a holding the nightly run has not
        # measured -- bought today, or the run missed it -- is measured now in
        # the background; the next Today load reads the result.
        _kicked = set(_hr.kick_background(
            [r["ticker"] for r in _review if not r["sleeve"] and not r["measurement_usable"]]))
        _later = " Measuring now: reopen Today in a minute or two."
        for _r in _review:
            if _r["ticker"] in _kicked:
                _r["measuring"] = True
                _r["measurement_note"] = (_r["measurement_note"] or "") + _later
        for _c in _rcards:
            if _c["meta"]["ticker"] in _kicked:
                _c["action"] = _c["action"] + _later
        _rt = {c["meta"]["ticker"] for c in _rcards}
        if _rt:
            recs = [r for r in recs if not (
                r.get("dimension") == "momentum"
                and any(str(x.get("ticker", "")).upper() in _rt
                        for x in ((r.get("apply") or {}).get("rules") or [])))]
        recs += _rcards
    except Exception:  # noqa: BLE001 - the review must never cost the Today view
        logger.warning("holding review failed", exc_info=True)
        degraded.append("holding_review")
    _tm.mark("holding_review")
    # PR 143: a rule check that failed, or has not run, is on Today -- the job
    # record alone is read by no one.
    try:
        from app.worker.scheduler import job_state as _js
        _state = _js()
        recs += _rule_check_headsup(_state, _user_rules)
        recs += _job_health_headsup(_state)          # PR 144
    except Exception:  # noqa: BLE001 - reporting must never cost Today
        logger.warning("rule-check heads-up failed", exc_info=True)
    # The FLOOR above is the plan (a cap may not sit below the planned sleeve);
    # the SHARE the cash card sizes buys against honours a take-profit hold.
    if _holds_active and _sleeve_share:
        _sleeve_share, _ = _bh.apply_holds(_sleeve_share, _holds_active,
                                           _bh.weights_of(rows, snap.get("nav") or 0.0))
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

    if simulate_cash_ils is not None:
        _sim_snap = {**snap, "nav": (snap.get("nav") or 0.0) + float(simulate_cash_ils) - _cash_ils,
                     "_real_nav": snap.get("nav") or 0.0}
        _redeploy = await _offload(_redeploy_cash_recs, rows, _sim_snap, plan, objective, cap,
                                   float(simulate_cash_ils), btgt["target"], _name_caps,
                                   _sleeve_share, _holds_active,
                                   round(sum((btgt.get("held_back") or {}).values())
                                         * (_sim_snap.get("nav") or 0.0), 2))
        return {"simulated": True, "simulate_cash_ils": float(simulate_cash_ils),
                "cash_now_ils": round(_cash_ils, 2),
                "note": ("Read-only preview: the cash card as if you held this much cash "
                         "(NAV raised by the difference). The preview itself is not saved and cannot be accepted."),
                "recommendations": [{**r, "apply": {"kind": "none", "simulated": True,
                                                    "legs": (r.get("apply") or {}).get("legs")}}
                                    for r in _redeploy]}
    _reserve = (0.0 if ignore_cash_reserve else
                round(sum((btgt.get("held_back") or {}).values()) * (snap.get("nav") or 0.0), 2))
    # PR 83: a hold's own volatility sizes the held-cash card's suggested split.
    # The volatility is in the review ROWS built above (`metrics`), not in
    # `holding_review.stored()` -- that is the stored backtest (long/short), and
    # reading `metrics` from it gave None on production (SOXL: 115.7% sat in
    # the review row while the card said "no volatility measurement").
    if _holds_active and not ignore_cash_reserve:
        _rv = {str(r.get("ticker") or "").upper(): r for r in (_review or [])}
        for _tk in _holds_active:
            _holds_active[_tk]["vol_20d_pct"] = (
                ((_rv.get(_tk.upper()) or {}).get("metrics") or {}).get("vol_20d_pct"))
    _redeploy = await _offload(_redeploy_cash_recs, rows, snap, plan, objective, cap, _cash_ils,
                               btgt["target"], _name_caps,
                               _sleeve_share, _holds_active, _reserve, _ideas,
                               core_id=btgt.get("core_strategy_id"))
    recs += await _offload(_commodity_recs, rows, snap, objective, plan, cap, _cash_ils,
                           _sleeve_names, _ledger, btgt["target"], _name_caps, btgt.get("core_strategy_id"))

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
                           _cash_ils, _sleeve_names, _ledger, _named, btgt["target"],
                           _name_caps)
    try:
        async with _agent_tx(session):
            from app.services.performance_service import performance as _perf_fn
            recs += _benchmark_recs(await _perf_fn(session, user), objective)
    except Exception:  # noqa: BLE001 -- performance is best-effort, never break Today
        logger.warning("benchmark-lag recommendation failed", exc_info=True)
        degraded.append("performance")

    # Macro signal. PR 108 (D-a): THE STRATEGIES' REGIME, not the futures one.
    # This read `cached_regime()` -- futures, which the code itself calls
    # display-only -- and on 2026-10-07 told the owner to raise cash while the
    # regime his strategies gate on read risk_on 0.95. Cache-only: Today never
    # waits on a fetch; a cold cache means no card, not a guess.
    market = {}
    try:
        from app.services.regime_service import cached_regime_proxy, describe
        market = cached_regime_proxy()
        if market.get("ok") and market.get("state") == "risk_off":
            _why = describe(market)
            recs.append({"id": _rid("macro", "riskoff"), "dimension": "macro", "severity": "MEDIUM",
                         "title": "Markets look risk-off",
                         "why": (f"The market regime your strategies gate on has turned risk-off "
                                 f"({_why}; score {market.get('score')})."),
                         "action": (f"The market backdrop is risk-off ({_why}). "
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
        _floor = _fs2.cash_target_amount_ils(nav, objective, plan)
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
                                f"{_fs2.cash_target_fraction(objective, plan, nav):.0%} cash target for a {objective} "
                                f"plan ({_ils(_floor)}). Too little dry powder means a forced sale "
                                f"at a bad moment."),
                        "action": (f"Sell {_sh} {_c['ticker']} (~{_ils(_need)}) to bring your cash "
                                   f"back to its target. {_c['reason']}."),
                        "impact": f"Restores your cash to its {_ils(_floor)} target.",
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

    # PR 90: the stored frontier verdict, as an actionable resize. Read-only
    # here -- the solve runs nightly or from POST /plan/frontier/refresh, never
    # on the Today path.
    try:
        async with _agent_tx(session):
            from app.services import frontier_service
            recs += await frontier_service.card(session, user, rows)
    except Exception:  # noqa: BLE001
        logger.warning("frontier recommendation failed", exc_info=True)
        degraded.append("frontier")
    # PR 135b (wave 4): add / remove / replace / move-to-a-hedge cards, from the
    # moves the goal model scored overnight. Read-only here.
    try:
        async with _agent_tx(session):
            from app.services import sleeve_moves
            recs += await sleeve_moves.cards(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("sleeve move cards failed", exc_info=True)
        degraded.append("sleeve_moves")
    # PR 136b (wave 5): "the best book we can measure for your goal", from the
    # optimiser's stored result. Read-only here.
    try:
        async with _agent_tx(session):
            from app.services import book_optimizer
            recs += await book_optimizer.card(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("optimizer card failed", exc_info=True)
        degraded.append("optimizer")

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

    # PR 81 -- the advanced rules' own cards: a band BUY (funded through
    # `propose_funded_buy`), the drawdown de-risk, a trend stop without a measured
    # edge, tuning suggestions, and the one-time caps-to-bands offer.
    try:
        async with _agent_tx(session):
            from app.services.advanced_rules import extra_cards
            _adv_cards = await extra_cards(
                session, user, rows=rows, snap=snap, plan=plan, objective=objective,
                cap=cap, cash=_cash_ils, book_target=btgt.get("target"), name_caps=_name_caps,
                ledger=_ledger)
            # A trend-break rule is the user's own subscription to the signal the
            # holding review also reads; one card per ticker, the rule's.
            _trend_tks = {str((c.get("meta") or {}).get("ticker") or "").upper()
                          for c in recs + _adv_cards
                          if (c.get("meta") or {}).get("rule_type") == "trend_break"}
            if _trend_tks:
                recs = [r for r in recs if not (
                    r.get("dimension") == "holding_review"
                    and str((r.get("meta") or {}).get("ticker") or "").upper() in _trend_tks)]
            recs += _adv_cards
    except Exception:  # noqa: BLE001
        logger.warning("advanced rule cards failed", exc_info=True)
        degraded.append("advanced_rules")

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
        # PR 75: the PLANNED targets stay what every card displays and caps
        # against; a ticker on a take-profit hold just gets no top-up card (see
        # the loop). Review: cutting `_targets` itself made the cap card quote
        # "a 30% sleeve" for a planned 40%.
        _held_tks = set(_holds_active or {})
        # The leg each rule currently wants -- one read, shared with the loop
        # below, so the card and `all_sleeve_targets` cannot disagree about
        # which instrument a sleeve is asking for. PR 63.
        _live_sig = await _sv.live_sleeve_targets(session, user)
        # Which sleeves want each ticker, so Accept can route to the right place.
        _wanted_by: dict[str, list[str]] = {}
        for _s in await _sv.list_sleeves(session, user):
            for _t in (sleeve_targets(_s.strategy_id, _s.sleeve_pct,
                                      live=_sv._live_for(_live_sig, _s.strategy_id)) or {}):
                _wanted_by.setdefault(_t.upper(), []).append(_s.strategy_id)
        if _targets and nav:
            _weights = snap.get("exposure_ticker") or {}
            for _tk, _w in sorted(_targets.items()):
                _tk = _tk.upper()
                _target_pct = _w * 100.0
                _actual_pct = (_weights.get(_tk, 0.0) or 0.0) * 100.0
                _gap = _target_pct - _actual_pct
                if abs(_gap) < _setting("sleeve_drift_band_pts", SLEEVE_DRIFT_BAND_PCT):
                    continue
                if _gap > 0 and _tk in _held_tks:
                    continue      # sold on its take-profit; not bought back yet
                _held = _actual_pct > 0.05
                _amount = abs(_gap) / 100.0 * nav
                _cold = not _held and _gap > 0
                if _cold:
                    # CAN THIS BE DONE WITH MONEY THAT IS ALREADY EARMARKED?
                    #
                    # The original objection stands and is quoted below: one tap
                    # taking a book from nothing to a fifth in a 3x fund is not
                    # topping up a drift. But it does not describe every cold
                    # sleeve. After a swap, the money for the new sleeve is
                    # already in the book, sitting in the instrument the old rule
                    # held -- PR 60 made the core defer exactly that position to
                    # this card. Refusing to act then is not caution, it is the
                    # app declining to finish a move it already recommended.
                    #
                    # THE FIRST ATTEMPT AT THAT TEST WAS WRONG, and the suite
                    # caught it. It asked only whether the money was UNCLAIMED
                    # by another sleeve, and `test_k_an_empty_sleeve_card_
                    # cannot_be_buried_by_accept` showed what that misses: on
                    # that fixture it would have taken a book from nothing to
                    # 10% in SOXL by selling an ordinary V position -- the exact
                    # decision the split exists to keep out of one tap. Being
                    # unclaimed is not the same as being earmarked.
                    #
                    # So the test is the RELATIONSHIP: actionable only when every
                    # position being sold is a leveraged instrument that THIS
                    # SLEEVE'S TICKER IS THE BASE OF -- `strategy_catalog.
                    # steps_back_to`, read off the specs, asymmetric on purpose.
                    # Selling TQQQ to hold QQQ qualifies; selling V to start SOXL
                    # does not, and neither does the reverse of the first. The
                    # exposure was already in the book at a higher multiple under
                    # a rule that is no longer running, so this is a downgrade of
                    # something you have, not the start of something you do not.
                    #
                    # Still guidance when a leg belongs to another sleeve (the
                    # piggy-bank failure `_fund_sleeve` already excludes) or when
                    # there is a shortfall (a position at a size nobody chose,
                    # which that function abstains over rather than half-fund).
                    #
                    # Priced through the same `load_basket` the button runs, in
                    # dry-run, so the card cannot show one plan and Accept
                    # execute another -- the `_core_plan` discipline. Wrapped in
                    # a savepoint because a failed agent must not abort the
                    # request's transaction on PostgreSQL.
                    _sids = _wanted_by.get(_tk) or []
                    _sid = _sids[0] if len(_sids) == 1 else None
                    _prev = None
                    if _sid:
                        try:
                            async with _agent_tx(session):
                                from app.services.strategy_service import load_basket
                                _prev = await load_basket(session, user, _sid,
                                                          mode="fund", dry_run=True,
                                                          honour_holds=True)
                        except Exception:  # noqa: BLE001 - a preview must not kill Today
                            _prev = None

                    from app.services import strategy_catalog as _cat
                    from app.services.funding_service import MIN_TRADE_ILS as _MIN_TR

                    _fund = (_prev or {}).get("funding") or {}
                    _claimed_tk = {str(t).upper() for t in await _sv.sleeve_tickers(session, user)}
                    _srcs = [str(x.get("ticker") or "").upper()
                             for x in (_fund.get("sells") or [])]
                    _shortfall = float(_fund.get("shortfall_ils") or 0.0)
                    _downgrade = bool(_srcs) and all(
                        t and t not in _claimed_tk and _cat.steps_back_to(t, _tk)
                        for t in _srcs)
                    _can_act = bool(_prev and _prev.get("ok")
                                    and not _prev.get("nothing_to_do")
                                    and _downgrade and _shortfall < _MIN_TR)

                    if _can_act:
                        # SIZED FROM THE PREVIEW, not from the drift gap. The
                        # executor spends `min(want, funded_ils)`, and a card
                        # that quotes anything else is the "sell 4,324 / sold
                        # 12,139" failure with different numbers.
                        _moves = round(min(float(_prev.get("amount_ils") or 0.0),
                                           float(_fund.get("funded_ils") or 0.0)), 2)
                        _from = ", ".join(sorted(set(_srcs)))
                        _cash_leg = float(_fund.get("from_cash_ils") or 0.0)
                        recs.append({
                            "id": _rid("sleeve_coldstart", _tk), "dimension": "strategy",
                            "severity": "MEDIUM",
                            "title": f"Finish the move: sell {_from}, hold {_tk}",
                            "why": (f"Your plan says {_target_pct:.0f}% of the book in {_tk} "
                                    f"and you hold none of it, while {_from} is still in the "
                                    f"book from the rule you retired. {_tk} is what that rule "
                                    f"stepped back to, so this is the same exposure without "
                                    f"the leverage — not a new position."),
                            "action": (f"Sell {_ils(_moves)} of {_from} and buy {_tk} with the "
                                       f"proceeds"
                                       + (f", plus {_ils(_cash_leg)} of cash above your cash target"
                                          if _cash_leg >= _MIN_TR else "") + "."),
                            "impact": (f"Takes the {_tk} sleeve from nothing to about "
                                       f"{_target_pct:.0f}%. Every funding leg and its estimated "
                                       f"tax is named before anything happens. Tracked book "
                                       f"updated — no brokerage order was placed."),
                            "how": [f"Accept sells {_from} and buys {_tk} in one funded pass",
                                    "The same plan the Plan tab's “Fund this sleeve” runs",
                                    "No brokerage order is placed"],
                            "est_amount": _moves,
                            "persistent": True,
                            "apply": {"kind": "fund_sleeve", "strategy_id": _sid},
                            "meta": {"ticker": _tk, "chosen_pct": round(_target_pct, 1),
                                     "actual_pct": round(_actual_pct, 1), "cold_start": True,
                                     "from": sorted(set(_srcs)), "steps_back": True},
                        })
                        continue

                    recs.append({
                        "id": _rid("sleeve_coldstart", _tk), "dimension": "strategy",
                        "severity": "MEDIUM",
                        "title": f"You chose a {_target_pct:.0f}% {_tk} sleeve and hold none of it",
                        "why": (f"Your plan says {_target_pct:.0f}% of the book in {_tk}; "
                                f"you hold nothing. That is not drift -- the sleeve has "
                                f"never been funded."
                                + (f" {_prev.get('reason')}" if (_prev or {}).get("reason") else "")),
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
                    # PR 105: the share of the POSITION that goes. This divided
                    # `_amount` (shekels) by `current_price` (the ticker's own
                    # currency), so a USD sleeve sold ~3.7x what the card said:
                    # "trim ₪18,550 of SOXL" sold 185 of 300 shares. The sibling
                    # at the concentration trim was fixed the same way in PR 102.
                    "apply": ({"kind": "trim", "ticker": _tk,
                               "shares": round(sum(float(r.quantity or 0) for r in rows
                                                   if (r.ticker or "").upper() == _tk)
                                               * abs(_gap) / _actual_pct, 6)}
                              if _over else _fund_action(_wanted_by.get(_tk) or [])),
                    "meta": {"ticker": _tk, "chosen_pct": round(_target_pct, 1),
                             "actual_pct": round(_actual_pct, 1), "cold_start": False},
                })
                # A TOP-UP THE FUNDER WOULD REFUSE IS NOT AN ACCEPT. (2026-10-22)
                #
                # Eran: "it doesn't matter if I click Accept or Ignore, this card
                # always comes back." Half of that was Ignore losing to an old
                # Accept (`resolve_marks`). The other half is this: the TQQQ
                # sleeve card offered "Add about 2,261 of TQQQ" for days and
                # every Accept was refused by `_drift_gate` -- funding it meant
                # selling Commodities and Equities the plan says he holds too
                # little of, 1.2 -> 9.6 points off target. Eight Accepts, no
                # trade, no mark, the same card back each time, and the alert
                # carried only the one-line error.
                #
                # The card never asked. `sleeve_coldstart` above already prices
                # the button's plan in dry-run before it will offer Accept; this
                # card did not, so it promised a trade the executor was always
                # going to refuse -- the card-claim invariant failing at the
                # button rather than the headline. Same function, same flags as
                # Accept (`honour_holds=True`), so the card cannot preview one
                # plan and Accept run another.
                #
                # When it would be refused the card carries NO action, so the
                # split in `headsup.py` turns it into a one-line Heads-up that
                # says why (PR 102: knowledge without an ask is not a card). A
                # failed preview leaves the card exactly as it was: a preview
                # must never cost Today a card.
                if not _over and recs[-1]["apply"].get("kind") == "fund_sleeve":
                    try:
                        async with _agent_tx(session):
                            from app.services.strategy_service import load_basket
                            _pv = await load_basket(session, user,
                                                    recs[-1]["apply"]["strategy_id"],
                                                    mode="fund", dry_run=True,
                                                    honour_holds=True)
                    except Exception:  # noqa: BLE001 - a preview must not kill Today
                        _pv = None
                    if isinstance(_pv, dict) and _pv.get("ok") is False:
                        _no = str(_pv.get("reason") or _pv.get("error") or "").strip()
                        _held_back = recs[-1]
                        _held_back["why"] = (_held_back["why"]
                                             + (f" The app cannot top it up in one tap: {_no}"
                                                if _no else ""))
                        _held_back["action"] = (f"A top-up of about {_ils(_amount)} is held back: "
                                                f"it would move your book away from your plan, "
                                                f"so there is nothing to accept.")
                        _held_back["impact"] = ("Nothing changes. Make room first (more cash, or a "
                                                "different plan); the Plan tab's “Fund this "
                                                "sleeve” shows the same refusal and the legs "
                                                "behind it.")
                        _held_back["how"] = ["The one-tap top-up is not offered while it would "
                                             "be refused",
                                             "Plan tab → the strategy card → Fund this "
                                             "sleeve shows the legs and the reason",
                                             "No brokerage order is placed either way"]
                        _held_back["apply"] = {"kind": "none"}
                        _held_back["meta"]["held_back"] = _no or str(_pv.get("error") or "")
                        # PR 138: a one-line Heads-up in a collapsed list is too quiet
                        # for "you cannot act on this sleeve". Eran, 2026-10-22: "I
                        # want a bold and strong notification that I can't act on this
                        # sleeve and I need to drop this sleeve." The structured facts
                        # ride on the card; `sleeve_blockers` turns them into the
                        # banner at the top of Today.
                        _sid_b = recs[-1]["apply"].get("strategy_id") if isinstance(recs[-1].get("apply"), dict) else None
                        _held_back["meta"]["blocker"] = {
                            "ticker": _tk, "amount_ils": round(float(_amount), 2),
                            "chosen_pct": round(_target_pct, 1), "actual_pct": round(_actual_pct, 1),
                            "reason": _no or str(_pv.get("error") or ""),
                            "strategy_id": (_wanted_by.get(_tk) or [None])[0]}
                # PR 135c (wave 4): an overweight position is a CHOICE -- trim it
                # back, or raise the sleeve to what you hold -- with the goal
                # model's recommendation. One card per position, as before.
                if _over:
                    try:
                        from app.services import sleeve_moves as _sm
                        _ch = await _sm.overweight_choice(session, user, _wanted_by.get(_tk) or [])
                    except Exception:  # noqa: BLE001 -- the plain trim card stands
                        _ch = None
                    if _ch:
                        _c = recs[-1]
                        _mv, _to = _ch["move"], _ch["move"]["book"][_ch["sleeve"]]
                        _c["apply"] = {"kind": "overweight_choice", "ticker": _tk,
                                       "options": {"trim": _c["apply"],
                                                   "raise": {"kind": "sleeve_move", "held": _ch["held"],
                                                             "book": _mv["book"]}},
                                       "recommended": _ch["recommended"], "recommended_why": _ch["why"],
                                       "sleeve": _ch["sleeve"], "raise_to_pct": _to,
                                       "trim_to_pct": round(_target_pct, 1)}
                        _c["action"] = (f"Choose: trim about {_ils(_amount)} of {_tk} back to "
                                        f"{_target_pct:.0f}%, or raise the sleeve to {_to:g}% -- what you "
                                        f"already hold (sells nothing).")
                        _c["meta"]["choice"] = {"recommended": _ch["recommended"],
                                                "goal_pts": _mv.get("goal_pts"), "halve_pts": _mv.get("halve_pts")}

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
                           f"Holdings › Rules lets you fine-tune or add them one by one."),
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
        # PR 83: the held-cash card has options, not legs.
        _legs = {x["ticker"] for x in (_redeploy[0]["apply"].get("legs") or [])}

        def _competes(r: dict) -> bool:
            if r.get("id") == _rid("cashdrag"):
                return True
            _ap = r.get("apply") or {}
            if _ap.get("kind") == "rebalance_to_objective":
                return True
            # PR 81: a buy YOUR RULE produced (a rebalance band below its
            # target) is not a competitor to be dropped -- it is the user's own
            # discipline, and dropping it left the rule latched with no card,
            # which the banner heal then silently acknowledged.
            if str(r.get("id", "")).startswith("rule_"):
                return False
            # Any sized buy of a ticker the redeploy card already funds, whoever
            # emitted it (war room, commodities, momentum...).
            return _ap.get("kind") in ("buy_funded", "buy") and _ap.get("ticker") in _legs
        recs = [r for r in recs if not _competes(r)]
        # One pot of money, one card: when a rule's own buy spends into a ticker
        # the redeploy card also funds, the rule's card is the one shown.
        _rule_buys = {(r.get("apply") or {}).get("ticker") for r in recs
                      if str(r.get("id", "")).startswith("rule_")
                      and (r.get("apply") or {}).get("kind") in ("buy_funded", "buy")}
        if not (_rule_buys & _legs):
            recs += _redeploy

    # PR 83: a buy-back rule that FIRED owns the buy-back. Below its level the
    # hold no longer cuts the sleeve, so the sleeve top-up and the cash card would
    # also buy the same ticker -- two cards for one decision, and accepting the
    # wrong one leaves the rule latched and the hold on. The rule's card wins.
    _bb_tks = {str((r.get("meta") or {}).get("ticker") or "").upper() for r in recs
               if (r.get("meta") or {}).get("rule_type") == "buy_back"
               and str(r.get("id", "")).startswith("rule_")}
    if _bb_tks:
        from app.services.funding_service import MIN_TRADE_ILS as _MIN_BB
        _kept = []
        for r in recs:
            _ap = r.get("apply") or {}
            _k = _ap.get("kind")
            if str(r.get("id", "")).startswith("rule_") or _k in (
                    "trim", "sell", "sell_losers", "rule_vs_sleeve", "none", None):
                _kept.append(r)
                continue
            _tk = str((r.get("meta") or {}).get("ticker") or _ap.get("ticker") or "").upper()
            if _k == "redeploy_cash":
                _l = [x for x in (_ap.get("legs") or [])
                      if str(x.get("ticker") or "").upper() not in _bb_tks]
                if len(_l) != len(_ap.get("legs") or []):
                    if sum(float(x.get("amount_ils") or 0) for x in _l) < _MIN_BB:
                        continue
                    _ap["legs"] = _l
                _kept.append(r)
                continue
            if _tk in _bb_tks:
                continue
            _kept.append(r)
        recs = _kept

    # Independent agents can contradict each other; reconcile before display.
    recs = _reconcile(recs, market)


    # Every card carries a plain-language "why" and "impact" (safety net for the
    # holding/hedge/momentum/fee agents that don't set them explicitly).
    _ensure_why_impact(recs)

    # PR 70. ONE PRIORITY SCALE. Scored here -- after reconciliation, BEFORE the
    # mute logic -- so the marks that decide whether a hidden card comes back
    # compare severities on the scale the card is now shown on.
    try:
        from sqlalchemy import select as _sel

        from app.models.tables import TradingRule as _TR
        from app.services.card_priority import apply_priority
        from app.services.fx import fx_rate as _fxp, price_currency as _pcp
        _pw: dict[str, float] = {}
        if nav:
            for _p in rows:
                _pm = _p.meta if isinstance(_p.meta, dict) else {}
                _tkp = str(_p.ticker or "").upper()
                _pw[_tkp] = _pw.get(_tkp, 0.0) + (float(_p.quantity) * float(_p.current_price or 0)
                                                  * _fxp(_pcp(_p.market, _pm))) / nav
        _mw = {str(t).upper(): float(lv) for t, lv in (await session.execute(
            _sel(_TR.ticker, _TR.level).where(_TR.subject == user.email, _TR.active.is_(True),
                                              _TR.rule_type == "max_weight"))).all()}
        apply_priority(recs, nav=float(nav or 0.0), weights=_pw, max_weight_rules=_mw)
        from app.services.card_priority import fold_alternatives
        recs = fold_alternatives(recs)
    except Exception:  # noqa: BLE001 -- a card keeps its own severity rather than vanish
        logger.warning("card priority scoring failed; generator severities kept", exc_info=True)
        degraded.append("card_priority")

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
        _marks, _done_wins = resolve_marks(_dis_map, _done_map)

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
                        # Fired again since. Do not suppress its card -- hiding
                        # a new firing is the deadlock this block exists to break.
                        _refired.add(_hid)
                        continue
                    # PR 109: the latch is cleared by the evaluator
                    # (`rules_service.heal_dismissed_rules`), not by a read.
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
        _high_cut = datetime.now(timezone.utc) - timedelta(days=int(_setting("ignore_mute_days", _DISMISS_TTL_DAYS)))
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
            _why = moved_on(_marks.get(_rid_), _r, done=(_rid_ in _done_wins))
            if _why:
                _moved[_rid_] = _why

        dismissed, completed = settle_mutes(
            dismissed, completed, refired=_refired, persistent=_persistent,
            stale_high=_stale_high, moved=set(_moved))

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

    # PR 70: by priority score; within 5 points your own rule or a protective
    # card first. Falls back to the severity order when a card was not scored.
    from app.services.card_priority import sort_key as _prio_key
    recs.sort(key=lambda r: (_prio_key(r) if r.get("priority") else (0, 0.0),
                             _SEV.get(r["severity"], 9)))
    _tm.mark("reconcile_and_filter")
    # Phase 3.3: validate the Risk Agent's beta against history before surfacing.
    from app.services.fx import holding_value_ils as _hv
    bt_holdings = [{"ticker": d["ticker"], "asset_class": d.get("asset_class") or "Equities",
                    "value_ils": _hv(d)} for d in pdicts]
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
        _pre_contra = {r.get("id"): r for r in recs}
        recs, contradictions = _contra(recs, sleeve_targets=_sleeve_tgts, nav=nav,
                                       weights=_w, book_target=btgt["target"])
        # PR 72: a rule you armed that fights a sleeve you chose is a DECISION,
        # not something to hide. See `_rule_conflicts_to_decisions`.
        recs, contradictions = await _rule_conflicts_to_decisions(
            session, user, recs, contradictions, _pre_contra, _w, nav=nav)
        # PR 79: the same decision for CORE targets, and for trims that overshoot.
        recs = await _rule_vs_plan_decisions(session, user, recs, _w, nav=nav, btgt=btgt)
        if contradictions:
            logger.info("today: %d contradiction(s), %d card(s) suppressed",
                        len(contradictions), sum(1 for c in contradictions if c.get("suppressed")))
    except Exception:  # noqa: BLE001
        logger.warning("card scope / contradiction pass failed", exc_info=False)
        for _r in recs:
            _r.setdefault("scope", {"kind": "book", "strategy_id": None,
                                    "name": None, "label": "Whole book"})
    _tm.mark("scope_and_contradictions")

    # A CARD THE CONTRADICTION PASS HID IS LISTED, NOT LOST. It goes into the
    # same history list as ignored and done cards, badged "blocked", with the
    # reason -- so "why is there no card about X" has an answer on screen.
    for _c in contradictions:
        if _c.get("suppressed") and _c.get("card"):
            _hidden_cards.append({
                "id": _c.get("card"), "title": _c.get("title"), "severity": None,
                "dimension": None, "how": "blocked", "hidden_at": None,
                "est_amount": None, "reason": _c.get("detail")})

    # PR 72: THIS BLOCK USED TO RUN BEFORE THE CONTRADICTION PASS, and that
    # ordering was the whole of Eran's 2026-09-28 item 1. It recorded
    # `rule_8cd9f1ff` (SOXL take-profit) as carded; `contradictions.check` then
    # dropped that card because SOXL sat at its 40% sleeve target -- so the
    # banner said "1 trading rule triggered" over a list with nothing to tap.
    # It now runs on the cards that are actually served. Moving it alone would
    # have been worse: the heal below would then have silently RETIRED the rule.
    # `_rule_conflicts_to_decisions` is what prevents that -- a rule that fights
    # a sleeve comes back as a decision card, which this block counts.
    #
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
    # PR 102: A CARD ASKS YOU TO DO SOMETHING, AND ACCEPT DOES IT. Everything
    # else is a heads-up: one line, no "Mark as done", dismissed for good.
    from app.services import headsup as _hu
    recs, headsups, _dropped = _hu.split(recs, nav=float(snap.get("nav") or 0.0),
                                        actionable=_is_actionable)
    try:
        _hu_gone = await _hu.dismissed(session, user)
    except Exception:  # noqa: BLE001 -- a store hiccup shows them, never hides cards
        _hu_gone = set()
    # PR 138: a refusal the user must act on is not dismissible knowledge. It is
    # read off the heads-ups BEFORE the dismissed ones are removed, so swiping the
    # one-liner away cannot silence it; it ends when the sleeve becomes fundable or
    # is dropped (the card is then simply not generated).
    blockers = sleeve_blockers(headsups)
    headsups = [h for h in headsups if h["id"] not in _hu_gone]

    rule_banner = {"triggered": [], "carded": [], "blocked": [], "healed": [],
                   "uncarded": [], "skipped_reason": None}
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
            from sqlalchemy import select as _select

            from app.models.tables import TradingRule as _TR
            _live = (await session.scalars(
                _select(_TR).where(_TR.subject == user.email, _TR.active.is_(True),
                                   _TR.triggered.is_(True)))).all()
            # A triggered rule shown as a heads-up has a visible place too.
            _carded = {str(r.get("id", ""))[5:] for r in recs + headsups
                       if str(r.get("id", "")).startswith("rule_")}
            # A RULE WHOSE CARD THE CONTRADICTION PASS HELD BACK IS NOT AN ORPHAN.
            #
            # It had a card; the app chose not to serve it, and the history lists
            # it as blocked with the reason. Healing it would retire the rule on
            # the strength of the app's own suppression -- found in review of
            # this PR, reproduced through the full build: a 30% cap on a 40%
            # sleeve ticker (a stale cap; `max_weight` cannot become a decision)
            # was acknowledged on every Today load, the churn the self-heal
            # comment above says was fixed; and a take-profit whose conversion
            # hit a transient error was switched off for good. Before this PR the
            # banner ran BEFORE the pass, so these counted as carded and nothing
            # was healed. Now they are neither carded (the banner may not count
            # a card you cannot tap) nor healed.
            _blocked = {str(c.get("card", ""))[5:] for c in contradictions
                        if c.get("suppressed") and str(c.get("card", "")).startswith("rule_")}
            for _r in _live:
                _short = str(_r.id)[:8]
                rule_banner["triggered"].append(_short)
                if _short in _carded:
                    rule_banner["carded"].append(_short)
                    continue
                if _short in _blocked:
                    rule_banner["blocked"].append(_short)
                    continue
                # PR 109: REPORTED, NOT RESOLVED. This acknowledged the rule,
                # and for a one-shot rule (stop_loss, trailing_stop...)
                # acknowledging sets active=False -- so a read of Today could
                # silently switch off a triggered stop whose card was not
                # served. The banner counts only carded rules (index.html
                # `renderRuleBanner`), so an uncarded latch cannot deadlock it.
                rule_banner["uncarded"].append(_short)
        except Exception:  # noqa: BLE001
            logger.warning("rule banner reconciliation failed", exc_info=False)

    return {"count": len(recs), "objective": objective, "recommendations": recs[:12],
            # PR 70: past the first 12 a card used to vanish without a trace, and
            # which ones vanished is now decided by the priority score. They are
            # served, collapsed under "N more lower-priority suggestions".
            "more_recommendations": recs[12:],
            # PR 102: knowledge without an ask -- bottom of Today, above the
            # Holding review (D2), collapsed.
            "headsups": headsups, "headsups_dropped": _dropped,
            "blockers": blockers,
            # PR 103: "Your goal", said once above the goal cards.
            "goal_summary": next((r["goal_summary"] for r in recs if r.get("goal_summary")), None),
            "goal_summary_head": next((r["goal_summary_head"] for r in recs if r.get("goal_summary_head")), None),
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
            # PR 77: one row per non-cash holding, every number behind its state.
            "holding_review": _review,
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


def _fv_monthly(monthly: float, r: float, years: float) -> float:
    """Future value of `monthly` paid at month-end for `years` at `r` a year."""
    rm, n = r / 12.0, int(round(years * 12))
    if monthly <= 0 or n <= 0:
        return 0.0
    return monthly * (((1 + rm) ** n - 1) / rm) if rm else monthly * n


def _goal_levers(*, nav: float, rate: float, vol: float, years: float, target: float,
                 standing_monthly: float, cons_rate: float, cons_vol: float) -> dict:
    """Every number the behind card states, from ONE engine (the one
    /plan/projection runs: same seed, same inputs).

    PR 74 review, CONFIRMED: the monthly amount used to be sized with plain
    compound growth while the headline was a Monte-Carlo median, which carries
    volatility drag. At NAV 30,000, 25%/yr, 67.5% vol, 5.26 years, target
    120,000 the card said "about ₪680/month closes the gap"; the engine's
    median WITH 680/month was ₪88,299 -- still short, so the card claimed a
    change it does not produce. Here the amount is SEARCHED on the engine: the
    smallest whole ₪10 whose median reaches the target.

    `cons_*` is the objective table's rate at the table's own volatility, the
    like-for-like cautious figure. Monotone in the deposit, so bisection holds.
    """
    import math

    from app.engines.simulation_engine import SimulationEngine

    from app.engines.return_convention import drift_pct

    def median(r: float, v: float, monthly: float) -> float:
        # PR 76: `r` is a compound rate; the engine gets its drift, exactly as
        # /plan/projection does, so the card and the screen quote one median.
        return float(SimulationEngine(seed=7).run(
            initial_value=nav, expected_return_pct=drift_pct(r, v), volatility_pct=v,
            horizon_years=years, target_value=target,
            contribution_per_year=(monthly * 12 if monthly > 0 else None)).nominal.p50)

    def need(r: float, v: float) -> float | None:
        if median(r, v, standing_monthly) >= target:
            return 0.0
        lo, hi = 0.0, 10.0
        while median(r, v, standing_monthly + hi) < target:
            lo, hi = hi, hi * 2
            if hi > 10_000_000:
                return None          # no realistic monthly amount closes it
        while hi - lo > 10.0:
            mid = (lo + hi) / 2
            if median(r, v, standing_monthly + mid) >= target:
                hi = mid
            else:
                lo = mid
        return math.ceil(hi / 10.0) * 10.0

    return {"projected": median(rate, vol, standing_monthly),
            "conservative": median(cons_rate, cons_vol, standing_monthly),
            "pmt": need(rate, vol), "pmt_cons": need(cons_rate, cons_vol),
            "rate": rate, "vol": vol, "cons_rate": cons_rate, "cons_vol": cons_vol}


def _behind_goal_recs(plan, snap, objective, projected_override=None,
                      standing_monthly: float = 0.0, *, goal: dict | None = None,
                      expected_roi_pct: float | None = None,
                      return_basis: str | None = None,
                      measured_pct: float | None = None,
                      volatility_pct: float | None = None,
                      levers: dict | None = None) -> list[dict]:
    """When the plan won't reach the goal on the current path, the ONE lever
    that legitimately moves it: more money in.

    PR 74 (Eran's item 4). The goal is `goal_service`'s -- the money you put in,
    compounded at your ROI to the deadline -- and years are years LEFT to that
    deadline, the same everywhere. The headline return is the MEASURED one /plan
    uses; the conservative objective-table figure is a second line, never the
    headline. "Set a target you'll actually hit" (rewrote the target to the
    projection) and "Give it more time" (rewrote the horizon) are GONE: both
    moved the goalposts to meet the ball, and Eran removed them (Q2).

    levers: `_goal_levers` -- projection, conservative figure and both monthly
    amounts from the engine /plan/projection runs. Without it (a failed run,
    or a unit test) every figure is fixed-return compounding, amount included.
    `projected_override` is kept for the call signature and ignored."""
    out: list[dict] = []
    goal = goal or {}
    target = goal.get("target")
    years = goal.get("years_left")
    nav = snap["nav"]
    if plan is None or not target or not years or years <= 0 or not nav:
        return out
    target = float(target)
    r_cons = (_OBJ_RETURN.get(objective, 6.5)) / 100.0
    r = (float(expected_roi_pct) / 100.0) if expected_roi_pct is not None else r_cons
    measured = return_basis not in (None, "static")

    def _pmt(rate: float, short: float) -> float:
        rm, n = rate / 12.0, max(1, int(round(years * 12)))
        p = short * rm / (((1 + rm) ** n) - 1) if rm else short / n
        # Rounded UP to a whole ₪10 -- the amount a person actually types into a
        # bank's standing-order form, and never below what closes the gap.
        return -(-p // 10.0) * 10.0

    if levers:
        # ONE engine for every number on the card (see `_goal_levers`).
        projected, proj_basis = float(levers["projected"]), "montecarlo"
        conservative = float(levers["conservative"])
        pmt, pmt_cons = levers.get("pmt"), levers.get("pmt_cons")
        if pmt is None:
            logger.warning("behind card: no monthly amount under 10M closes the gap; not shown")
            return out
    else:
        # No engine run (tests, or it failed): the fixed-return pair, sized on
        # the SAME formula it projects with, so the claim still holds for it.
        # `projected_override` is deliberately NOT used here: a Monte-Carlo
        # headline with a fixed-return monthly amount is the mismatch the
        # review caught. The engine path is `levers`.
        projected = nav * (1 + r) ** years + _fv_monthly(standing_monthly, r, years)
        proj_basis = "fixed-return"
        conservative = nav * (1 + r_cons) ** years + _fv_monthly(standing_monthly, r_cons, years)
        pmt = pmt_cons = None
    if projected >= target:
        return out  # on track on the current path

    gap = target - projected
    behind_pct = round((1 - projected / target) * 100) if target else 0
    sev = "CRITICAL" if behind_pct >= 40 else "HIGH"
    deadline = goal.get("deadline") or "your deadline"

    if pmt is None:
        pmt = _pmt(r, gap)
        pmt_cons = _pmt(r_cons, max(0.0, target - conservative))
    new_total = round(standing_monthly + pmt, 2)
    if return_basis == "measured":
        _rate_word = f"your measured ~{r * 100:.1f}%/yr"
    elif measured:
        _rate_word = (f"your part-measured ~{r * 100:.1f}%/yr"
                      + (f" ({measured_pct:g}% of the book measured)" if measured_pct is not None else ""))
    else:
        _rate_word = f"~{r * 100:.1f}%/yr (instrument table, not measured)"
    if proj_basis == "montecarlo":
        # A median of simulations. Since PR 76 it compounds at the stated rate
        # (`return_convention`), so the rate and the number agree; the volatility
        # is still said, because it is what makes the range so wide.
        _vol = (levers or {}).get("vol") or volatility_pct
        _how = (f"the median of 10,000 simulations at {_rate_word}"
                + (f" and ~{_vol:.0f}% volatility" if _vol else ""))
        _path = (f"With your standing order of {_ils(standing_monthly)}/month, {_how} reaches about "
                 if standing_monthly > 0 else f"{_how[0].upper()}{_how[1:]} reaches about ")
    else:
        _path = (f"With your standing order of {_ils(standing_monthly)}/month, at {_rate_word}, you'd reach about "
                 if standing_monthly > 0 else f"At {_rate_word} you'd reach about ")
    # The SECOND line exists to put a cautious figure beside a measured one.
    # With no measurement the headline already IS a table rate, and a second
    # table rate beside it is noise. Called "conservative" only when its
    # OUTCOME is lower (since PR 76 both compound at their rates, so it is,
    # whenever the table rate is the lower one).
    if not measured:
        _cons_line = ""
    else:
        _cons_word = ("a conservative" if conservative < projected else "the objective table's")
        _cons_line = (f"At {_cons_word} {r_cons * 100:.1f}%/yr: about {_ils(conservative)}"
                      + (f", and closing the gap would take about {_ils(pmt_cons)}/month."
                         if pmt_cons else "."))
    out.append({"id": _rid("behind", "contrib"), "dimension": "goal", "severity": sev,
                "title": ("You're behind — raise your monthly contribution" if standing_monthly > 0
                          else "You're behind — add a monthly contribution"),
                "why": f"{_path}{_ils(projected)} of your {_ils(target)} goal by {deadline} "
                       f"(~{behind_pct}% short), so nothing changes unless you add to it."
                       + (f" {_cons_line}" if _cons_line else ""),
                "action": (f"Raise your standing order from {_ils(standing_monthly)} to about "
                           f"{_ils(new_total)}/month to close the gap by {deadline}."
                           if standing_monthly > 0 else
                           f"Investing about {_ils(pmt)}/month would close the gap by {deadline}."),
                "impact": f"About {_ils(pmt)}/month more closes the ~{_ils(gap)} gap and puts your "
                          f"{_ils(target)} goal back within reach by {deadline}.",
                "how": [(f"Accept sets your standing order to {_ils(new_total)}/month"
                         if standing_monthly > 0 else
                         f"Accept sets up a standing order of {_ils(pmt)}/month into cash, on the 1st"),
                        "Set the same transfer up at your bank -- the app records it, it does not move money",
                        "Cash above your target then gets a card that puts it back to work",
                        *([goal["breakdown"]] if goal.get("breakdown") else [])],
                "est_amount": round(pmt, 2),
                "goal_target_ils": round(target, 2), "goal_deadline": goal.get("deadline"),
                "projected_ils": round(projected, 2),
                "goal_years_left": round(float(years), 1),
                "return_pct": round(r * 100, 2), "return_basis": return_basis or "static",
                "conservative_return_pct": round(r_cons * 100, 2),
                "conservative_projection_ils": round(conservative, 2),
                "apply": {"kind": "set_standing_order", "monthly_total_ils": new_total,
                          "add_ils": round(pmt, 2)}})
    _proj_formula = (f("Projection", "projected = Monte-Carlo median (10,000 sims)",
                       substituted=f"NAV {nav:,.0f}, {years:.1f}y at {r * 100:.1f}%",
                       result=f"₪{projected:,.0f}")
                     if proj_basis == "montecarlo" else
                     f("Projection", "projected = NAV x (1+r)^years + standing order FV",
                       substituted=f"{nav:,.0f} x (1+{r:.3f})^{years:.2f}", result=f"₪{projected:,.0f}"))
    out[-1]["audit_trail"] = audit_for("goal",
        raw_data={"nav": round(nav, 2), "target": round(target, 2), "projected": round(projected, 2),
                  "projection_basis": proj_basis, "behind_pct": behind_pct,
                  "annual_return_pct": round(r * 100, 2), "return_basis": return_basis or "static",
                  "conservative_return_pct": round(r_cons * 100, 2),
                  "conservative_projection": round(conservative, 2),
                  "years": round(float(years), 3), "deadline": goal.get("deadline"),
                  "put_in": goal.get("put_in"), "goal_basis": goal.get("basis")},
        formulas=[
            f("Goal", "target = sum(deposit x (1+roi)^(years to deadline))",
              result=f"₪{target:,.0f}"),
            _proj_formula,
            f("Monthly contribution", "pmt = gap x (r/12) / ((1+r/12)^(12*years) - 1)",
              result=f"₪{pmt:,.0f}/mo")])

    # Lever B — shift to a higher-growth mix (only if not already Grow). BOTH
    # sides the same kind of number: the objective table's rate compounded, for
    # Grow and for the current objective. (Review, CONFIRMED: comparing a
    # compounded Grow figure with a Monte-Carlo median invented the uplift.)
    if objective != "Grow":
        _g = _OBJ_RETURN["Grow"] / 100.0
        proj_grow = nav * (1 + _g) ** years + _fv_monthly(standing_monthly, _g, years)
        proj_now = nav * (1 + r_cons) ** years + _fv_monthly(standing_monthly, r_cons, years)
        if proj_grow > proj_now:
            out.append({"id": _rid("behind", "grow"), "dimension": "goal", "severity": "HIGH",
                        "title": "Shift to a higher-growth mix",
                        "action": f"Switching from {objective} to a Grow mix raises expected growth: on the "
                                  f"objective table, about {_ils(proj_grow)} by {deadline} instead of "
                                  f"{_ils(proj_now)} — but with bigger swings along the way.",
                        "how": ["Change your objective to Grow (more equities, fewer bonds)",
                                "Accepting this rebalances your holdings toward the Grow target",
                                "Make sure the extra volatility still fits your risk tolerance"],
                        "est_amount": round(proj_grow - proj_now, 2),
                        "apply": {"kind": "set_objective_and_rebalance", "objective": "Grow"}})
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
    from app.services.intake_service import is_cash_position
    for p in rows:
        tk = p.ticker
        # A cash balance is not a holding with a thesis. Without this the
        # screener rates it like a security and offers "CASH screens well
        # (fundamentals 80/100) ... adding on weakness is reasonable" -- seen in
        # the sandbox, where the builtin provider answers for any symbol. It has
        # not reached production because Yahoo returns no fundamentals for
        # "CASH", so the score is None and the loop skips it by accident. An
        # accident is not a guard, and the day the provider changes it is a card
        # telling you to buy more of your own bank balance.
        if is_cash_position(tk, p.meta if isinstance(p.meta, dict) else None):
            continue
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


# --------------------------------------------------------------------------- #
# Taking money off the table
# --------------------------------------------------------------------------- #
#: How close a position must come to its own TAKE-PROFIT before the app says
#: so. Ten percent of price: near enough that the decision is real, far enough
#: that it is not simply announcing the firing a day early.
_NEAR_RULE_FRAC = 0.10

#: ...and the same question for a TRAILING STOP, which cannot use the same
#: measure. A trailing stop RATCHETS: it sits at peak x (1 - trail), so a 10%
#: trail is ~10% below the price BY CONSTRUCTION whenever the position is at
#: its peak. "Within 10% of the level" is therefore its normal resting state,
#: not news.
#:
#: MEASURED on the live book the hour this shipped, which is how it was caught:
#: VTI, BND and QQQ all produced "closing on your trailing stop" while having
#: given back 12.9%, 14.2% and **7.8%** of their trails respectively. QQQ was
#: 7.8% of the way from its peak to its stop and the app called it closing.
#:
#: The honest measure is how much of the trail has actually been given back:
#:      (peak - price) / (peak - stop)
#: which is 0 at the peak and 1 at the stop, whatever the trail width.
_TRAIL_GIVEBACK = 0.60

#: Unrealised gain, against cost, at which a holding is worth a conversation
#: even with no rule on it at all.
_GAIN_CARD_PCT = 25.0

#: How close to a `max_weight` cap counts as drifting INTO it. A winner crosses
#: its cap by growing, and the day it crosses is a worse day to find out.
_NEAR_CAP_PCT_POINTS = 2.0


def _raise_cap_act(rule: dict, tk: str, cap_pct: float, targets: dict | None) -> dict | None:
    """Eran (D3): a near-cap heads-up offers ONE act -- raise the cap to the plan
    target plus the rebalance band -- when the cap sits within one band of the
    target, i.e. when the cap is what is out of step with the plan."""
    from app.services.advanced_rules import DEFAULTS
    t = (targets or {}).get(tk)
    if t is None or not rule.get("id"):
        return None
    band = float(_thr.get("rebalance_band_pts", DEFAULTS["rebalance_band"]))
    t_pct = float(t) * 100.0
    new = round(t_pct + band, 1)
    if abs(cap_pct - t_pct) > band or new <= cap_pct:
        return None
    return {"label": f"Raise the cap to {new:g}% (target {t_pct:.1f}% + {band:g})",
            "apply": {"kind": "edit_rule", "target_rule_id": str(rule["id"]), "level": new}}


def _float_or_none(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


#: One-day move, in percent, at which a holding earns a line of its own.
_BIG_DAY_MOVE_PCT = 10.0


def _protection_off_headsup(rules: list[dict], *, carded: set[str]) -> list[dict]:
    """PR 140 (owner, 2026-10-23): every downside rule that is switched off, in
    ONE heads-up with one act -- "Re-arm all".

    Read live on 10-22: about twenty stops and trailing stops across SOXL, TQQQ,
    QQQ, VTI, VXUS, XLE, COMT, UGA and USO were off, switched off by the
    protection card, and nothing on Today said so; they were found only when a
    stop that should have fired was silent. A stop that is off AND crossed
    already has its own HIGH card (`stop_off`, PR 138) and is left out here.

    The id is the paused SET, so Ignore hides this list only: a rule paused
    later brings a new heads-up. Auto re-arm is on the BACKLOG (owner's choice).
    """
    from app.services.frontier_service import DOWNSIDE_RULE_TYPES
    off = [r for r in rules or []
           if not r.get("active") and r.get("rule_type") in DOWNSIDE_RULE_TYPES and r.get("id")
           and str(r["id"]) not in carded and not r.get("breached_now")]
    if not off:
        return []
    off.sort(key=lambda r: (str(r.get("ticker") or ""), str(r.get("rule_type") or "")))
    ids = [str(r["id"]) for r in off]
    names = [f"{r.get('ticker')} {str(r.get('rule_type')).replace('_', ' ')}" for r in off]
    n = len(off)
    return [{
        "id": _rid("protection_off", *ids), "dimension": "rule", "severity": "MEDIUM",
        "title": f"{n} protective rule{'s are' if n > 1 else ' is'} switched off",
        "action": (f"{n} rule{'s' if n > 1 else ''} that sell on the way down "
                   f"{'are' if n > 1 else 'is'} off, so {'they' if n > 1 else 'it'} will not fire: "
                   + ", ".join(names) + "."),
        "how": [*(f"Off: {x}" for x in names),
                "Re-arm all switches them back on; nothing is sold by re-arming"],
        "est_amount": None, "apply": {"kind": "none"},
        "headsup_act": {"label": f"Re-arm all {n}", "apply": {"kind": "rearm_rules", "rule_ids": ids}},
        "meta": {"headsup_kind": "protection_off", "rule_ids": ids}}]


RULE_CHECK_STALE_MIN = 75      # the check runs every 30 minutes; two missed runs is news


def _rule_check_headsup(state: dict, rules: list[dict], now=None) -> list[dict]:
    """PR 143 -- your rules were not checked, said where you look.

    The rules are checked only inside the 30-minute price job. Its failures used to
    be a log line and a green job; now the job fails with the reason, and this
    turns that record into one heads-up: the last check failed, or none has
    succeeded for `RULE_CHECK_STALE_MIN` minutes. Only while the scheduler runs
    and you have an armed rule -- with nothing armed there is nothing to miss.
    """
    from datetime import datetime, timezone
    if not (state or {}).get("scheduler_running"):
        return []
    if not any(r.get("active") and r.get("rule_type") != "buy_back" for r in rules or []):
        return []
    st = ((state or {}).get("history") or {}).get("price_refresh") or {}
    now = now or datetime.now(timezone.utc)

    def _ts(v):
        try:
            d = datetime.fromisoformat(str(v))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    ok_at = _ts(st.get("last_success"))
    stale = ok_at is None or (now - ok_at).total_seconds() > RULE_CHECK_STALE_MIN * 60
    failed = st.get("last_ok") is False
    if not (failed or stale):
        return []
    since = (f"since {ok_at.strftime('%d %b %H:%M')} UTC" if ok_at else "since the app last restarted")
    why = (f"The last check failed: {st.get('last_error')}" if failed and st.get("last_error")
           else "No check has completed in that time.")
    return [{
        "id": _rid("rule_check", "failed" if failed else "stale", ok_at.isoformat() if ok_at else "none"),
        "dimension": "rule", "severity": "HIGH",
        "title": f"Your trading rules have not been checked {since}",
        "action": (f"Stops, bands and take-profits are checked every 30 minutes. {why} Until a check "
                   f"succeeds, a rule that should fire will not -- check your stops by hand."),
        "how": ["The app retries every 30 minutes; this goes away when a check succeeds",
                "Nothing is bought or sold by this notice"],
        "est_amount": None, "apply": {"kind": "none"},
        "meta": {"headsup_kind": "rule_check", "failed": failed,
                 "last_success": st.get("last_success"), "last_error": st.get("last_error")}}]


JOB_STALE_HOURS = 26           # a daily job that has not succeeded in a day and a bit


def _job_health_headsup(state: dict, now=None) -> list[dict]:
    """PR 144 -- a build or a job that is failing, said where you look.

    Railway deploys every push whether or not anything checked it, and a failing
    job used to be one line on `/push/status`: `nav_snapshot` failed 7 nights out
    of 7 that way. One HIGH heads-up names every job that is failing now, has not
    succeeded in `JOB_STALE_HOURS` (or, never having run, was registered that long
    ago), or a `boot_check` that found an import a job cannot make. `price_refresh`
    is left to `_rule_check_headsup`, which says what its failure means.
    """
    from datetime import datetime, timezone
    if not (state or {}).get("scheduler_running"):
        return []
    now = now or datetime.now(timezone.utc)
    hist = (state or {}).get("history") or {}

    def _ts(v):
        try:
            d = datetime.fromisoformat(str(v))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    bad: list[tuple[str, str]] = []
    for jid, st in sorted(hist.items()):
        if jid == "price_refresh":
            continue
        if st.get("last_ok") is False:
            bad.append((jid, f"failing: {st.get('last_error') or 'no reason recorded'}"))
            continue
        if jid == "boot_check":
            continue
        ok_at = _ts(st.get("last_success")) or _ts(st.get("registered_at"))
        if ok_at and (now - ok_at).total_seconds() > JOB_STALE_HOURS * 3600:
            since = "never succeeded" if not st.get("last_success") else "last succeeded"
            bad.append((jid, f"{since} since {ok_at.strftime('%d %b %H:%M')} UTC"))
    if not bad:
        return []
    names = [j for j, _ in bad]
    boot = "boot_check" in names
    title = ("This build cannot run some of its own jobs" if boot else
             f"{len(bad)} background job{'s are' if len(bad) > 1 else ' is'} not working")
    return [{
        "id": _rid("job_health", *names), "dimension": "system", "severity": "HIGH",
        "title": title,
        "action": ("The app's scheduled work (snapshots, signals, solves, digests) is not running as it "
                   "should: " + "; ".join(f"{j} {why}" for j, why in bad)[:600]
                   + ". Cards that depend on it may be out of date."),
        "how": [*(f"{j}: {why}"[:200] for j, why in bad),
                "This clears itself when the jobs succeed; nothing is bought or sold by it"],
        "est_amount": None, "apply": {"kind": "none"},
        "meta": {"headsup_kind": "job_health", "jobs": names}}]


def _off_stop_and_big_move_recs(rows, snap, rules: list[dict], closes_by_ticker: dict) -> list[dict]:
    """2026-10-22 -- "why was there no card while my portfolio dropped?"

    Read live: SOXL fell 20% in a session and the app said nothing, because
    (a) its stop-loss was switched OFF while the price sat below it -- an
    inactive rule never raised a card -- and (b) no card looked at a single
    day's move at all.

    Facts only; nothing here says sell or keep.
      * a stop-loss / trailing stop that is OFF and whose condition is TRUE now
        -> one card whose Accept switches it back on (`edit_rule`, which re-arms);
      * a holding that moved `_BIG_DAY_MOVE_PCT`% or more in a day -> a heads-up
        (`apply: none`, so it is split into Heads-up, never an Accept that does
        nothing).
    """
    out: list[dict] = []
    nav = float(snap.get("nav") or 0.0)
    weights = snap.get("exposure_ticker") or {}
    seen: set[str] = set()
    for r in rules or []:
        if r.get("active") or r.get("rule_type") not in ("stop_loss", "trailing_stop"):
            continue
        if not r.get("breached_now") or not r.get("ticker") or not r.get("id"):
            continue
        tk = str(r["ticker"]).upper()
        if tk in seen:
            continue
        seen.add(tk)
        price = r.get("current_price")
        target = r.get("target") if r.get("target") is not None else r.get("level")
        try:
            price_s = f"{float(price):,.2f}"
            target_s = f"{float(target):,.2f}"
        except (TypeError, ValueError):
            continue
        value = round(float(weights.get(tk, 0.0)) * nav, 2)
        out.append({
            "id": _rid("stop_off", tk), "dimension": "holding", "severity": "HIGH",
            "title": f"Your {tk} stop is switched off and the price is below it",
            "action": (f"You set a stop on {tk} at {target_s}. The price is {price_s}, so it "
                       f"would have fired -- but the rule is OFF, so the app has stayed silent. "
                       f"Switching it on makes it act on its next check: it will raise its "
                       f"sell card for {tk} (about {_ils(value)}). Nothing is sold by switching it on."),
            "how": [f"Accept switches the {tk} stop back on",
                    "Ignore leaves it off -- the app will not warn you about this price again until it changes"],
            "est_amount": value,
            "apply": {"kind": "edit_rule", "target_rule_id": str(r["id"])},
            "meta": {"ticker": tk, "rule_id": str(r["id"]), "price": float(price),
                     "stop_level": float(target), "rule_active": False}})
    out += _protection_off_headsup(rules, carded={str(c["meta"]["rule_id"]) for c in out})
    for tk, cl in sorted((closes_by_ticker or {}).items()):
        held = [p for p in rows if (p.ticker or "").upper() == tk]
        if not held or len(cl) < 2:
            continue
        price = float(held[0].current_price or 0.0)
        if price <= 0 or not cl[-1] or not cl[-2]:
            continue
        move = (price / float(cl[-1]) - 1.0) * 100.0
        if abs(move) < 0.5:
            move = (float(cl[-1]) / float(cl[-2]) - 1.0) * 100.0
        if abs(move) < float(_setting("big_day_move_pct", _BIG_DAY_MOVE_PCT)):
            continue
        w = float(weights.get(held[0].ticker, 0.0)) * 100.0
        up = move > 0
        out.append({
            "id": _rid("big_day", tk), "dimension": "holding", "severity": "MEDIUM",
            "title": f"{tk} {'rose' if up else 'fell'} {abs(move):.0f}% in a day",
            "action": (f"{tk} is {w:.1f}% of your book and moved {move:+.1f}% against its last close. "
                       "This is a heads-up with no action attached: the app is not telling you to "
                       "sell or to keep it."),
            "how": ["Check whether your stop and plan still say what you want for it"],
            "est_amount": round(abs(move), 1), "apply": {"kind": "none"},
            "meta": {"ticker": tk, "day_move_pct": round(move, 2), "weight_pct": round(w, 2)}})
    return out


def _profit_recs(rows, snap, rules: list[dict], targets: dict | None = None) -> list[dict]:
    """Cards for taking money off the table -- the ones Eran had none of.

    2026-09-14: *"I'm holding assets that are moving which is great but I don't
    see any card recommendation about when to sell to trim profits."* He was
    right, and the reason was structural rather than a bug:

      * the only ACTIONABLE trim in the app was a concentration-cap breach;
      * `Trim: <ticker>` was a FUNDAMENTALS verdict (score < 40) that mentioned
        a gain only as a clause in its sentence, and shipped `apply: none`;
      * his take-profit rules existed but sat far away -- SOXL's is at +80%, so
        nothing was ever going to fire.

    So the app was silent on a book whose largest holding was up and sitting
    0.2 points under its own cap. Three cards, in the order a decision actually
    arrives:

      1. one of YOUR OWN rule levels is close;
      2. a holding has run far enough to be worth a look regardless;
      3. a winner is growing INTO its cap, before it breaches.

    Every card carries `est_amount`. That is not decoration: `moved_on` can only
    un-mute a card whose number has moved, so a card without one stays muted for
    the full TTL however far the position runs -- which is exactly what happened
    to `Trim: SOXL` after it was marked done on 2026-09-08.
    """
    out: list[dict] = []
    nav = float(snap.get("nav") or 0.0)
    if not nav:
        return out
    weights = snap.get("exposure_ticker") or {}
    by_ticker: dict[str, list[dict]] = {}
    for r in rules or []:
        if r.get("active") and r.get("ticker"):
            by_ticker.setdefault(str(r["ticker"]).upper(), []).append(r)

    from app.services.intake_service import is_cash_position

    for p in rows:
        tk = (p.ticker or "").upper()
        if is_cash_position(tk, p.meta if isinstance(p.meta, dict) else None):
            continue
        price = float(p.current_price or 0.0)
        cost = float(p.cost_basis or 0.0)
        qty = float(p.quantity or 0.0)
        if price <= 0 or qty <= 0:
            continue
        w_pct = float(weights.get(p.ticker, 0.0)) * 100.0
        value_ils = (weights.get(p.ticker, 0.0)) * nav
        gain_pct = ((price / cost) - 1.0) * 100.0 if cost > 0 else 0.0
        mine = by_ticker.get(tk, [])

        # 1) ONE OF YOUR OWN LEVELS IS CLOSE.
        for r in mine:
            rt = r.get("rule_type")
            if rt not in ("take_profit", "trailing_stop"):
                continue
            target = r.get("target")
            try:
                target = float(target)
            except (TypeError, ValueError):
                continue
            if target <= 0:
                continue
            if rt == "take_profit":
                # Approaching a level from BELOW: distance as a share of price
                # is the right measure, and it shrinks as the position runs.
                gap = (target - price) / price
                if not (0 < gap <= float(_setting("near_rule_pct", _NEAR_RULE_FRAC * 100.0)) / 100.0):
                    continue
                near_pct = gap * 100.0
            else:
                # A trailing stop. Measure the fraction of the TRAIL given
                # back, never the distance -- see `_TRAIL_GIVEBACK`.
                try:
                    trail = float(r.get("level") or 0.0) / 100.0
                except (TypeError, ValueError):
                    continue
                if not (0.0 < trail < 1.0):
                    continue
                peak = target / (1.0 - trail)
                span = peak - target
                if span <= 0:
                    continue
                given = (peak - price) / span
                if given < float(_setting("near_trail_giveback_pct", _TRAIL_GIVEBACK * 100.0)) / 100.0:
                    continue
                near_pct = given * 100.0
            # The slice that rule would actually sell, valued through the same
            # arithmetic Accept would run. A card that names a number Accept
            # would not produce is the claim invariant broken again.
            frac = 1.0 if rt == "trailing_stop" else _take_profit_frac(mine, r)
            amount = round(value_ils * frac, 2)
            if rt == "take_profit":
                title = f"{tk} is closing on your take-profit"
                action = (f"{tk} is {_pct(gap * 100)} below your {target:,.2f} "
                          f"take-profit. When it gets there the app will offer to "
                          f"sell {frac:.0%} of the position -- about {_ils(amount)} "
                          f"-- and leave the rest running.")
            else:
                title = f"{tk} has given back most of its trailing stop"
                action = (f"{tk} has fallen {_pct(near_pct)} of the way from its "
                          f"peak to your trailing stop at {target:,.2f}. If it gets "
                          f"there the whole position exits -- about {_ils(amount)}.")
            out.append({
                "id": _rid("near_rule", tk, rt), "dimension": "holding",
                "severity": "MEDIUM", "title": title, "action": action,
                "how": [f"Decide now what you want to do at {target:,.2f}, "
                        f"rather than on the day",
                        "Or move the level if it no longer reflects your plan",
                        "Nothing happens automatically -- the rule raises a card"],
                "est_amount": amount, "apply": {"kind": "none"},
                "meta": {"ticker": tk, "rule_type": rt, "target": target,
                         "gap_pct": round(near_pct, 2),
                         # PR 70: distance to the level as a share of price, the
                         # one measure the priority score reads for every level.
                         "distance_pct": round(((target - price) if rt == "take_profit"
                                                else (price - target)) / price * 100.0, 2)}})

        # 2) IT HAS SIMPLY RUN A LONG WAY.
        # PR 100: a gain is not a reason to trim. Under (or at) its plan target
        # the card states the gain and what covers it -- one line, no trim
        # suggestion: SOXL at 33% of a 40% target was told trimming "moves your
        # portfolio closer to your plan" while its buy-back rule said the plan
        # wanted 1,673 MORE. Over its target it says how much the plan wants
        # trimmed, sized on the excess.
        if gain_pct >= float(_thr.get("gain_line_pct", _GAIN_CARD_PCT)) and cost > 0:
            from app.services.funding_service import MIN_TRADE_ILS as _min_trade
            gain_ils = round(value_ils * (1.0 - cost / price), 2)
            tps = sorted(float(r["target"]) for r in mine
                         if r.get("rule_type") == "take_profit" and _float_or_none(r.get("target")))
            tgt = (targets or {}).get(tk)
            t_pct = float(tgt) * 100.0 if tgt is not None else None
            over_ils = round((w_pct - t_pct) / 100.0 * nav, 2) if t_pct is not None else 0.0
            if t_pct is not None and over_ils >= _min_trade:
                out.append({
                    "id": _rid("ran_up", tk), "dimension": "holding", "severity": "MEDIUM",
                    "title": f"Trim {tk} to target: {w_pct:.0f}% of your book vs {t_pct:.0f}%",
                    "action": (f"{tk} is +{gain_pct:.0f}% since you bought ({_ils(gain_ils)} of gain). "
                               f"Trimming to its {t_pct:.0f}% target sells about {_ils(over_ils)}."),
                    "how": [f"Sell about {_ils(over_ils)} of {tk}", "Mind the tax on any realised gain"],
                    # PR 102: Accept sells the excess, every row of the ticker.
                    "est_amount": over_ils,
                    "apply": {"kind": "trim", "ticker": p.ticker,
                              "shares": round(qty * over_ils / value_ils, 6) if value_ils > 0 else 0.0},
                    "meta": {"ticker": tk, "gain_pct": round(gain_pct, 2), "weight_pct": round(w_pct, 2),
                             "target_pct": round(t_pct, 2), "over_target_ils": over_ils}})
            else:
                cover = (f"Your take-profit at {tps[0]:,.2f} covers it." if tps else
                         "You have no take-profit on it.")
                out.append({
                    "id": _rid("ran_up", tk), "dimension": "holding", "severity": "LOW",
                    "title": f"{tk} +{gain_pct:.0f}% since you bought: {_ils(gain_ils)} of gain",
                    "action": cover,
                    "how": ([f"Your take-profit raises a card at {tps[0]:,.2f}"] if tps else
                            [f"Set a take-profit on {tk} if you want the app to ask you at a level you choose"]),
                    "est_amount": gain_ils, "apply": {"kind": "none"},
                    "meta": {"ticker": tk, "gain_pct": round(gain_pct, 2),
                             "target_pct": round(t_pct, 2) if t_pct is not None else None}})

        # 3) IT IS GROWING INTO ITS CAP.
        for r in mine:
            if r.get("rule_type") != "max_weight":
                continue
            try:
                cap_pct = float(r.get("level"))
            except (TypeError, ValueError):
                continue
            headroom = cap_pct - w_pct
            if not (0 < headroom <= float(_setting("near_cap_pts", _NEAR_CAP_PCT_POINTS))):
                continue
            over_at_cap = round(nav * (w_pct - cap_pct) / 100.0, 2)
            out.append({
                "id": _rid("near_cap", tk), "dimension": "diversification",
                "severity": "MEDIUM",
                "title": f"{tk} is nearly at your {cap_pct:.0f}% cap",
                "action": (f"{tk} is {w_pct:.1f}% of your book against the "
                           f"{cap_pct:.0f}% cap you set -- {headroom:.1f} points "
                           f"of headroom. It is getting there by going up, so the "
                           f"breach will arrive on a good day, not a bad one."),
                "how": [f"Trim {tk} back below the cap on your own terms",
                        "Or raise the cap, if you have changed your mind about "
                        "how much of this you want to own",
                        "Waiting is fine -- this is a heads-up, not a breach"],
                # THE HEADROOM IN SHEKELS, not the trim. The trim is zero by
                # construction here -- the position is not over the cap yet --
                # and a card whose amount is always 0.0 can never un-mute,
                # because `moved_on` fires on the amount MOVING. Shipping 0.0
                # reproduced the exact defect this card was written to fix, and
                # the test asserting "est_amount is not None" passed on it: a
                # check that could not fail for the thing it was checking.
                #
                # The headroom moves every time the position does, which is
                # what makes the mute expire when the situation changes.
                "est_amount": round(max(0.0, nav * headroom / 100.0), 2),
                "meta_trim_at_cap_ils": round(max(0.0, over_at_cap), 2),
                "apply": {"kind": "none"},
                **({"headsup_act": raise_cap} if (raise_cap := _raise_cap_act(r, tk, cap_pct, targets))
                   else {}),
                "meta": {"ticker": tk, "weight_pct": round(w_pct, 2),
                         "cap_pct": cap_pct, "headroom_pct": round(headroom, 2)}})
    return out


def _take_profit_frac(mine: list[dict], rule: dict) -> float:
    """The slice this take-profit would sell, read through `rules_service`.

    Imported rather than re-derived: two implementations of "how much does a
    take-profit sell" is two numbers that can disagree between the card and
    Accept, which is the claim invariant this repo already paid for once.
    """
    from app.services.rules_service import TAKE_PROFIT_FRACTION

    raw = rule.get("trim_fraction")
    try:
        frac = float(raw) if raw is not None else TAKE_PROFIT_FRACTION
    except (TypeError, ValueError):
        frac = TAKE_PROFIT_FRACTION
    return frac if 0.0 < frac <= 1.0 else TAKE_PROFIT_FRACTION


def _pct(x: float) -> str:
    return f"{abs(x):.1f}%"


def _hedge_recs(rows, snap) -> list[dict]:
    """Flag sector/factor concentration and suggest a diversifier or hedge."""
    from app.services.fx import position_value_ils
    out: list[dict] = []
    nav = snap["nav"]
    if not nav or len(rows) < 2:
        return out
    sector_w: dict[str, float] = {}
    from app.services.intake_service import is_cash_position
    for p in rows:
        # Cash has no sector. Asking for the fundamentals of "CASH" returns
        # Pathward Financial's, which would book the cash balance as Financials.
        _m = getattr(p, "meta", None)
        if is_cash_position(p.ticker, _m if isinstance(_m, dict) else None):
            continue
        f = _fundamentals(p.ticker)
        sector = (f.sector if f and f.sector else "Unknown")
        val = position_value_ils(p)   # PR 105: was qty * native price, read as ILS
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


def _holding_review_recs(rows, snap, closes_by_ticker: dict, measured: dict,
                         sleeve_notes: dict, cash_ils: float,
                         trigger_ils: float | None, *,
                         armed_stops: set | None = None) -> tuple[list[dict], list[dict]]:
    """PR 77 -- (review rows, cards). Pure: every input is already fetched.

    One row per non-cash holding with every number that produced its state; a
    card only for Trim/Exit on a holding no sleeve owns. ACTIONABLE only when the
    stored measurement is fresh and the signal beat holding (`holding_review`);
    otherwise guidance that says why, with the numbers.
    """
    from app.services import holding_review as hr
    from app.services.intake_service import is_cash_position

    by_tk: dict[str, list] = {}
    for p in rows:
        if is_cash_position(p.ticker, p.meta if isinstance(p.meta, dict) else None):
            continue
        by_tk.setdefault(p.ticker.upper(), []).append(p)
    review, cards = [], []
    frac = float(hr.TRIM_FRACTION["value"])
    for tk, held in sorted(by_tk.items()):
        m = hr.metrics(closes_by_ticker.get(tk) or [])
        state = hr.state_of(m)
        meas = measured.get(tk)
        ok_fresh, stale_why = hr.fresh(meas)
        row = {"ticker": tk, "state": state, "metrics": m,
               "sleeve": sleeve_notes.get(tk), "measurement": meas,
               "measurement_usable": ok_fresh, "measurement_note": stale_why}
        review.append(row)
        if tk in sleeve_notes:
            row["note"] = sleeve_notes[tk]
            continue                      # the sleeve's own rule decides
        if state not in ("Trim", "Exit"):
            continue
        sig = "long" if state == "Exit" else "short"
        s = hr.SIGNALS[sig]
        j = (meas or {}).get(sig) or {}
        shares_total = sum(float(p.quantity or 0) for p in held)
        fraction = 1.0 if state == "Exit" else frac
        shares = round(shares_total * fraction, 6)
        _pl = _plan_sale(held, shares)          # the lots Accept will take
        gross, tax = _pl["gross"], _pl["tax"]
        net = gross - tax
        def _pc(v):
            return "n/a" if v is None else f"{v}%"
        m_line = (f"Short term: 20-day {m['sma20']:,.2f} vs 50-day {m['sma50']:,.2f}, "
                  f"1 month {_pc(m['ret_1m_pct'])}. Long term: 50-day {m['sma50']:,.2f} vs 200-day "
                  f"{m['sma200'] if m['sma200'] is not None else 'n/a'}, 6 months {_pc(m['ret_6m_pct'])}, "
                  f"12 months {_pc(m['ret_12m_pct'])}, {_pc(m['drawdown_52w_pct'])} from its 52-week high.")
        hist_words = ("price history, dividends not included" if (meas or {}).get("return_basis") == "price_only"
                      else "history with dividends")
        bt_line = (f"On {tk}'s own {hist_words} ({j.get('start')} to {j.get('end')}), selling when "
                   f"{s['words']} returned {j.get('rule_cagr_pct')}%/yr after costs and 25% CGT "
                   f"vs {j.get('hold_cagr_pct')}%/yr holding; worst drop {j.get('rule_max_drawdown_pct')}% "
                   f"vs {j.get('hold_max_drawdown_pct')}%. (The first 200 days count as cash for the "
                   f"rule, which favours holding.)") if j.get("measured") else None
        actionable = bool(ok_fresh and j.get("edge"))
        cash_after = cash_ils + net
        lands = (f"{_ils(net)} lands in cash (about {_ils(cash_after)} after). "
                 + (f"Above your {_ils(trigger_ils)} trigger, the cash card researches where it goes."
                    if trigger_ils is not None and cash_after > trigger_ils else
                    "It stays in cash."))
        what = ("all" if state == "Exit" else f"{fraction:.0%}")
        horizon = "both horizons" if state == "Exit" else "the short horizon"
        if actionable:
            cards.append({
                "id": _rid("review", tk, state), "dimension": "holding_review",
                "severity": "HIGH",
                "title": f"Sell {what} of {tk} — trend broke on {horizon}",
                "why": f"{m_line} {bt_line}",
                "action": (f"Sell {shares:g} {tk} (~{_ils(gross)}, estimated CGT {_ils(tax)}). "
                           f"{lands}"),
                "impact": (f"Measured on {tk}'s own ~{j.get('years')} years: this signal "
                           f"{j.get('why')}."
                           # A trim keeps the row, so the hold can be priced and
                           # released; an exit leaves nothing to price, and a
                           # hold that never releases would block the name for
                           # good (found in review). The cash card is the reader.
                           + (f" The cash card will not top {tk} back up while it trades at "
                              f"or above today's price." if state == "Trim" else "")),
                "how": [f"Accept sells {shares:g} {tk} in your InvestWise book at the live price "
                        f"and credits the net proceeds to cash",
                        "No brokerage order is placed — mirror the sale at your broker",
                        f"Signal: {s['words']} ({s['id']}@{s['version']}); "
                        + (f"the trim size is {hr.TRIM_FRACTION['id']}@{hr.TRIM_FRACTION['version']} "
                           f"(not owner-reviewed); the backtest measured a full exit"
                           if state == "Trim" else "exit = the whole position"),
                        "Not financial advice."],
                "est_amount": round(gross, 2),
                "actionable": True,
                "apply": ({"kind": "trim", "ticker": held[0].ticker, "shares": shares,
                           "hold_after_sale": True} if state == "Trim" else
                          {"kind": "sell_position", "ticker": held[0].ticker, "shares": shares}),
                "meta": {"ticker": tk, "state": state, "measurement": j,
                         "assumptions": (meas or {}).get("assumptions")}})
        elif m.get("ok") and tk not in (armed_stops or set()):
            # PR 104b: never offer a stop on a holding that already has one armed
            # (production 2026-10-07: BND's stop at 64.46 was armed and the card
            # offered a second at 64.42).
            # Eran, 2026-09-28: "the card should be actionable". Without a
            # measured edge the card may not SELL (Q5), so its action is the
            # protection a broken trend calls for: a stop-loss scaled to the
            # holding's own volatility -- the card this one replaces
            # (`_momentum_recs`) offered the same kind of rule.
            closes = closes_by_ticker.get(tk) or []
            buf = stop_buffer_pct(closes, k=1.5, lo=8.0, hi=15.0)
            last = float(m["price"])
            stop = round(last * (1 - buf / 100.0), 2)
            if not ok_fresh:
                not_sell = (f"The measurement is not usable ({stale_why}), so without a fresh "
                            f"measured edge this cannot be a sell card.")
            elif not j.get("measured"):
                not_sell = (f"Selling {tk} on this signal could not be measured "
                            f"({j.get('why') or 'no backtest'}), so this is not a sell.")
            else:
                not_sell = (f"Historically, selling {tk} on this signal {j.get('why')}, so this "
                            f"is not a sell.")
            cards.append({
                "id": _rid("review_g", tk, state), "dimension": "holding_review",
                "severity": "MEDIUM",
                "title": f"{tk}'s trend turned down on {horizon} — protect it with a stop",
                "summary": (f"{tk}'s moving averages point down on {horizon}"
                            + (f" and it is {abs(float(m['drawdown_52w_pct'])):.1f}% below its 52-week high."
                               if m.get("drawdown_52w_pct") is not None else ".")),
                "why": m_line + (f" {bt_line}" if bt_line else ""),
                "action": (f"{not_sell} Arm a stop-loss at {stop:,.2f} (~{buf:.0f}% below "
                           f"today's {last:,.2f}) instead."),
                "impact": (f"Accept arms a stop-loss on {tk} at {stop:,.2f}. Nothing is sold now; "
                           f"if {tk} falls to there you get an alert and a Today to-do to sell."),
                "how": [f"Accept arms a stop-loss at {stop:,.2f} (~{buf:.0f}% below today; "
                        f"1.5x the holding's 10-day volatility, clamped 8-15%)",
                        "Nothing is bought or sold, and no brokerage order is placed",
                        "Adjust or remove it any time under Trading rules",
                        "Not financial advice."],
                "est_amount": None, "actionable": True,
                "apply": {"kind": "create_rules", "rules": [{
                    "ticker": held[0].ticker, "rule_type": "stop_loss", "mode": "price",
                    "level": stop,
                    "note": f"From holding review ({state}; ~{buf:.0f}% below {last:.2f})"}]},
                "meta": {"ticker": tk, "state": state, "measurement": j or None,
                         "stop": stop, "buffer_pct": buf}})
    return review, cards


def _momentum_recs(rows, snap, sleeve_floor: dict | None = None) -> list[dict]:
    """Surface holdings in a strong down- or up-trend and turn each into a
    concrete, one-click discipline rule (stop-loss / trailing stop). Accept arms
    the rule — the app never trades; a hit raises an alert + a Today to-do."""
    out: list[dict] = []
    weights = (snap or {}).get("exposure_ticker") or {}
    from app.services.intake_service import is_cash_position
    for p in rows:
        # A cash balance has no trend. Without this the loop fetched the history
        # of "CASH" -- a real NASDAQ ticker, Pathward Financial -- and on
        # 2026-09-17 production showed "CASH is in a downtrend" with a stop-loss
        # offered on the user's shekels.
        _m = getattr(p, "meta", None)
        if is_cash_position(p.ticker, _m if isinstance(_m, dict) else None):
            continue
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


def _redeploy_name_targets(held_by_class: dict, weights: dict, target: dict | None,
                           sleeve_targets: dict | None) -> dict[str, float]:
    """{ticker: target share of the book} for every held name, or {} without sleeves.

    A sleeve name's target is the sleeve's. The core names in a class share what
    the class target has left once its sleeve names are subtracted, split in
    proportion to their current value -- the core's own composition is the
    user's, and this card is not the place to change it.
    """
    if not sleeve_targets:
        return {}
    st = {str(k).upper(): float(v or 0.0) for k, v in sleeve_targets.items()}
    out: dict[str, float] = {}
    for cls, held in held_by_class.items():
        tw = float((target or {}).get(cls) or 0.0)
        sleeve_names = [p for p in held if p.ticker.upper() in st]
        core_names = [p for p in held if p.ticker.upper() not in st]
        for p in sleeve_names:
            out[p.ticker] = st[p.ticker.upper()]
        core_share = max(0.0, tw - sum(st[p.ticker.upper()] for p in sleeve_names))
        tot = sum(weights.get(p.ticker, 0.0) for p in core_names)
        for p in core_names:
            share = (weights.get(p.ticker, 0.0) / tot) if tot > 0 else 1.0 / len(core_names)
            out[p.ticker] = core_share * share
    return out


def _plan_weight_pass(legs, remaining, name_tgt, held_by_class, weights, nav, cap,
                      name_caps, name_gap, name_reason) -> None:
    """Spend what the class pass left: first every name still below its target,
    then the rest at plan weights. Mutates ``legs``.

    The class pass cannot see a sleeve that is short inside a class that is on
    target in aggregate -- SOXL under while QQQ is over nets to "Equities fine".
    """
    from app.services import funding_service as _fund

    cls_of = {p.ticker: cls for cls, held in held_by_class.items() for p in held}
    placed = {x["ticker"]: x for x in legs}

    def _room(tk):
        already = placed[tk]["amount_ils"] if tk in placed else 0.0
        return _fund.name_room_ils(
            nav, weights.get(tk, 0.0), _fund.effective_name_cap(cap, tk, name_caps)) - already

    def _add(tk, amt, reason):
        nonlocal remaining
        if tk in placed:
            placed[tk]["amount_ils"] = round(placed[tk]["amount_ils"] + amt, 2)
        else:
            placed[tk] = {"ticker": tk, "amount_ils": round(amt, 2),
                          "asset_class": cls_of.get(tk, ""), "reason": reason}
            legs.append(placed[tk])
        remaining = round(remaining - amt, 2)

    # (1) Still-short names, largest shortfall first.
    for tk in sorted(name_tgt, key=lambda t: -name_gap(t)):
        already = placed[tk]["amount_ils"] if tk in placed else 0.0
        g = name_gap(tk) - already
        amt = round(min(g, _room(tk), remaining), 2)
        if remaining < _fund.MIN_TRADE_ILS:
            break
        if amt >= _fund.MIN_TRADE_ILS or (tk in placed and amt > 0):
            _add(tk, amt, name_reason(tk, cls_of.get(tk, "")))

    # (2) Everything at target: the rest goes in at plan weights, which keeps
    # every class where the plan wants it while cash comes down to its target.
    if remaining >= _fund.MIN_TRADE_ILS:
        budget = remaining
        # Drop the names whose share would be under one trade and re-spread
        # their share over the rest, until every leg clears the minimum --
        # otherwise the dropped shares silently stay in cash.
        pool = {tk: t for tk, t in name_tgt.items() if t > 0 and _room(tk) > 0}
        pro: dict[str, float] = {}
        while pool:
            tot = sum(pool.values())
            pro = {tk: round(min(budget * t / tot, max(0.0, _room(tk))), 2)
                   for tk, t in pool.items()}
            small = [tk for tk, a in pro.items()
                     if a < _fund.MIN_TRADE_ILS and tk not in placed]
            if not small:
                break
            for tk in small:
                pool.pop(tk)
        ok = {tk: a for tk, a in pro.items() if tk in pool}
        if not ok and name_tgt:
            # Too thin to split: one trade into the largest target with room.
            tk = max((t for t in name_tgt if _room(t) >= _fund.MIN_TRADE_ILS),
                     key=lambda t: name_tgt[t], default=None)
            if tk:
                ok = {tk: round(min(budget, _room(tk)), 2)}
        for tk, a in ok.items():
            if a <= 0:
                continue
            _add(tk, a, f"at plan weight ({name_tgt[tk]:.1%} of the book) -- "
                        f"every holding is at its target, so new money keeps the mix on plan")


IDEAS_SHOWN = 5          # Eran, 2026-09-29: "Top 5"


def _touch_probability(now: float | None, level: float, vol_pct: float | None,
                       days: int = 21) -> float | None:
    """Chance the price touches `level` from `now` within `days` trading days,
    driftless lognormal (reflection principle): 2 x (1 - N(z)). None when the
    inputs are missing -- the caller then says it does not know."""
    import math
    if not now or not level or level <= 0 or not vol_pct:
        return None
    if now <= level:
        return 1.0
    sig = float(vol_pct) / 100.0 * math.sqrt(days / 252.0)
    if sig <= 0:
        return 0.0
    z = math.log(float(now) / float(level)) / sig
    return max(0.0, min(1.0, 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))))


def _held_cash_card(holds: dict, reserved_ils: float, cash_ils: float,
                    trigger_ils: float, *, nav: float = 0.0, mix: dict | None = None,
                    target: dict | None = None, under_legs: list | None = None,
                    ideas: list | None = None) -> dict:
    """PR 83 -- cash above the trigger that a buy-back hold is keeping back.

    Three answers (Eran, 2026-09-29): keep waiting; buy it back now; invest SOME
    of it elsewhere and keep the rest -- with the places, the reasons, and a
    suggested split that is argued, not 50/50.
    """
    from app.services import funding_service as _fund
    mix, target = mix or {}, target or {}
    # PR 141: `holds` includes the 2x/3x funds a stop on one of them blocks
    # (`via`). They are not holds of their own -- nothing was sold, and "Buy
    # back" must not buy them -- but they are not a place to put the money either.
    tks = sorted(t for t, h in holds.items() if not (h or {}).get("via"))
    _via = sorted(t for t, h in holds.items() if (h or {}).get("via"))
    each = round(reserved_ils / len(tks), 2)

    def _lvl(tk):
        return float((holds.get(tk) or {}).get("level") or 0.0)

    def _now(tk):
        return (holds.get(tk) or {}).get("price_now")
    names = ", ".join(tks)
    def _wait(tk):
        h = holds.get(tk) or {}
        # PR 139.1: a STOP hold first. A stop sells the whole position, so the
        # ticker has no price row (`price_now` is None) and fell through to the
        # guard sentence: production 10-23 said SOXL waited "until your drawdown
        # guard re-arms" when it waits for its sleeve's re-entry.
        if h.get("kind") == "stop":
            what = str(h.get("exit_rule_type") or "stop").replace("_", " ")
            return (f"{tk} until its sleeve's signal ({h['strategy_id']}) is out and back in "
                    f"(your {what} sold it at {_lvl(tk):,.2f})" if h.get("strategy_id") else
                    f"{tk} until you end the hold (your {what} sold it at {_lvl(tk):,.2f})")
        if _lvl(tk) > 0 and _now(tk) is not None:
            return f"{tk} until it trades below {_lvl(tk):,.2f} (now {float(_now(tk)):,.2f})"
        return f"{tk} until your drawdown guard re-arms"
    waits = "; ".join(_wait(tk) for tk in tks)
    if _via:
        _src = sorted({str(holds[t].get("via")) for t in _via})
        waits += f"; and, while the {', '.join(_src)} hold lasts, any other 2x/3x fund"

    # ---- where else, and why -------------------------------------------------
    held_set = {t.upper() for t in holds}      # PR 141: blocked funds are not a destination
    dests: list[dict] = []
    for leg in (under_legs or []):
        tk = str(leg.get("ticker") or "").upper()
        if tk and tk not in held_set:
            cls = leg.get("asset_class") or classify(tk, None)
            dests.append({"ticker": tk, "name": tk, "kind": "plan", "asset_class": cls,
                          "why": [leg.get("reason") or f"{cls} is under its target"],
                          "max_ils": float(leg.get("amount_ils") or 0.0)})
    seen = {d["ticker"] for d in dests} | held_set
    for idea in sorted((ideas or []), key=lambda x: -float(x.get("score") or 0)):
        tk = str(idea.get("ticker") or "").upper()
        if not tk or tk in seen:
            continue
        cls = classify(tk, None)
        dests.append({"ticker": tk, "name": idea.get("name") or tk, "kind": "idea",
                      "asset_class": cls, "score": idea.get("score"),
                      "why": (list(idea.get("reasons") or [])[:3]
                              or [f"screener score {idea.get('score')}"]),
                      "flags": list(idea.get("flags") or [])})
        seen.add(tk)
        if sum(1 for d in dests if d["kind"] == "idea") >= int(_setting("ideas_shown", IDEAS_SHOWN)):
            break
    for d in dests:
        cls = d["asset_class"]
        d["class_now_pct"] = round(float(mix.get(cls, 0.0)) * 100.0, 2)
        d["class_target_pct"] = round(float(target.get(cls, 0.0)) * 100.0, 2)
        # Say which it is: a leg the plan itself would buy is "under target"
        # only when its class IS under target (live: VTI was labelled under
        # target with Equities at 69.3% vs 68.7%).
        if d["kind"] == "plan":
            d["plan_label"] = ("under target" if d["class_now_pct"] < d["class_target_pct"]
                               else "keeps the plan's mix")

    # ---- the suggested split -------------------------------------------------
    # Eran, 2026-09-29 23:42 ("I asked you to suggest the amounts"): the chance
    # the held ticker touches its buy-back level decides which option the card
    # RECOMMENDS -- it must not zero the elsewhere amounts (PR 84 kept
    # reserved x 92% and left ~130, under the 250 minimum, so every box said 0).
    # His rule: plan legs first, up to their limits (they keep the mix on plan);
    # then HALF of what is left across the two best-scored ideas, by score; the
    # rest stays reserved.
    probs = [_touch_probability(_now(tk), _lvl(tk), (holds.get(tk) or {}).get("vol_20d_pct"))
             for tk in tks]
    known = [p for p in probs if p is not None]
    p_touch = (sum(known) / len(known)) if known else None
    _min = float(_fund.MIN_TRADE_ILS)
    left = float(reserved_ils)
    for d in dests:
        d["suggested_ils"] = 0.0
    for d in [x for x in dests if x["kind"] == "plan"]:
        amt = math.floor(min(left, d["max_ils"]) / 10.0) * 10.0
        if amt >= _min:
            d["suggested_ils"] = amt
            left = round(left - amt, 2)
    pool = left / 2.0
    top = [x for x in dests if x["kind"] == "idea"][:2]
    tot = sum(float(x.get("score") or 1) for x in top) or 1.0
    amts = [math.floor(pool * float(d.get("score") or 1) / tot / 10.0) * 10.0 for d in top]
    if top and any(a < _min for a in amts):
        # too little for two buys: the best idea takes the pool, if it can
        amts = [math.floor(pool / 10.0) * 10.0] + [0.0] * (len(top) - 1)
    for d, amt in zip(top, amts):
        if amt >= _min:
            d["suggested_ils"] = amt
    suggested = round(sum(d["suggested_ils"] for d in dests), 2)
    keep = round(reserved_ils - suggested, 2)
    _plan_tks = [d["ticker"] for d in dests if d["kind"] == "plan" and d["suggested_ils"] > 0]
    _idea_tks = [d["ticker"] for d in dests if d["kind"] == "idea" and d["suggested_ils"] > 0]
    _how = []
    if _plan_tks:
        _how.append(f"{', '.join(_plan_tks)} first (it keeps your mix on plan)")
    if _idea_tks:
        _how.append(f"half of the rest into {' and '.join(_idea_tks)}, the best-scored ideas")
    _split = (("Suggested split: " + ", then ".join(_how) + f"; {_ils(keep)} stays reserved "
               f"for {names}. Change any amount.") if suggested >= _min else
              "Nothing here reaches the minimum buy, so there is no split to suggest.")
    wait_pick = p_touch is not None and p_touch >= 0.5
    if p_touch is None:
        split_why = ("No volatility measurement for " + names + " yet, so the chance of "
                     "reaching the buy-back level is unknown. " + _split)
    else:
        split_why = (f"{names} would have to fall "
                     + ", ".join(f"{(float(_now(tk)) / _lvl(tk) - 1) * 100:.1f}%"
                                 for tk in tks if _lvl(tk) > 0 and _now(tk))
                     + f" to reach your buy-back level; on its own volatility that has about a "
                     f"{p_touch:.0%} chance within a month, so the app's pick is to "
                     + (("keep waiting. If you would rather put some of it to work, the "
                         + _split[0].lower() + _split[1:])
                        if wait_pick else ("put part of it to work. " + _split)))
    over = [d for d in dests if d["class_now_pct"] >= d["class_target_pct"]]
    honesty = (" Every class is already at or over its plan target (the reserve is the only "
               "gap), so anything invested elsewhere moves the mix above plan — each place "
               "shows by how much." if dests and len(over) == len(dests) else "")

    return {
        "id": _rid("held_cash", *tks), "dimension": "cash", "severity": "MEDIUM",
        "title": f"{_ils(reserved_ils)} of your cash is waiting for {names}",
        "why": (f"You hold {_ils(cash_ils)} in cash, above your {_ils(trigger_ils)} trigger. "
                f"{_ils(reserved_ils)} of it is reserved: when you sold, the app agreed not to "
                f"buy back {waits}. Holdings › Rules shows this as a buy-back rule you can edit."),
        "action": ("Keep waiting, buy it back now (ends the hold), or invest part of it "
                   "elsewhere and keep the rest reserved."),
        "impact": split_why + honesty,
        "how": ["Keep: the card goes quiet until the amount changes",
                f"Buy back: ends the hold and buys {names} from cash at the live price",
                f"Elsewhere: invests the amounts you set; the rest stays reserved and "
                f"{names} stays on hold",
                "No brokerage order is placed — mirror it at your broker"],
        "est_amount": round(reserved_ils, 2), "actionable": True, "decision": True,
        "apply": {"kind": "held_cash", "tickers": tks, "reserved_ils": round(reserved_ils, 2),
                  "recommended": ("keep" if wait_pick or suggested < _fund.MIN_TRADE_ILS
                                  else "redeploy"),
                  "nav_ils": round(float(nav or 0.0), 2),
                  "options": {"keep": {}, "buy": {"legs": [
                      {"ticker": tk, "amount_ils": each} for tk in tks]},
                      "redeploy": {"destinations": dests, "suggested_keep_ils": keep,
                                   "split_why": split_why,
                                   "touch_probability": p_touch,
                                   "min_leg_ils": _fund.MIN_TRADE_ILS}},
                  "holds": {tk: {"level": _lvl(tk), "price_now": _now(tk)} for tk in tks}},
        "meta": {"tickers": tks}}


def _redeploy_cash_recs(rows, snap, plan, objective, cap, cash_ils,
                        book_mix: dict | None = None,
                        name_caps: dict | None = None,
                        sleeve_targets: dict | None = None,
                        holds: dict | None = None,
                        reserved_ils: float = 0.0,
                        ideas: list | None = None,
                        _inner: bool = False, core_id: str | None = None) -> list[dict]:
    """Surplus cash -> one sized, executable redeployment plan.

    PR 75 -- `holds` are tickers under a take-profit buy-back hold and
    `reserved_ils` the planned value they are short of. That money stays in
    cash (it is the sleeve's, parked), and no leg -- including the plan-weight
    pass -- may buy a held ticker. Review, CONFIRMED before this: the pass gave
    SOXL its plan weight and the card bought the whole sale straight back.

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

    WITH SLEEVES, EACH NAME HAS A TARGET, AND THE CARD MUST USE IT.
    ``sleeve_targets`` is {ticker: share of the book} for every sleeve holding.
    Without it the class pass split a class's budget evenly across whatever you
    hold in it and gave the smallest name the most. Reproduced on the live book
    (2026-09-20) with ₪1,067/month arriving: month 3 put ₪623 into QQQ, 3pp OVER
    its 9% sleeve target, and ₪270 into SOXL, UNDER its 42% target. So when
    sleeve targets exist, each held name gets a target (a sleeve's own; a core
    name splits what is left of its class target in proportion to its current
    value), money goes only to names below their target, and whatever is left
    after every gap is filled is invested AT plan weights -- which moves every
    class toward target, because cash is the only thing over its target.
    Books without sleeves keep the behaviour they had.
    """
    from app.services import funding_service as _fund

    nav = snap.get("nav") or 0.0
    if not nav or cash_ils <= 0:
        return []
    # The book's currency decides whether US municipal bonds are a real option.
    _ccy = (getattr(plan, "currency", None) or "ILS")
    # THE TRIGGER. Eran, 2026-09-21: "the card should appear if I have more than
    # 1500, not by % of my portfolio" -- and then it invests down to the target
    # (his 150 floor). Below the trigger there is no card, however much of it
    # would be spendable.
    _trigger = getattr(plan, "cash_redeploy_at_ils", None) if plan is not None else None
    if _trigger is not None and cash_ils <= float(_trigger):
        return []
    spendable = _fund.spendable_cash(cash_ils, nav, objective, plan)
    _held = {t.upper() for t in (holds or {})}
    _reserved = round(max(0.0, min(float(reserved_ils or 0.0), spendable)), 2)
    spendable = round(spendable - _reserved, 2)
    if spendable < _fund.MIN_TRADE_ILS:
        # PR 83 -- ABOVE THE TRIGGER, BUT THE MONEY IS SPOKEN FOR. Eran,
        # 2026-09-29: "I have more than 1500 in cash but still no
        # recommendation card". Measured: ₪1,863 cash, ₪150 floor, and a SOXL
        # buy-back hold (sold at 142.29 on a rule-vs-sleeve card, SOXL 147.86)
        # parking the sleeve's 8.37% shortfall -- ₪1,969 -- in cash. So the
        # spendable ₪1,713 was all reserved and this returned [] in silence.
        # His answer: a card with a choice -- keep waiting, buy it back now, or
        # invest it elsewhere.
        if _reserved >= _fund.MIN_TRADE_ILS and _held and not _inner:
            # Where ELSE the reserved cash could go, and why (Eran: "I want to
            # see the other places the app recommends"): what is under target
            # with the reserve ignored (the held tickers stay excluded), plus
            # the screener's top ideas.
            under = _redeploy_cash_recs(rows, snap, plan, objective, cap, cash_ils, book_mix,
                                        name_caps, sleeve_targets, holds, 0.0, None, True, core_id=core_id)
            return [_held_cash_card(holds, _reserved, cash_ils, float(_trigger or 0.0),
                                    nav=nav, mix=current_mix(rows)[0], target=book_mix or {},
                                    under_legs=((under[0].get("apply") or {}).get("legs") or [])
                                    if under else [],
                                    ideas=ideas or [])]
        return []

    mix, _ = current_mix(rows)
    if snap.get("_real_nav"):
        # A simulated cash amount raises NAV; every invested class is diluted by
        # the same factor (review: the gaps were measured against the real NAV).
        _k = float(snap["_real_nav"]) / nav
        mix = {c: w * _k for c, w in mix.items()}
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

    name_tgt = _redeploy_name_targets(held_by_class, weights, target, sleeve_targets)

    def _value(tk: str) -> float:
        return weights.get(tk, 0.0) * nav

    def _name_gap(tk: str) -> float:
        return max(0.0, name_tgt.get(tk, 0.0) * nav - _value(tk))

    def _name_reason(tk: str, cls: str) -> str:
        t = name_tgt.get(tk, 0.0)
        w = weights.get(tk, 0.0)
        if tk.upper() in {k.upper() for k in (sleeve_targets or {})}:
            return f"the {tk} sleeve is {w:.1%} of the book vs your {t:.1%} target"
        return f"{tk} holds your core {cls}: {w:.1%} vs {t:.1%} of the book"

    # Gaps per asset class, largest shortfall first.
    gaps = []
    for cls, tw in (target or {}).items():
        if cls.lower() == "cash" or not tw:
            continue
        gap_ils = max(0.0, (float(tw) - float(mix.get(cls, 0.0)))) * nav
        if gap_ils >= _fund.MIN_TRADE_ILS:
            gaps.append((cls, gap_ils, float(tw)))
    if not gaps and not name_tgt:
        return []
    gaps.sort(key=lambda g: g[1], reverse=True)

    total_gap = sum(g[1] for g in gaps) or 1.0
    legs: list[dict] = []
    unplaced: list[dict] = []
    remaining = spendable
    for cls, gap_ils, tw in gaps:
        if remaining < _fund.MIN_TRADE_ILS:
            break
        # Proportional share of the surplus, never more than the class actually needs.
        budget = min(gap_ils, round(spendable * (gap_ils / total_gap), 2), remaining)
        held = held_by_class.get(cls) or []
        if held and name_tgt:
            # Only names BELOW their target, in proportion to how far below.
            short = {p.ticker: _name_gap(p.ticker) for p in held}
            short = {tk: g for tk, g in short.items() if g > 0}
            tot = sum(short.values())
            for tk, g in sorted(short.items(), key=lambda kv: -kv[1]):
                if remaining < _fund.MIN_TRADE_ILS:
                    break
                room = _fund.name_room_ils(
                    nav, weights.get(tk, 0.0), _fund.effective_name_cap(cap, tk, name_caps))
                amt = round(min(budget * g / tot, g, room, remaining), 2)
                if amt < _fund.MIN_TRADE_ILS:
                    continue
                legs.append({"ticker": tk, "amount_ils": amt, "asset_class": cls,
                             "reason": _name_reason(tk, cls)})
                remaining = round(remaining - amt, 2)
        elif held:
            # Top up what you already own, weakest weight first.
            held.sort(key=lambda p: weights.get(p.ticker, 0.0))
            per = round(budget / len(held), 2)
            for p in held:
                if remaining < _fund.MIN_TRADE_ILS:
                    break
                room = _fund.name_room_ils(
                    nav, weights.get(p.ticker, 0.0),
                    _fund.effective_name_cap(cap, p.ticker, name_caps))
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
    if name_tgt and remaining >= _fund.MIN_TRADE_ILS:
        _plan_weight_pass(legs, remaining, name_tgt, held_by_class, weights, nav, cap,
                          name_caps, _name_gap, _name_reason)
        remaining = round(spendable - sum(x["amount_ils"] for x in legs), 2)

    # With name targets the plan-weight pass above already placed the surplus;
    # this fallback is for books without sleeves.
    _fallback = not name_tgt
    if not legs and remaining >= _fund.MIN_TRADE_ILS and _fallback:
        best = None
        for cls, gap_ils, tw in gaps:
            for p in (held_by_class.get(cls) or []):
                room = _fund.name_room_ils(
                    nav, weights.get(p.ticker, 0.0),
                    _fund.effective_name_cap(cap, p.ticker, name_caps))
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
    if not name_tgt and remaining >= _fund.MIN_TRADE_ILS and legs:
        topups = [x for x in legs if not x.get("new_position")]
        for x in sorted(topups, key=lambda leg: weights.get(leg["ticker"], 0.0)):
            if remaining < _fund.MIN_TRADE_ILS:
                break
            already = x["amount_ils"]
            room = _fund.name_room_ils(
                nav, weights.get(x["ticker"], 0.0),
                _fund.effective_name_cap(cap, x["ticker"], name_caps)) - already
            extra = round(min(remaining, max(0.0, room)), 2)
            if extra < _fund.MIN_TRADE_ILS:
                continue
            x["amount_ils"] = round(already + extra, 2)
            x["reason"] += " (incl. budget reallocated from an unfillable class)"
            remaining = round(remaining - extra, 2)

    # PR 75: a held ticker is never a leg, whichever pass produced it.
    if _held:
        for x in [x for x in legs if x["ticker"].upper() in _held]:
            unplaced.append({"asset_class": x.get("asset_class"), "amount_ils": x["amount_ils"],
                             "reason": f"{x['ticker']} is on a take-profit buy-back hold -- kept in cash"})
        legs[:] = [x for x in legs if x["ticker"].upper() not in _held]

    # PR 75 (plan item 3): RESEARCH the core legs -- is topping up this name the
    # best use of the money in its class? Sleeves are never swapped. See
    # `redeploy_research`. Only runs when there is a core leg to research, so a
    # sleeve-only card costs no screener scan.
    _research = None
    _sleeve_set = {t.upper() for t in (sleeve_targets or {})}
    if any(x["ticker"].upper() not in _sleeve_set for x in legs):
        from app.services import redeploy_research as _rr
        try:
            _research = _rr.research(
                legs, unplaced, sleeve_tickers=_sleeve_set,
                held_tickers={t.upper() for t in weights},
                scores=_core_commodities_only(_rr.live_scores(objective, _ccy), core_id),
                trend=_rr.live_trend,
                room_for=lambda tk: _fund.name_room_ils(
                    nav, weights.get(tk, 0.0), _fund.effective_name_cap(cap, tk, name_caps)),
                min_trade=_fund.MIN_TRADE_ILS)
        except Exception:  # noqa: BLE001 - research failing must leave the sized card as it was
            logger.warning("redeploy research failed; card shown unresearched", exc_info=True)
            _research = None

    if not legs:
        return []
    deployed = round(sum(x["amount_ils"] for x in legs), 2)
    kept = round(cash_ils - deployed, 2)
    # SPENDABLE MONEY NO PASS COULD PLACE -- a holding at its concentration cap,
    # a class with no candidate under the minimum trade. It used to fall into
    # "Keep ₪X in cash (your ₪150 floor)", which called ₪4,136 a ₪150 floor.
    _silent = round(spendable - deployed - sum(u["amount_ils"] for u in unplaced), 2)
    if _silent >= 1:
        unplaced.append({"asset_class": None, "amount_ils": _silent,
                         "reason": "no underweight holding had room for it under your "
                                   "concentration caps"})
    # Money the passes or the research could not (or would not) place.
    _leftover = round(_reserved + sum(u["amount_ils"] for u in unplaced), 2) >= 1
    floor_pct = _fund.cash_target_fraction(objective, plan, nav)
    new_names = [x["ticker"] for x in legs if x.get("new_position")]

    # The soft target is 7.5, not "8%": `:.0%` rounded the user's own number.
    _soft = f"{floor_pct * 100:.1f}".rstrip("0").rstrip(".") + "%"
    if _fund.cash_target_ils_of(plan) is not None:
        _soft = _ils(_fund.cash_target_ils_of(plan))
    _excess = max(0.0, round(cash_ils - _fund.cash_soft_floor_ils(nav, objective, plan), 2))
    return [{
        "id": _rid("redeploy", round(deployed)), "dimension": "allocation", "severity": "HIGH",
        "title": f"Put {_ils(deployed)} of cash back to work",
        # No claim about WHERE the cash came from. This said "the sale proceeds
        # left your mix off-plan", which is false the day the surplus is a
        # standing-order deposit.
        "why": ((f"You're holding {_ils(cash_ils)}, above the {_ils(float(_trigger))} at which "
                 f"you asked to put cash to work. "
                 + (f"Everything above your {_soft} reserve ({_ils(_excess)}) goes into what the "
                    f"plan is short of." if not _leftover else
                    f"Of the {_ils(_excess)} above your {_soft} reserve, {_ils(deployed)} goes into "
                    f"what the plan is short of and the rest stays in cash, for the reasons below."))
                if _trigger is not None else
                (f"You're holding {_ils(cash_ils)} ({cash_ils / nav:.1%} of the portfolio), "
                 f"{_ils(_excess)} above your {_soft} cash target. Cash above the target "
                 f"earns nothing toward your goal.")),
        "action": ("Put it back to work: "
                   + "; ".join(f"{_ils(x['amount_ils'])} into {x['ticker']}" for x in legs)
                   + f". Keeps {_ils(kept)} as your buffer."),
        "impact": (("Moves every underweight asset class toward its target and cuts cash from "
                    if not _leftover else
                    "Moves the classes it buys toward their targets (some money is kept in cash "
                    "-- see below) and cuts cash from ")
                   + f"{cash_ils / nav:.1%} to {kept / nav:.1%}."
                   + (f" Adds {', '.join(new_names)} to fill a gap the plan needs."
                      if new_names else "")),
        "how": ([f"Buy {_ils(x['amount_ils'])} of {x['ticker']} — {x['reason']}" for x in legs]
                + [f"⚠ {_ils(u['amount_ils'])} stays in cash — {u['reason']}"
                   for u in unplaced if u["amount_ils"] >= 1]
                + [(f"Keep {_ils(kept)} in cash"
                    + (" in total -- the amounts above plus " if _leftover else " (")
                    + (f"your {_soft} target; the hard floor is {_ils(_fund.CASH_HARD_FLOOR_ILS)}"
                       if _soft != _ils(_fund.CASH_HARD_FLOOR_ILS) else f"your {_soft} floor")
                    + ("" if _leftover else ")")),
                   "Accept executes every leg at live prices, funded entirely from cash",
                   "Tracked book only — no brokerage order is placed"]
                + ([f"{_ils(_reserved)} more stays in cash for "
                    + ", ".join(f"{t} (not bought back until it trades below "
                                f"{float((holds or {}).get(t, {}).get('level') or 0):,.2f})"
                                for t in sorted(_held))
                    + " -- your sleeve target is unchanged"] if _reserved >= 1 else [])
                + _research_lines(_research, objective)),
        "est_amount": deployed,
        "apply": {"kind": "redeploy_cash", "legs": legs},
        "meta": {"cash_before_ils": round(cash_ils, 2), "cash_after_ils": kept,
                 "floor_pct": floor_pct,
                 # What the plan asked for but the book could not absorb. Silence
                 # here is what let ~1,940 sit idle while the card read as a
                 # complete answer.
                 "unplaced": [u for u in unplaced if u["amount_ils"] >= _fund.MIN_TRADE_ILS],
                 # What was researched, what won and why the rest lost, plus the
                 # assumption pins (investing-discipline §3).
                 "research": _research},
    }]


def _research_lines(research: dict | None, objective: str | None) -> list[str]:
    """The 'also considered' lines for the cash card, and the disclosures."""
    if not research:
        return []
    out: list[str] = []
    for r in research.get("records") or []:
        alts = r.get("also_considered") or []
        if r.get("decision") == "kept" and r.get("incumbent"):
            head = (f"Researched {r['asset_class']}: kept {r['incumbent']}"
                    + (f" ({r['incumbent_score']:.0f}/100)" if r.get("incumbent_score") is not None else ""))
        elif r.get("decision") == "swapped":
            head = f"Researched {r['asset_class']}: {r['chosen']} instead of {r['leg_ticker_before']}"
        elif r.get("decision") == "held_back":
            head = f"Researched {r['asset_class']}: nothing bought -- see the cash line"
        else:
            head = f"Researched {r['asset_class']}: {r['chosen']}"
        if alts:
            head += ". Also considered: " + "; ".join(
                f"{a['ticker']} {a['score']:.0f} ({a['lost_because']})" for a in alts)
        out.append(head)
    if out:
        from app.services import objective_weights as _ow
        _tilt = _ow.tilt_disclosure(objective)
        out.append("Scores are the screener's (weights "
                   f"{_ow.WEIGHTS_VERSION}, not owner-reviewed)"
                   + (f"; {_tilt}" if _tilt else "")
                   + ". A new name must beat what you hold by "
                   f"{research['records'][0]['margin_pts']:g} points; "
                   "confirmed downtrends (price < 50-day < 200-day) are not bought. "
                   "Not financial advice.")
    return out


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
                    "meta": {"cash_weight": round(cash_w, 4), "headsup_kind": "idle_cash"}})
        out[-1]["audit_trail"] = audit_for("income",
            raw_data={"cash_weight": round(cash_w, 4), "nav": round(nav, 2)},
            formulas=[_fml("Cash weight", "cash_weight = cash_value / NAV", result=f"{cash_w:.0%}")])
    if (objective or "") == "Income":
        yields = []
        from app.services.intake_service import is_cash_position
        for d in pdicts:
            # Not the yield of Pathward Financial, which is what "CASH" quotes.
            if is_cash_position(d["ticker"], {"asset_class": d.get("asset_class")}):
                continue
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
                   book_target: dict | None = None,
                   name_caps: dict | None = None) -> list[dict]:
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

    # QUANTITY > 0, or a ghost row silences the buy idea it looks like.
    #
    # MEASURED 2026-09-14. USO sat at quantity 0.0 and ranked 85.4 on the
    # commodity screen -- ABOVE COMT at 79.2 -- and the card recommended COMT,
    # because `held` was built from every row and USO looked held. The skip
    # below is right ("this card exists to surface a name the book does NOT
    # hold"); it was being handed a book that was not true.
    #
    # Third consequence of the same zero row, after the armed rules and the
    # Commodities-at-0% reading. `rules_service._positions_index` and
    # `_profit_recs` already filter this; this one did not.
    held = {str(getattr(r, "ticker", "") or "").upper() for r in (rows or [])
            if float(getattr(r, "quantity", 0) or 0) > 0}

    for idea in ideas:
        if len(out) >= int(_setting("max_idea_cards", _MAX_BUY_IDEA_CARDS)):
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
        name = str(idea.get("name") or "").strip()
        label = f"{tk} ({name})" if name and name != tk else tk
        reasons = [str(r) for r in (idea.get("reasons") or [])][:3]
        gaps = [str(g) for g in (idea.get("data_gaps") or [])]
        score = idea.get("score")

        buy = _fund.propose_funded_buy(
            rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
            ticker=tk, buying_class=cls, cash_ils=cash_ils,
            exclude=sleeve_names, market=str(idea.get("market") or "NYSE"),
            ledger=ledger, book_target=book_target, name_caps=name_caps)

        # THE RESERVE IS A LAST RESORT, NOT A FIRST ONE. Eran, 2026-09-06: the
        # cash target is soft, "if there is a good/valueble/interesting holding
        # that I must buy you can use the cash to do it".
        #
        # The bar is deliberately NOT a score threshold. Any number I picked
        # would be one I invented for someone else's money, and the old
        # `cash_floor_pct` docstring already records what happens when the
        # reserve becomes routinely spendable -- the book sat at 2.92% against
        # its own 3% limit and no screen called that a decision. So the rule is
        # structural instead: try the ordinary plan first, and reach for the
        # reserve only when trims and spendable cash could not pay for it at
        # all. The app never decides the name is worth the reserve; it puts the
        # score, the ranking reasons and the resulting cash percentage on the
        # card and Eran taps or does not.
        #
        # The ledger carries `cash_used` across cards in one build, so two ideas
        # cannot each plan against the same reserve.
        if buy is None and cash_ils > _fund.CASH_HARD_FLOOR_ILS:
            _why = (f"{tk} ranks {('%.0f' % float(score)) if score is not None else 'well'}"
                    f"/100 on the {objective or 'Balanced'} screen"
                    + (f" — {'; '.join(reasons).lower()}" if reasons else "")
                    + ", and nothing in the book was overweight enough to trim for it")
            buy = _fund.propose_funded_buy(
                rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
                ticker=tk, buying_class=cls, cash_ils=cash_ils,
                exclude=sleeve_names, market=str(idea.get("market") or "NYSE"),
                ledger=ledger, book_target=book_target, name_caps=name_caps,
                allow_reserve=True, reserve_reason=_why)

        if buy is None:
            # Not a failure and not worth logging as one. The plan does not want
            # this purchase right now, and the honest response is silence.
            continue

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
                     "ranked_for": idea.get("ranked_for"),
                     # PR 70: what the priority score reads for an idea --
                     # performance, potential, and how much of its class's gap
                     # this buy closes, all from numbers already on the card.
                     "idea": {"screen_score": score,
                              "return_6m_pct": (idea.get("metrics") or {}).get("trend_pct"),
                              **{k: (round(float(getattr(buy, a)) * 100, 2)
                                     if getattr(buy, a, None) is not None else None)
                                 for k, a in (("class_from_pct", "class_before"),
                                              ("class_to_pct", "class_after"),
                                              ("class_target_pct", "class_target"))}}},
        })
        already_named.add(tk)

    return out


COMMODITY_BUYS_CORE_ONLY = 1        # D80: a setting (`commodity_buys_core_only`)


def core_commodity_tickers(core_id: str | None) -> list[str]:
    """D80 (PR 116): the core basket's commodity tickers, heaviest first.

    Empty without a core, when the core holds no commodity, or when the setting
    is off. The goal model prices the Commodities slice as the core's funds
    (frontier_inputs: IAU/DBC), so a card buying sugar to fill that gap claimed
    an exposure the model never measured.
    """
    if not core_id or not int(_thr.get("commodity_buys_core_only", COMMODITY_BUYS_CORE_ONLY)):
        return []
    from app.services.strategies import core_weights
    w = core_weights(core_id) or {}
    return [t.upper() for t, _ in sorted(w.items(), key=lambda kv: -kv[1])
            if classify(t, None) == "Commodities"]


def _core_commodities_only(scores: dict, core_id: str | None) -> dict:
    """D80: with a core holding commodities, research may only swap a commodity
    top-up for another of the core's commodity funds."""
    keep = set(core_commodity_tickers(core_id))
    if not keep:
        return scores
    return {t: v for t, v in scores.items() if v.get("screen") != "Commodities" or t.upper() in keep}


def _commodity_recs(rows, snap, objective, plan=None, cap=0.25, cash_ils=0.0,
                    sleeve_names=None, ledger=None, book_mix: dict | None = None,
                    name_caps: dict | None = None, core_id: str | None = None) -> list[dict]:
    """Recommend a specific commodity, sized to the plan gap and funded explicitly.

    With a core that holds commodities (and `commodity_buys_core_only` on), only
    the core's commodity funds are candidates, ranked by the same screen.
    """
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
    core_com = core_commodity_tickers(core_id)
    try:
        from app.agents.screener_agent import OpportunityAgent
        picks = OpportunityAgent().screen_commodities(top_n=1000 if core_com else 3)
    except Exception:  # noqa: BLE001
        picks = []
    if core_com:
        picks = [p for p in picks if p.ticker.upper() in core_com][:3]
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
    # PR 115 (D-c): below the ONE card floor there is no card and no heads-up.
    # A ₪180 gap on a small book used to become "Buy ₪180 of X" with nothing to
    # press, because funding refuses any leg under the funded minimum.
    if gap_ils < _thr.card_floor_ils(nav):
        return out
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
            book_target=book_mix, name_caps=name_caps)
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


def _rebalance_plan(rows, target: dict) -> dict:
    """THE ONE SIZING of "rebalance the book to its target", for the card AND Accept.

    Pure. Returns the per-holding legs Accept will execute and the tax the sales
    owe, so the card can quote the tax before the tap and the executor cannot
    trade a different number (the rule `_core_plan` already follows).

    PR 73 -- WHY THIS EXISTS. `_rebalance_to` used to compute one factor per asset
    class and write the new share counts straight into the book: no sale, no
    purchase, no tax, and NO LEDGER ROW. Reproduced on a book shaped like Eran's
    (Equities x1.584, Fixed Income x0.2475, Commodities x1.1, 0 transactions),
    and matched live: the rebalance he accepted on 2026-09-26 13:14:49 scaled
    USO and UGA by exactly 1.0295 and SOXL and VTI by 1.00268 -- two holdings
    per class agreeing to five decimals -- none of it recorded. Sixteen rebalance
    accepts since 08-21 went the same way, which is why the per-holding chart
    could not rebuild a share count for QQQ, USO, UGA or VTI.

    Now the SAME target mix is reached with real legs: every holding whose count
    falls is a SELL (valued and taxed through `_sale_value_ils`, the function
    every other sell uses), every holding whose count rises is a BUY funded only
    by the sales' net proceeds. Tax is real money leaving the book, so the buys
    are scaled to what the sales actually raise; the book lands a hair short of
    the target by exactly the tax, and says so.

    Cash stays out, as before (PR 17): a rebalance reshuffles INVESTED value.
    """
    from collections import defaultdict

    from app.services.fx import book_nav_ils, fx_rate, position_value_ils, price_currency
    from app.services.intake_service import is_cash_position

    investable = [p for p in rows
                  if not is_cash_position(p.ticker, p.meta if isinstance(p.meta, dict) else None)]
    nav = book_nav_ils(investable)
    empty = {"sells": [], "buys": [], "tax_ils": 0.0, "gross_sold_ils": 0.0,
             "net_proceeds_ils": 0.0, "buy_ils": 0.0, "buy_scale": 1.0, "nav_ils": nav}
    if not nav:
        return empty
    byc = defaultdict(list)
    for p in investable:
        byc[classify_holding(p)].append(p)
    present_weight = sum(w for c, w in target.items() if c in byc) or 1.0
    sells, buys = [], []
    for c, rowsc in byc.items():
        w = target.get(c, 0.0)
        desired = (w / present_weight) * nav  # classes absent from target -> 0 (sold down)
        cur = sum(position_value_ils(p) for p in rowsc)
        for p in rowsc:
            price = float(p.current_price or 0)
            if price <= 0:
                continue
            meta = p.meta if isinstance(p.meta, dict) else {}
            rate = fx_rate(price_currency(p.market, meta))
            share = (position_value_ils(p) / cur) if cur > 0 else 1.0 / len(rowsc)
            new_qty = desired * share / (price * rate)
            old_qty = float(p.quantity or 0)
            delta = new_qty - old_qty
            if abs(delta) * price * rate < 0.01:          # under an agora: nothing to trade
                continue
            leg = {"id": str(p.id), "ticker": p.ticker, "market": p.market,
                   "asset_class": c, "price": price, "rate": rate, "old_qty": old_qty}
            if delta < 0:
                shares = min(old_qty, -delta)
                gross, tax = _sale_value_ils(shares, price, float(p.cost_basis or 0),
                                             p.market, meta)
                sells.append({**leg, "shares": shares, "value_ils": gross, "tax_ils": tax})
            else:
                buys.append({**leg, "shares": delta, "value_ils": delta * price * rate})
    gross = sum(s["value_ils"] for s in sells)
    tax = sum(s["tax_ils"] for s in sells)
    net = gross - tax
    want = sum(b["value_ils"] for b in buys)
    scale = min(1.0, net / want) if want > 0 else 1.0
    for b in buys:
        b["shares"] *= scale
        b["value_ils"] *= scale
    return {"sells": sells, "buys": buys, "tax_ils": round(tax, 2),
            "gross_sold_ils": round(gross, 2), "net_proceeds_ils": round(net, 2),
            "buy_ils": round(sum(b["value_ils"] for b in buys), 2),
            "buy_scale": round(scale, 6), "nav_ils": nav}


async def _rebalance_to(session, user, rows, objective: str) -> dict:
    """Rebalance the book to its target mix WITH REAL TRADES. Returns the legs.

    Reads the same `_book_target` the card does, and sizes through the same
    `_rebalance_plan` -- one sizing, two callers. Sells first (they raise the
    money), then buys out of the net proceeds; anything the buys could not use
    lands in cash. The returned detail is what `_record_apply` writes to the
    ledger, so every share this moves is a Buy or Sell row with its price, value
    and tax. See `_rebalance_plan` for why that was not true before PR 73.

    **Every value here is ILS.** (PR 1: summing native prices against an ILS cash
    row minted shares out of the arithmetic.) The NAV check below now expects
    the book to fall by exactly the tax the sales owe -- the invariant
    `_conservation_report` states for every other accept -- and refuses anything
    else.
    """
    from app.services.fx import book_nav_ils
    from app.services.intake_service import buy_into_book

    target = (await _book_target(session, user, objective))["target"]
    total_before = book_nav_ils(rows)
    plan = _rebalance_plan(rows, target)
    from app.services import buyback_hold as _bh   # PR 139: before the first sale
    await _bh.assert_buyable(session, user, [b["ticker"] for b in plan["buys"] if b["shares"] > 0])
    # BY ROW, NEVER BY TICKER. The plan sizes each ROW; a ticker can have two
    # (two accounts). Keyed by ticker, the first review of this PR sold one
    # row's leg out of the other, drove it negative, and `delete_position`
    # (which removes every row of the ticker) deleted ~48k ILS of BND with no
    # ledger row -- committed before the NAV check could object. Each leg now
    # touches exactly the row it was sized on, and a row sold to zero is
    # removed on its own (`update_position(quantity=0)` deletes that row only).
    by_id = {str(p.id): p for p in rows}

    sold, bought = [], []
    for s in plan["sells"]:
        p = by_id.get(s["id"])
        if p is None:
            continue
        remaining = float(p.quantity) - s["shares"]
        # NOT rounded here: `update_position` quantises to the MONEY column's
        # own scale, so the object and the stored row hold the same number.
        await update_position(session, user, str(p.id),
                              quantity=(0.0 if remaining <= 1e-9 else remaining))
        sold.append({"ticker": s["ticker"], "shares": round(s["shares"], 6),
                     "price": s["price"], "value_ils": round(s["value_ils"], 2),
                     "tax_ils": round(s["tax_ils"], 2), "market": s["market"]})
    spent = 0.0
    for b in plan["buys"]:
        if b["shares"] <= 0:
            continue
        await buy_into_book(session, user, ticker=b["ticker"], market=b["market"],
                            price=b["price"], shares=b["shares"],
                            asset_class=b.get("asset_class"))
        spent += b["value_ils"]
        bought.append({"ticker": b["ticker"], "shares": round(b["shares"], 6),
                       "price": b["price"], "amount_ils": round(b["value_ils"], 2),
                       "market": b["market"]})
    await session.flush()
    leftover = round(plan["net_proceeds_ils"] - spent, 2)
    credited = 0.0
    if leftover >= 0.01:
        # Sales with nothing to buy (a class sold down to zero, say): the net
        # proceeds are the user's money and land in cash, visibly.
        credited = await credit_cash(session, user, leftover)

    fresh = await list_positions(session, user)
    total_after = book_nav_ils(fresh)
    expected = total_before - plan["tax_ils"]
    if abs(total_after - expected) > max(1.0, total_before * 1e-6):
        raise ValueError(
            f"rebalance moved NAV by {total_after - total_before:+.2f} ILS against "
            f"{-plan['tax_ils']:+.2f} of tax ({total_before:.2f} -> {total_after:.2f}); "
            f"refusing to write it")
    return {"sold": sold, "bought": bought, "tax_ils": plan["tax_ils"],
            "cash_added_ils": round(credited, 2) if credited else 0.0,
            "rebalance": {"gross_sold_ils": plan["gross_sold_ils"],
                          "net_proceeds_ils": plan["net_proceeds_ils"],
                          "bought_ils": round(spent, 2), "buy_scale": plan["buy_scale"]}}


def _lot(r, k):
    """A position row's field, whether it is an ORM row or a `load_positions` dict."""
    return r.get(k) if isinstance(r, dict) else getattr(r, k, None)


def _plan_sale(lots: list, shares: float) -> dict:
    """Which lots a sale of `shares` takes, and what it is worth. PURE.

    ONE place decides the order (oldest row first, by `created_at`, then id),
    so the card that ESTIMATES a sale and the Accept that EXECUTES it take the
    same lots at the same bases. PR 77's first version valued the card in
    `list_positions` order (no ORDER BY) and sold oldest-first: on a book with
    an older 6 @ 100 and a newer 4 @ 140 the card said ₪22 of tax and Accept
    took ₪62.50 (found in review, reproduced).
    """
    def _age(r):
        at = _lot(r, "created_at")
        at_s = at.isoformat() if hasattr(at, "isoformat") else str(at or "")
        return (at is None, at_s, str(_lot(r, "id") or ""))
    left = max(0.0, float(shares or 0))
    legs, sold, gross, tax, price = [], 0.0, 0.0, 0.0, None
    total = sum(float(_lot(r, "quantity") or 0) for r in lots)
    for r in sorted(lots, key=_age):
        if left <= 1e-9:
            break
        qty = float(_lot(r, "quantity") or 0)
        take = min(left, qty)
        if take <= 0:
            continue
        g, t = _sale_value_ils(take, float(_lot(r, "current_price") or 0),
                               float(_lot(r, "cost_basis") or 0), _lot(r, "market"),
                               _lot(r, "meta"))
        legs.append((r, take))
        gross, tax, sold, left = gross + g, tax + t, sold + take, left - take
        price = float(_lot(r, "current_price") or 0)
    return {"legs": legs, "shares": sold, "gross": gross, "tax": tax, "price": price,
            "full_exit": total - sold <= 1e-9}


async def _sell_shares(session, user, rows_of_ticker: list, shares: float) -> dict:
    """Sell `shares` of one ticker across ALL its rows, oldest first.

    PR 77 -- the defect recorded after PR 73: `trim` and `sell_position` valued
    ONE row (`by_ticker` keeps the last) and, on a full exit, `delete_position`
    removed EVERY row of the ticker+market. A ticker held in two accounts lost
    the second account's shares with no proceeds and no ledger leg. Here each
    row is valued with its own cost basis and only what is sold leaves it; a
    row sold to zero is removed by `update_position` (the ghost-row rule).
    """
    plan = _plan_sale(rows_of_ticker, shares)
    for r, take in plan["legs"]:
        await update_position(session, user, str(r.id),
                              quantity=max(0.0, float(r.quantity or 0) - take))
    return {k: plan[k] for k in ("shares", "gross", "tax", "price", "full_exit")}


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


async def _decision_recommendation(session: AsyncSession, user: User, strategy_id: str,
                                   ticker: str) -> tuple[str, str]:
    """("keep" | "sell", why) for a rule-vs-sleeve card. PR 75, Eran's choice.

    Reads the STORED daily signal (`strategy_signal_state`), never fetches: the
    Today view must not wait on a price feed. Fresh = its session is within the
    signal service's own MAX_FEED_AGE_DAYS. The strategy's rule still holding
    the ticker -> Keep; out of it, or no fresh signal -> Sell.
    """
    try:
        from app.services import strategy_signal_service as sss
        row = await sss._state(session, user.email, strategy_id)
    except Exception:  # noqa: BLE001
        row = None
    if row is None or not row.as_of:
        return "sell", (f"No stored signal for the {strategy_id} strategy, so the "
                        f"risk-reducing answer is the default.")
    try:
        age = (datetime.now(timezone.utc).date()
               - datetime.fromisoformat(str(row.as_of)[:10]).date()).days
    except ValueError:
        age = 10**6
    if age > sss.MAX_FEED_AGE_DAYS:
        return "sell", (f"The {strategy_id} signal is from {row.as_of} ({age} days old), "
                        f"too old to lean on, so the risk-reducing answer is the default.")
    w = float((row.target or {}).get(ticker.upper(), (row.target or {}).get(ticker, 0)) or 0)
    if w > 0:
        return "keep", (f"The {strategy_id} rule still holds {ticker} as of {row.as_of} "
                        f"— the trend it was bought for is intact.")
    return "sell", (f"The {strategy_id} rule is out of {ticker} as of {row.as_of} "
                    f"— the trend it was bought for has turned.")


async def _rule_conflicts_to_decisions(session: AsyncSession, user: User, recs: list[dict],
                                       contradictions: list[dict], pre: dict,
                                       weights: dict, *, nav: float = 0.0
                                       ) -> tuple[list[dict], list[dict]]:
    """Turn "your rule wants to sell a sleeve that is on target" into a choice.

    Eran, 2026-09-28, item 1: "I have trading rule triggered but no recommendation
    card to act". `contradictions.check` rule 1 was right that the sale moves the
    book away from a strategy he chose -- and wrong to settle that by hiding it.
    Both are decisions he made: the take-profit and the sleeve. Only he can say
    which one is stale. His answer to what the choices are:

      * SELL: take the slice the rule planned, and lower the sleeve by exactly
        the weight sold, so the sleeve funder does not buy it straight back.
        That round trip is measured on this book: SOXL trimmed 09-18 20:08:22
        and re-bought by `sleeve_fund` at 20:09:25; trimmed 09-22 18:40:45 and
        re-bought at 18:41:03 -- tax booked each time for nothing.
      * KEEP: the position stays and the rule is UPDATED ("instead of retire
        the rule --> update the rule"), to a level today's price has not
        reached, so it does not fire again on the next evaluation.

    Only rule cards are converted, and only rules `keep_level` can re-level.
    Every other suppression stands and is listed in the history as "blocked".
    Never raises: a failure leaves the suppression exactly as it was.
    """
    try:
        from sqlalchemy import select as _select

        from app.models.tables import TradingRule as _TR
        from app.services import sleeve_service as _sv
        from app.services.rules_service import _positions_index, keep_level
    except Exception:  # noqa: BLE001
        return recs, contradictions
    out_recs = list(recs)
    idx = None
    for c in contradictions:
        if c.get("kind") != "sells_a_sleeve_at_target" or not c.get("suppressed"):
            continue
        card = pre.get(c.get("card"))
        if not card or not str(card.get("id", "")).startswith("rule_"):
            continue
        spec = card.get("apply") or {}
        if spec.get("kind") != "trim" or not spec.get("rule_id"):
            continue
        try:
            rule = await session.get(_TR, uuid.UUID(str(spec["rule_id"])))
            if rule is None:
                rule = (await session.scalars(_select(_TR).where(
                    _TR.subject == user.email))).all()
                rule = next((r for r in rule if str(r.id) == str(spec["rule_id"])), None)
            if rule is None:
                continue
            if idx is None:
                idx = await _positions_index(session, user)
            tk = str(spec.get("ticker") or rule.ticker).upper()
            pos = idx.get(tk) or {}
            price, cost = float(pos.get("price") or 0), float(pos.get("cost") or 0)
            new_level = keep_level(rule, price, cost)
            holders = await _sv.sleeves_holding(session, user, tk)
            if new_level is None or not holders:
                continue
            sl = holders[0]
            shares = float(spec.get("shares") or 0)
            w_now = float(weights.get(tk, 0.0)) * 100.0
            if pos.get("lots"):
                _pl = _plan_sale(pos["lots"], shares)    # the lots Accept will take
                gross, tax = _pl["gross"], _pl["tax"]
            else:
                gross, tax = _sale_value_ils(shares, price, cost, pos.get("market"), pos.get("meta"))
            # THE WEIGHT SOLD, FROM THE SHEKELS SOLD. It was shares / qty x
            # weight, and `qty` comes from `_positions_index`, which keeps ONE
            # row per ticker while `weights` sums every row -- so a ticker held
            # in two accounts halved the denominator and the sleeve would have
            # been cut twice as far as the sale (found in review). Value over
            # NAV has no per-row term to get wrong.
            if not nav or nav <= 0:
                continue
            sold_pct = gross / float(nav) * 100.0
            w_after = max(0.0, w_now - sold_pct)
            unit = "%" if rule.mode == "pct" else ""
            name = sl["strategy_id"]
            # WHICH ANSWER IS RECOMMENDED: the sleeve's own strategy signal
            # (Eran, 2026-09-28). Its rule still holding the ticker -> Keep; out,
            # or no fresh signal -> Sell, the risk-reducing fallback.
            rec_choice, rec_why = await _decision_recommendation(session, user, name, tk)
            decision = {
                **card,
                "title": (f"{tk} hit your take-profit — but it's on your "
                          f"{sl['sleeve_pct']:g}% sleeve target. Your call."),
                "why": (f"Two of your own decisions disagree. The rule says take a slice at "
                        f"{float(rule.level):g}{unit} (now {price:,.2f}). The sleeve says hold "
                        f"{sl['ticker_pct']:.1f}% of the book in {tk}, and you hold {w_now:.1f}%."),
                "action": (f"Sell {shares:g} {tk} (~₪{gross:,.0f}) and keep the "
                           f"{sl['sleeve_pct']:g}% sleeve target — or keep the shares and move "
                           f"the rule to {new_level:g}{unit}."),
                # PR 75 (Eran: "if I chose to sell it should not change the goal of
                # the sleeve, only the current holding"). The sale no longer
                # resizes the sleeve; a buy-back hold stops the app re-buying it.
                "impact": (f"Sell: about ₪{max(0.0, gross - tax):,.0f} lands in cash after an "
                           f"estimated ₪{tax:,.0f} of tax; {tk} goes from {w_now:.1f}% to about "
                           f"{w_after:.1f}% of the book while the {name} sleeve target stays "
                           f"{sl['sleeve_pct']:g}%. Nothing re-buys {tk} while it trades at or above "
                           f"{price:,.2f}; once it falls below that, top-ups resume. "
                           f"Keep: nothing is sold; the rule re-arms at {new_level:g}{unit}."),
                "how": [f"Sell: trims {shares:g} {tk} in your InvestWise book and credits the "
                        f"net proceeds to cash. Your sleeve target is not changed.",
                        f"Keep & update rule: nothing is sold; the take-profit moves to "
                        f"{new_level:g}{unit} (you can type a different level).",
                        "No brokerage order is placed — mirror a sale at your broker.",
                        # Honest about Ignore: dismissing a take-profit has always
                        # latched it off (a declined firing is not re-asked). Keep
                        # is the button that updates the rule instead.
                        "Ignore hides this and switches the take-profit off, as with any "
                        "declined take-profit. Use Keep to hold the position AND keep a "
                        "take-profit."],
                "est_amount": round(gross, 2),
                "apply": {"kind": "rule_vs_sleeve", "rule_id": str(rule.id), "ticker": tk,
                          "recommended": rec_choice, "recommended_why": rec_why,
                          "options": {
                              "sell": {"apply": dict(spec), "strategy_id": name,
                                       "sleeve_pct": sl["sleeve_pct"],
                                       "weight_now_pct": round(w_now, 2),
                                       "weight_after_pct": round(w_after, 2),
                                       "hold_above": round(price, 6),
                                       "gross_ils": round(gross, 2), "tax_ils": round(tax, 2)},
                              "keep": {"level": new_level, "old_level": float(rule.level),
                                       "mode": rule.mode, "rule_type": rule.rule_type}}},
                "actionable": True,
                "decision": True,
                "notes": [],
            }
            out_recs.insert(0, decision)
            c["suppressed"] = False
            c["converted_to"] = decision["id"]
        except Exception:  # noqa: BLE001 -- the suppression stands; never break Today
            logger.warning("could not convert rule conflict %s", c.get("card"), exc_info=True)
    return out_recs, contradictions


def cap_keep_level(weight_pct: float, target_pct: float) -> float:
    """Where a max-weight cap moves when you KEEP a holding its plan wants.

    Above both today's weight and the plan's target, by 1.5 points, rounded up
    to a whole percent -- VTI at 18.1% with an 18.2% core target -> 20%. The
    same headroom a sleeve cap gets, so the cap guards against a RUN past the
    plan instead of trimming the plan itself.
    """
    import math
    return float(math.ceil(max(float(weight_pct or 0), float(target_pct or 0)) + 1.5))


_RULE_WORDS = {"take_profit": "take-profit", "max_weight": "max-weight cap",
               "stop_loss": "stop-loss", "trailing_stop": "trailing stop",
               "price_above": "price alert", "price_below": "price alert"}


async def _rule_vs_plan_decisions(session: AsyncSession, user: User, recs: list[dict],
                                  weights: dict, *, nav: float, btgt: dict
                                  ) -> list[dict]:
    """PR 79 -- a rule trim that would leave a holding BELOW ITS PLAN TARGET is a decision.

    `_rule_conflicts_to_decisions` (PR 72) covers one shape: a rule selling a
    SLEEVE ticker already at or under its sleeve target. Two shapes escaped it,
    and the first one was live on 2026-09-29:

      * a CORE ticker. VTI's plan target is 18.2% (60% of a 30.4% core); a 15%
        max-weight cap -- suggested by the app when VTI was 12% -- trimmed it to
        15%, and the realign card bought it straight back (ledger: 09-26 12:26
        buy, 13:14 trim; 09-29 07:42 buy, 08:46 trim).
      * a trim that OVERSHOOTS: the ticker is above target, but the trim takes
        it below.

    Same card as PR 72, same two answers (Eran, 2026-09-29): SELL keeps the plan
    target and records a buy-back hold at today's price; KEEP updates the rule
    so it stops fighting the plan. Stop-losses and trailing stops are NOT
    converted: a protective exit is supposed to overrule a target.
    Never raises: on any failure the card stays as it was.
    """
    try:
        from app.services import sleeve_service as _sv
        from app.services.rules_service import _positions_index, keep_level
        from app.models.tables import TradingRule as _TR
        targets = await plan_ticker_targets(session, user, btgt=btgt)
        claimed = {str(t).upper() for t in await _sv.sleeve_tickers(session, user)}
    except Exception:  # noqa: BLE001
        logger.warning("plan targets unavailable for rule conflicts", exc_info=True)
        return recs
    if not targets or not nav or nav <= 0:
        return recs
    out, idx = [], None
    for card in recs:
        spec = card.get("apply") or {}
        if (card.get("decision") or not str(card.get("id", "")).startswith("rule_")
                or spec.get("kind") not in ("trim", "sell_position") or not spec.get("rule_id")):
            out.append(card)
            continue
        try:
            rule = await session.get(_TR, uuid.UUID(str(spec["rule_id"])))
            tk = str(spec.get("ticker") or getattr(rule, "ticker", "")).upper()
            t = float(targets.get(tk) or 0.0)
            from app.services.advanced_rules import PROTECTIVE as _PROT
            if (rule is None or t <= 0 or rule.rule_type in _PROT
                    or rule.rule_type == "rebalance_band"):
                out.append(card)
                continue
            if idx is None:
                idx = await _positions_index(session, user)
            pos = idx.get(tk) or {}
            price, cost = float(pos.get("price") or 0), float(pos.get("cost") or 0)
            shares = float(spec.get("shares") or 0)
            if pos.get("lots"):
                _pl = _plan_sale(pos["lots"], shares)
                gross, tax = _pl["gross"], _pl["tax"]
            else:
                gross, tax = _sale_value_ils(shares, price, cost, pos.get("market"), pos.get("meta"))
            w_now = float(weights.get(tk, 0.0)) * 100.0
            w_after = max(0.0, w_now - gross / float(nav) * 100.0)
            t_pct = t * 100.0
            if w_after >= t_pct - 0.5:
                out.append(card)            # the trim stays at or above the plan: it stands
                continue
            if rule.rule_type == "max_weight":
                new_level = cap_keep_level(w_now, t_pct)
            else:
                new_level = keep_level(rule, price, cost)
            if new_level is None:
                out.append(card)
                continue
            core_id = btgt.get("core_strategy_id")
            where = ("sleeve" if tk in claimed else "core")
            owner = None
            if where == "sleeve":
                holders = await _sv.sleeves_holding(session, user, tk)
                owner = holders[0]["strategy_id"] if holders else None
            else:
                owner = core_id
            owner_name = (btgt.get("core_strategy_name") or core_id or "core") if where == "core" else (owner or "sleeve")
            word = _RULE_WORDS.get(rule.rule_type, "rule")
            unit = "%" if rule.mode == "pct" else ""
            rec_choice = "keep"
            rec_why = (f"Your plan targets {t_pct:.1f}% in {tk}. Selling to "
                       f"{w_after:.1f}% puts it below that, and the {where} would buy it back.")
            out.append({
                **card,
                "title": (f"{tk}: your {word} fights your plan's {t_pct:.1f}% target. Your call."),
                "why": (f"Two of your own decisions disagree. The {word} at "
                        f"{float(rule.level):g}{unit} says sell; your {where} "
                        f"({owner_name}) targets {t_pct:.1f}% of the book in {tk}, and you hold "
                        f"{w_now:.1f}%."),
                "action": (f"Sell {shares:g} {tk} (~₪{gross:,.0f}) and keep the {t_pct:.1f}% "
                           f"target — or keep the shares and move the {word} to "
                           f"{new_level:g}{unit}."),
                "impact": (f"Sell: {tk} goes from {w_now:.1f}% to about {w_after:.1f}%; the "
                           f"target stays {t_pct:.1f}% and nothing re-buys {tk} while it trades "
                           f"at or above {price:,.2f}. Keep: nothing is sold; the {word} moves to "
                           f"{new_level:g}{unit}."),
                "how": [f"Sell: trims {shares:g} {tk} in your InvestWise book, credits the net "
                        f"proceeds to cash, and holds re-buys of {tk} until it trades below "
                        f"{price:,.2f}.",
                        f"Keep & update rule: nothing is sold; the {word} moves from "
                        f"{float(rule.level):g}{unit} to {new_level:g}{unit} (you can type another).",
                        "No brokerage order is placed — mirror a sale at your broker."],
                "est_amount": round(gross, 2),
                "apply": {"kind": "rule_vs_sleeve", "rule_id": str(rule.id), "ticker": tk,
                          "recommended": rec_choice, "recommended_why": rec_why,
                          "conflict": where,
                          "options": {
                              "sell": {"apply": dict(spec), "strategy_id": owner,
                                       "sleeve_pct": round(t_pct, 2),
                                       "target_label": (f"Your {t_pct:.1f}% {where} target"
                                                        f" ({owner_name})"),
                                       "weight_now_pct": round(w_now, 2),
                                       "weight_after_pct": round(w_after, 2),
                                       "hold_above": round(price, 6),
                                       "gross_ils": round(gross, 2), "tax_ils": round(tax, 2)},
                              "keep": {"level": new_level, "old_level": float(rule.level),
                                       "mode": rule.mode, "rule_type": rule.rule_type,
                                       "rule_word": word}}},
                "actionable": True,
                "decision": True,
            })
        except Exception:  # noqa: BLE001 -- the card stands as it was
            logger.warning("rule-vs-plan conversion failed for %s", card.get("id"), exc_info=True)
            out.append(card)
    return out


async def _apply_held_cash(session: AsyncSession, user: User, rec: dict, spec: dict,
                           choice: str | None, alloc: str | None = None) -> dict:
    """PR 83 -- keep waiting / buy it back now / invest it elsewhere."""
    from app.services import buyback_hold as _bh
    if choice not in ("keep", "buy", "redeploy"):
        return {"ok": False, "applied": "none", "needs_choice": True, "title": rec["title"],
                "error": "Choose: keep waiting, buy it back now, or invest it elsewhere."}
    if choice == "keep":
        return {"applied": "keep_waiting", "title": rec["title"],
                "note": "Kept. The cash stays reserved; nothing was bought."}
    tks = [str(t).upper() for t in (spec.get("tickers") or [])]
    if choice == "buy":
        released = await _bh.release(session, user, tks)
        await session.commit()
        detail = await _apply_redeploy_cash(session, user, (spec.get("options") or {}).get("buy") or {})
        return {"applied": "held_cash_buy", "title": rec["title"], "released": released,
                **detail, "note": (f"Hold ended on {', '.join(released) or ', '.join(tks)}; bought "
                                   + ", ".join(f"{b['shares']:g} {b['ticker']}" for b in detail["bought"])
                                   + "." if detail.get("bought") else
                                   f"Hold ended on {', '.join(tks)}; nothing could be bought "
                                   f"({'; '.join(x['reason'] for x in detail.get('skipped', []))}).")}
    # "redeploy" = ELSEWHERE, IN THE AMOUNTS YOU SET (Eran: "invest some of the
    # money and hold some"). Only places the card offered; each leg at least the
    # minimum trade; the total at most the reserve and the cash above the floor.
    # The hold on the held ticker STAYS; what is not invested stays reserved.
    from app.services import funding_service as _fund
    from app.services.intake_service import get_cash
    opt = (spec.get("options") or {}).get("redeploy") or {}
    offered = {str(d.get("ticker")).upper(): d for d in (opt.get("destinations") or [])}
    legs, bad = [], []
    for part in [x for x in (alloc or "").split(",") if x.strip()]:
        tk, _, amt = part.partition(":")
        tk = tk.strip().upper()
        try:
            a_ils = round(float(amt), 2)
        except ValueError:
            bad.append(part)
            continue
        if a_ils <= 0:
            continue
        if tk not in offered:
            bad.append(f"{tk} was not one of the offered places")
            continue
        if a_ils < _fund.MIN_TRADE_ILS:
            bad.append(f"{tk} ₪{a_ils:,.0f} is under the ₪{_fund.MIN_TRADE_ILS:,.0f} minimum")
            continue
        legs.append({"ticker": tk, "amount_ils": a_ils,
                     "asset_class": offered[tk].get("asset_class")})
    if bad:
        return {"ok": False, "applied": "none", "title": rec["title"], "error": "; ".join(bad)}
    if not legs:
        return {"ok": False, "applied": "none", "title": rec["title"],
                "error": "Enter an amount for at least one place."}
    total = round(sum(x["amount_ils"] for x in legs), 2)
    plan = await get_plan(session, user)
    cash = await get_cash(session, user)
    rows = await list_positions(session, user)
    from app.services.fx import book_nav_ils
    spendable = _fund.spendable_cash(cash, book_nav_ils(rows),
                                     getattr(plan, "objective", None), plan)
    limit = round(min(float(spec.get("reserved_ils") or 0.0), spendable), 2)
    if total > limit + 0.5:
        return {"ok": False, "applied": "none", "title": rec["title"],
                "error": f"₪{total:,.0f} is more than the ₪{limit:,.0f} that can be invested."}
    detail = await _apply_redeploy_cash(session, user, {"legs": legs})
    kept = round(float(spec.get("reserved_ils") or 0.0) - sum(
        b["amount_ils"] for b in detail.get("bought", [])), 2)
    return {"applied": "held_cash_redeploy", "title": rec["title"], **detail,
            "kept_reserved_ils": kept,
            "note": ("Invested: " + ", ".join(f"₪{b['amount_ils']:,.0f} {b['ticker']}"
                                              for b in detail.get("bought", []))
                     + f". ₪{kept:,.0f} stays reserved for {', '.join(spec.get('tickers') or [])}.")}


async def apply_recommendation(session: AsyncSession, user: User, rec_id: str,
                               choice: str | None = None,
                               level: float | None = None,
                               alloc: str | None = None) -> dict | None:
    """Accept = apply the recommendation to holdings/plan immediately.

    Sales don't just delete holdings — the net-of-tax proceeds are credited to a
    visible CASH position, so accepting a 'sell' shows up as liquidity you can see
    and redeploy (rather than value silently vanishing).

    `choice` / `level` are only read by a `rule_vs_sleeve` decision card: "sell"
    or "keep", and for "keep" an optional level of the user's own.
    """
    built = await build_recommendations(session, user)
    # BOTH LISTS. Since PR 70 anything past the first 12 is served under
    # "N more lower-priority suggestions" with a working Accept button -- and
    # this looked only in the first 12, so Accept on one of those returned 404
    # ("no longer applies") for a card that was plainly on screen.
    rec = next((r for r in (built.get("recommendations", [])
                            + built.get("more_recommendations", [])
                            # PR 102: a heads-up's one act option (D3).
                            + [h for h in built.get("headsups", []) if h.get("apply")])
                if r["id"] == rec_id), None)
    if rec is None:
        return None
    spec = rec.get("apply") or {"kind": "none"}
    kind = spec.get("kind")
    _sleeve_after = None
    if kind == "overweight_choice":
        # PR 135c: the answer picks which of the card's two moves runs; each then
        # goes through its own path (`trim`, or `sleeve_move`, which sells nothing).
        if choice not in ("trim", "raise"):
            return {"ok": False, "applied": "none", "needs_choice": True, "title": rec["title"],
                    "error": "This card needs a choice: trim back to your size, or raise the sleeve to what you hold."}
        spec = (spec.get("options") or {})[choice]
        kind = spec.get("kind")
    if kind == "rule_vs_sleeve":
        opts = spec.get("options") or {}
        if choice not in ("sell", "keep"):
            return {"ok": False, "applied": "none", "needs_choice": True, "title": rec["title"],
                    "error": "This card needs a choice: sell the slice (the sleeve target stays), or keep & update the rule."}
        if choice == "keep":
            from app.services.rules_service import keep_and_relevel
            lvl = float(level) if level is not None else float(opts["keep"]["level"])
            res = await keep_and_relevel(session, user, spec["rule_id"], lvl,
                                         reason="rule vs sleeve: kept the position")
            if not res.get("ok"):
                return {"applied": "none", "title": rec["title"], **res}
            unit = "%" if res.get("mode") == "pct" else ""
            return {"applied": "keep_and_relevel", "title": rec["title"], **res,
                    "note": (f"Kept {res['ticker']}. Your "
                             f"{_RULE_WORDS.get(res.get('rule_type'), 'rule')} moved from "
                             f"{res['old_level']:g}{unit} to {res['new_level']:g}{unit}. "
                             f"Nothing was sold.")}
        # SELL: the rule's own trim, then the sleeve resize below.
        _sleeve_after = opts.get("sell") or {}
        spec = dict(_sleeve_after.get("apply") or {})
        kind = spec.get("kind")
        if kind != "trim":
            return {"ok": False, "applied": "none", "title": rec["title"],
                    "error": "the sell option carries no trade"}
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
    ledger_before = await _ledger_net(session, user)
    # Summed over EVERY row. `_book_snapshot` is keyed by ticker and keeps one
    # row, so a ticker held in two accounts read as a phantom gap on every
    # accept (found in review of PR 73).
    shares_before: dict[str, float] = {}
    for _p in rows:
        _k = (_p.ticker or "").upper()
        shares_before[_k] = shares_before.get(_k, 0.0) + float(_p.quantity or 0)

    if kind == "held_cash":
        # PR 86: dispatched HERE, after the before-snapshot, and run through the
        # same boundary as every other trade. PR 83 returned before this point,
        # so its buys reached the book but never the ledger, the share guard or
        # the conservation report (production 2026-09-30: VTI 620, USO 270,
        # BND 270 bought, cash 1,863 -> 703, zero ledger rows) -- the exact
        # "a new handler silently opts out" failure `_record_apply` warns about.
        out = await _apply_held_cash(session, user, rec, spec, choice, alloc)
        if out.get("bought"):
            _k2 = out.get("applied") or kind
            await _record_apply(session, user, _k2, rec_id, out)
            out["unrecorded"] = await _guard_every_share_is_a_trade(
                session, user, _k2, rec_id, shares_before, ledger_before)
            out.update(await _conservation_report(
                session, user, _k2, rec_id, nav_before, book_before, out))
        return out

    if kind == "trim":
        # PR 77: sells across EVERY row of the ticker (see `_sell_shares`).
        # The long comment that lived here (why an exit deletes the row: USO
        # at 0 shares still carried three armed rules) is honoured there --
        # a row sold to zero is removed by `update_position`.
        _held = [x for x in rows if x.ticker == spec["ticker"]]
        if _held:
            res = await _sell_shares(session, user, _held, float(spec["shares"]))
            credited = await credit_cash(session, user, res["gross"] - res["tax"])
            # `shares` IS WHAT THE LEDGER RECORDS (a 0-share sell hid 15 trims).
            # PR 147: `price` and `gross_ils` are what the ledger records; without
            # them every trim was written at price 0 and ₪0.
            detail = {"sold": [spec["ticker"]], "shares": round(res["shares"], 6),
                      "price": float(res["price"] or 0), "gross_ils": round(res["gross"], 2),
                      "cash_added_ils": round(credited, 2),
                      "tax_ils": round(res["tax"], 2), "full_exit": res["full_exit"]}
            if spec.get("hold_after_sale") and res["shares"] > 0:
                from app.services import buyback_hold as _bh
                detail["buyback_hold"] = await _bh.record(
                    session, user, ticker=spec["ticker"], sold_price=res["price"],
                    shares=res["shares"], strategy_id=None, rule_id=None)
                await session.commit()
            if spec.get("hold_until_rule") and res["shares"] > 0:
                from app.services import buyback_hold as _bh
                detail["buyback_hold"] = await _bh.record(
                    session, user, ticker=spec["ticker"], sold_price=res["price"],
                    shares=res["shares"], strategy_id=None, rule_id=spec.get("rule_id"),
                    until_rule=str(spec["hold_until_rule"]))
                await session.commit()
    elif kind == "sell_position":
        # A stop-loss / trailing stop / take-profit is a full exit by definition.
        # The position leaves the book and the net-of-CGT proceeds land in cash,
        # so the money is visible and redeployable rather than vanishing.
        _held = [x for x in rows if x.ticker == spec["ticker"]]
        if _held:
            _total = sum(float(x.quantity or 0) for x in _held)
            res = await _sell_shares(session, user, _held,
                                     float(spec.get("shares") or 0) or _total)
            credited = await credit_cash(session, user, res["gross"] - res["tax"])
            detail = {"sold": [spec["ticker"]], "shares": round(res["shares"], 6),
                      "price": float(res["price"] or 0), "gross_ils": round(res["gross"], 2),
                      "cash_added_ils": round(credited, 2), "tax_ils": round(res["tax"], 2),
                      "full_exit": res["full_exit"],
                      "broker_note": "Tracked book updated — no brokerage order was placed."}
            from app.services import buyback_hold as _bh
            if spec.get("rule_type") in _bh.PROTECTIVE_EXITS and res["shares"] > 0:
                # PR 139: a protective exit holds the ticker against every buyer
                # until its sleeve's signal is out and back in (or you end it).
                # Measured: two SOXL stop-outs re-bought by sleeve funding within
                # days, 10-12 .. 10-19, the second above its own sale price.
                detail["buyback_hold"] = await _bh.record_stop(
                    session, user, ticker=spec["ticker"], sold_price=res["price"],
                    shares=res["shares"], exit_rule_type=str(spec["rule_type"]),
                    rule_id=spec.get("rule_id"))
                await session.commit()
            elif spec.get("hold_after_sale") and res["shares"] > 0:
                from app.services import buyback_hold as _bh
                detail["buyback_hold"] = await _bh.record(
                    session, user, ticker=spec["ticker"], sold_price=res["price"],
                    shares=res["shares"], strategy_id=None, rule_id=None)
                await session.commit()
        else:
            detail = {"sold": [], "note": f"{spec['ticker']} is no longer held."}
    elif kind == "sell_losers":
        sold, proceeds, tax_total = [], 0.0, 0.0
        for tk in spec.get("tickers", []):
            p = by_ticker.get(tk)
            if p:
                # EVERY ROW `delete_position` REMOVES IS VALUED, AND THE SHARES ARE
                # REPORTED. This valued one row and reported a bare ticker, so the
                # ledger wrote a 0-share Sell (PR 73's guard caught it in review:
                # TQQQ -19.1041 unexplained) and a ticker held in two rows lost the
                # second row's value outright.
                same = [r for r in rows if r.ticker == tk and r.market == p.market]
                shares = sum(float(r.quantity or 0) for r in same)
                gross = tax = 0.0
                for r in same:
                    g, t_ = _sale_value_ils(float(r.quantity or 0), float(r.current_price or 0),
                                            float(r.cost_basis or 0), r.market, r.meta)
                    gross += g
                    tax += t_
                await delete_position(session, user, tk, p.market)
                proceeds += (gross - tax)
                tax_total += tax
                sold.append({"ticker": tk, "shares": round(shares, 6),
                             "price": float(p.current_price or 0),
                             "value_ils": round(gross, 2), "tax_ils": round(tax, 2)})
        credited = await credit_cash(session, user, proceeds)
        detail = {"sold": sold, "cash_added_ils": round(credited, 2), "tax_ils": round(tax_total, 2)}
    elif kind == "fee_swap":
        detail = await _apply_fee_swap(session, user, by_ticker, spec)
    elif kind == "buy_funded":
        detail = await _apply_buy_funded(session, user, spec)
    elif kind == "core_to_basket":
        detail = await _apply_core_to_basket(session, user, spec)
    elif kind == "frontier_rules":
        from app.services.frontier_service import apply_rules_tuning
        detail = await apply_rules_tuning(session, user, spec)
        if detail.get("refused"):
            return {"ok": False, "applied": "none", "title": rec["title"], **detail}
        # PR 104a.1: the verdict that suggested this was solved on the OLD rules;
        # without a re-solve the screen kept its numbers and read as "nothing
        # happened". Same solve a firing rule triggers (`resolve_after_rule`).
        try:
            from app.services.frontier_service import compute_for
            v2 = await compute_for(session, user, source="rules_changed")
            hl = v2.get("headline") or {}
            detail["resolved"] = {"outcome": v2.get("outcome"), "headline": hl}
            if hl.get("p_goal_pct") is not None:
                detail["note"] = (f"Done. Your goal re-solved on the new rules: {hl['p_goal_pct']:g}% chance"
                                  + (f" ({hl['without_rules_pct']:g}% with no rules at all)."
                                     if hl.get("without_rules_pct") is not None else "."))
        except Exception:  # noqa: BLE001 -- the rules are changed; tonight's solve catches up
            logger.warning("re-solve after a rules retune failed", exc_info=True)
            detail["note"] = ("Done. Your rules are changed; the goal's odds update after the next solve "
                              "(Plan > Goal > Re-solve now).")
    elif kind == "sleeve_move":
        from app.services.sleeve_moves import apply_move
        detail = await apply_move(session, user, spec)
        if detail.get("refused"):
            return {"ok": False, "applied": "none", "title": rec["title"], **detail}
    elif kind == "frontier_core":
        from app.services.frontier_service import apply_core_switch
        detail = await apply_core_switch(session, user, spec)
        if detail.get("refused"):
            return {"ok": False, "applied": "none", "title": rec["title"], **detail}
    elif kind == "frontier_resize":
        from app.services.frontier_service import apply_resize
        detail = await apply_resize(session, user, spec)
        if detail.get("refused"):
            # Refused UNTOUCHED (book moved, no price): nothing reached the
            # book, so nothing reaches the ledger either.
            return {"ok": False, "applied": "none", "title": rec["title"], **detail}
    elif kind == "edit_rule":
        from app.services.rules_service import update_rule
        detail = await update_rule(session, user, str(spec.get("target_rule_id")),
                                   level=spec.get("level"), params=spec.get("params"),
                                   force=True)
        detail = {"rule_edited": detail}
    elif kind == "rearm_rules":
        # PR 140: "Re-arm all" on the protection-off heads-up. Each rule goes
        # through `toggle_rule`, so each re-arm leaves its own "resumed" event.
        from app.services.rules_service import list_rules, toggle_rule
        still_off = {str(r["id"]) for r in await list_rules(session, user) if not r.get("active")}
        ids = [str(x) for x in spec.get("rule_ids") or [] if str(x) in still_off]
        if not ids:
            return {"ok": False, "applied": "none", "title": rec["title"],
                    "error": "every rule on this list is already on - nothing was changed."}
        for rid in ids:
            await toggle_rule(session, user, rid)
        detail = {"rules_rearmed": ids, "note": f"Re-armed {len(ids)} rule(s). Nothing was sold."}
    elif kind == "caps_to_bands":
        from app.services.advanced_rules import apply_caps_to_bands
        detail = await apply_caps_to_bands(session, user, spec)
    elif kind == "redeploy_cash":
        detail = await _apply_redeploy_cash(session, user, spec)
    elif kind == "set_standing_order":
        detail = await _apply_set_standing_order(session, user, spec)
    elif kind == "fund_sleeve":
        # Reuses the P0.1 funding path, so there is ONE implementation of "raise
        # this much and buy the sleeve" rather than a second that could size it
        # differently from the button on the Plan tab.
        from app.services.strategy_service import load_basket
        detail = await load_basket(session, user, spec["strategy_id"],
                                   sleeve_pct=spec.get("sleeve_pct"), mode="fund",
                                   honour_holds=True)
    elif kind == "fund_plan":
        # A ticker two sleeves both want. Only the whole-plan path can honour a
        # SUMMED target -- funding one of the two sleeves would buy that sleeve's
        # share while the card described the total, so the card would still be
        # right and the action would still be wrong.
        from app.services.strategy_service import fund_plan
        detail = await fund_plan(session, user, dry_run=False, honour_holds=True)
    elif kind == "rebalance_to_objective":
        plan = await get_plan(session, user)
        detail = await _rebalance_to(session, user, rows, plan.objective if plan else "Balanced")
    elif kind == "set_objective_and_rebalance":
        await upsert_plan(session, user, objective=spec["objective"])
        await session.commit()
        rows = await list_positions(session, user)
        detail = await _rebalance_to(session, user, rows, spec["objective"])
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
    # PR 75 -- THE SLEEVE TARGET STAYS; THE SALE IS REMEMBERED. Eran: "if I
    # chose to sell it should not change the goal of the sleeve, only the
    # current holding". PR 72 shrank the sleeve so the funder would not buy the
    # slice straight back; a buy-back hold at the sale price does that job
    # without editing the plan (`buyback_hold`).
    if _sleeve_after and detail.get("shares"):
        from app.services import buyback_hold as _bh
        _p = by_ticker.get(spec["ticker"])
        _px = float(_sleeve_after.get("hold_above")
                    or (getattr(_p, "current_price", None) or 0))
        detail["buyback_hold"] = await _bh.record(
            session, user, ticker=spec["ticker"], sold_price=_px,
            shares=float(detail.get("shares") or 0),
            strategy_id=_sleeve_after.get("strategy_id"), rule_id=spec.get("rule_id"))
        detail["sleeve_unchanged_pct"] = _sleeve_after.get("sleeve_pct")
        await session.commit()
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
    detail["unrecorded"] = await _guard_every_share_is_a_trade(
        session, user, kind, rec_id, shares_before, ledger_before)
    conservation = await _conservation_report(
        session, user, kind, rec_id, nav_before, book_before, detail)
    return {"applied": kind, "title": rec["title"], **detail, **conservation}


async def _ledger_net(session, user) -> dict[str, float]:
    """ticker -> the share count the LEDGER explains (Buy +, Sell -, Adjust signed)."""
    from sqlalchemy import select

    from app.models.tables import Transaction
    out: dict[str, float] = {}
    try:
        rows = (await session.execute(
            select(Transaction.ticker, Transaction.txn_type, Transaction.quantity)
            .where(Transaction.subject == user.email))).all()
    except Exception:  # noqa: BLE001 -- the guard must never cost a trade
        return out
    for tk, typ, q in rows:
        q = float(q or 0)
        d = abs(q) if typ == "Buy" else -abs(q) if typ == "Sell" else q if typ == "Adjust" else 0.0
        k = (tk or "").upper()
        out[k] = out.get(k, 0.0) + d
    return out


async def _guard_every_share_is_a_trade(session, user, kind: str, rec_id: str,
                                        shares_before: dict, ledger_before: dict) -> list[dict]:
    """NO SHARE COUNT CHANGES ON ACCEPT WITHOUT A LEDGER ROW. Enforced, not hoped.

    PR 73. `_rebalance_to` rewrote share counts for sixteen accepts with no row,
    and nothing noticed: the ledger write is best-effort, NAV was conserved so the
    rebalance's own check passed, and the only reader that could tell -- the
    per-holding chart -- fell back to the price without complaint. "Each handler
    records its own" is the rule that failed; this measures instead.

    For every ticker, the change in the book's share count across this accept is
    compared with the change in what the ledger explains. Any remainder is
    written as an `Adjust` leg naming the kind and card, and logged at ERROR --
    so the history can still be rebuilt, and the silent writer is named the day
    it appears instead of weeks later on a chart. Cash is not a share count.
    """
    from app.services import ledger_service as ledger

    after_rows = await list_positions(session, user)
    after = {}
    for p in after_rows:
        k = (p.ticker or "").upper()
        after[k] = after.get(k, 0.0) + float(p.quantity or 0)
    before = dict(shares_before or {})
    ledger_after = await _ledger_net(session, user)
    healed = []
    for tk in sorted(set(before) | set(after)):
        if tk in ("", "CASH"):
            continue
        d_book = after.get(tk, 0.0) - before.get(tk, 0.0)
        d_ledger = ledger_after.get(tk, 0.0) - ledger_before.get(tk, 0.0)
        gap = d_book - d_ledger
        # Both sides are stored at 4 decimals, so honest arithmetic can differ by
        # one rounding step (1e-4). Anything past 1.5e-4 is a share nobody recorded.
        if abs(gap) <= 1.5e-4:
            continue
        logger.error("UNRECORDED SHARE CHANGE on accept: %s %s (%s) moved %+.6f shares, "
                     "ledger explains %+.6f; writing an Adjust for %+.6f",
                     kind, rec_id, tk, d_book, d_ledger, gap)
        await ledger.record_leg(session, user, ticker=tk, txn_type="Adjust",
                                quantity=round(gap, 6), source=kind[:40],
                                note=f"unrecorded by {kind} on card {rec_id}"[:255])
        healed.append({"ticker": tk, "shares": round(gap, 6)})
    if healed:
        await session.commit()
    return healed


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
    # A handler that already wrote its own legs says so. `_execute_funded_sleeve`
    # records every leg itself (source "sleeve_fund"), and this used to record
    # the same buys and sells a second time as "fund_sleeve" -- observed on
    # production as identical pairs at the same second (SOXL 3.9046 and 6.2778,
    # BND -6.7875 and -4.4084), each trade counted twice.
    if detail.get("ledger_recorded"):
        return
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
                    # PR 147: a single-ticker sale reports its price and gross here.
                    sells.append({"ticker": s, "shares": detail.get("shares") or 0,
                                  "price": detail.get("price") or 0,
                                  "value_ils": detail.get("gross_ils") or 0})
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
                     "amount_ils": detail.get("value_ils") or 0,
                     "price": detail.get("price") or 0}]
        await ledger.record_legs(session, user, sold=sells, bought=buys,
                                 source=src, note=note)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not cost a trade
        logger.warning("ledger: could not record %s (%s): %s", kind, rec_id, exc)



async def _apply_set_standing_order(session: AsyncSession, user: User, spec: dict) -> dict:
    """Make the user's ACTIVE standing orders total `monthly_total_ils`.

    None -> create one on the 1st. One -> set its amount. Several -> add one
    order for the difference, rather than guess which of them to change. A
    paused order is left paused: pausing is a decision this card did not make.
    Moves no money now; the first deposit is recorded on its day.
    """
    from app.services import standing_order_service as so

    total = float(spec.get("monthly_total_ils") or 0.0)
    orders = [o for o in await so.list_orders(session, user) if o.active]
    current = sum(float(o.amount_ils) for o in orders)
    if total <= 0 or total <= current + 0.005:
        return {"standing_order": "unchanged", "monthly_total_ils": round(current, 2)}
    if not orders:
        o = await so.create_order(session, user, amount_ils=total, day_of_month=1,
                                  note="from the behind-your-goal card")
        action = "created"
    elif len(orders) == 1:
        o = await so.update_order(session, user, str(orders[0].id), amount_ils=total)
        action = "raised"
    else:
        o = await so.create_order(session, user, amount_ils=round(total - current, 2),
                                  day_of_month=orders[0].day_of_month,
                                  note="top-up from the behind-your-goal card")
        action = "added"
    return {"standing_order": action, "order": so.to_dict(o),
            "monthly_total_ils": await so.monthly_total(session, user),
            "broker_note": "Recorded in the app only -- set the same transfer up at your bank."}


async def _apply_redeploy_cash(session: AsyncSession, user: User, spec: dict) -> dict:
    """Buy every leg straight out of cash, at live prices.

    Cash is debited leg by leg as we go, so a price move between building the
    card and accepting it can only shorten the list -- it can never spend money
    that isn't there.
    """
    from app.providers.registry import guarded_quote
    from app.services.fx import quote_in_ils
    from app.services.intake_service import buy_into_book, get_cash, set_cash

    from app.services import buyback_hold as _bh
    cash = await get_cash(session, user)
    _stops = _bh.stop_held(await _bh.load(session, user))   # PR 139

    bought, skipped = [], []
    for leg in (spec.get("legs") or []):
        tk, amount = leg.get("ticker"), float(leg.get("amount_ils") or 0)
        if not tk or amount <= 0:
            continue
        if str(tk).upper() in _stops:
            # A cash-only buy: skipping the leg leaves its money in cash, so the
            # rest of the card still runs. Measured: 10-14 05:32 this card bought
            # 14.09 TQQQ (3,415 ILS) while the book was being re-levered.
            skipped.append({"ticker": tk, "reason": _bh.refusal(str(tk).upper(),
                                                                _stops[str(tk).upper()])})
            continue
        if amount > cash:
            skipped.append({"ticker": tk, "reason": "not enough cash left"})
            continue
        try:
            q = await _offload(guarded_quote, tk)
        except Exception:  # noqa: BLE001
            q = None
        if q is None or not q.price:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        # PR 105: major units first -- a `.TA` quote arrives in agorot, and
        # `fx_rate("ILA")` is 1.0, so this booked 100x too few shares.
        price, _ccy, price_ils = quote_in_ils(float(q.price), getattr(q, "currency", None) or "USD")
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
            price=price, shares=shares, depth=3)
        cash = round(cash - amount, 2)
        bought.append({"ticker": tk, "shares": shares, "amount_ils": round(amount, 2),
                       "price": price, "new_position": is_new})
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
    from app.services.fx import fx_rate, to_major_units
    from app.services.intake_service import buy_into_book, get_cash, set_cash

    ticker = (spec.get("ticker") or "").upper()
    from app.services import buyback_hold as _bh   # PR 139: before anything is sold
    await _bh.assert_buyable(session, user, [ticker])
    try:
        q = await _offload(guarded_quote, ticker)
        # THE 100x TRAP, at the boundary that buys. `to_major_units` says "use at
        # every boundary" in its own docstring; PR 45 fixed `add_holding` and this
        # one was left because nothing in the candidate universe could reach it.
        # PR 62 put Tel Aviv listings in that universe, which is what turns this
        # from a loaded gun into a firing one -- V6 called it exactly that.
        #
        # Yahoo quotes Tel Aviv in AGOROT and says so in the CURRENCY CODE, not
        # the price: LUMI.TA comes back as 7916.0 with currency ILA, meaning
        # 79.16 ILS. Unnormalised, `qty = native / price` buys a hundredth of the
        # shares, and `fx_rate("ILA")` is a code no provider knows -- it errors
        # and falls back rather than converting. Two wrongs that do not cancel.
        price, ccy = to_major_units(float(q.price),
                                    (getattr(q, "currency", None) or "USD"))
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
        # THE EXACT SHARE COUNT. This was f"{int(shares)} {p.ticker}": `int()`
        # cut the fraction, and `_record_apply` parses that string into the
        # ledger -- so GOOGL's funded sales are recorded as 2.0 and 1.0 shares.
        sold.append({"ticker": p.ticker, "shares": round(shares, 6),
                     "price": float(p.current_price or 0), "value_ils": round(gross, 2),
                     "tax_ils": round(tax, 2)})

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
            "shares": round(qty, 4), "value_ils": round(budget, 2), "price": float(price),
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
    from app.services.funding_service import cash_target_amount_ils
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
        if float(btgt.get("commodities_pct") or 0.0) > 0 and _ch(p) == "Commodities":
            # PR 71: Commodities are carved out of the core with their own
            # target. Without this, rule 1 of `plan_realign` would sell USO in
            # full as "not in the basket" the day after the plan bought it.
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
    # C15 (2026-10-23): WHERE the overshoot is, per ticker. The trim used to be
    # sized from the sleeves that are over and then sold pro-rata across EVERY
    # sleeve holding -- the production ledger 10-19 .. 10-21 shows each accept
    # selling TQQQ (its sleeve short) and sleeve funding buying it back minutes
    # later. A ticker the core's own basket holds is not a source either: selling
    # QQQ in one leg to buy QQQ in the next (10-20 03:59) is tax for nothing.
    basket_tks = {str(t).upper() for t in basket}
    trim_from: dict[str, float] = {}
    for s in (occ.get("sleeves") or []):
        held_pct = float(s.get("held_pct") or 0.0) * 100.0   # held_pct is a fraction
        # `target_pct`, NOT `sleeve_pct`. `sleeve_occupancy` has never emitted a
        # `sleeve_pct` key, so this read 0.0 for every sleeve -- which made the
        # comparison `held > 0` and handed the core the sleeves' ENTIRE held
        # value as "overshoot". On the live book that offered SOXL's whole
        # ₪7,686 position. `core_realign` promises in its own docstring that it
        # "will not trim a sleeve BELOW its own target... may reclaim what is
        # over that decision and may never overrule it", and this made that
        # guarantee vacuous. It never bit because the funding path only engages
        # when the buys exceed sells plus cash; on an under-funded core it would
        # have sold a whole sleeve. PR 60.1.
        want_pct = float(s.get("target_pct") or 0.0)
        if held_pct > want_pct:
            o_ils = (held_pct - want_pct) / 100.0 * nav
            mine = {str(t).upper(): float(v) for t, v in (s.get("held_by_ticker_ils") or {}).items()
                    if float(v) > 0 and str(t).upper() in sleeve_val
                    and str(t).upper() not in basket_tks}
            tot = sum(mine.values())
            if tot > 0:
                take = min(o_ils, tot)
                for t, v in mine.items():
                    trim_from[t] = trim_from.get(t, 0.0) + take * v / tot
                over += take
    if sleeve_val:
        tot = sum(sleeve_val.values())
        gain["__sleeves__"] = sum(gain.get(t, 0.0) * v for t, v in sleeve_val.items()) / tot

    # AN UNFUNDED SLEEVE HAS FIRST CLAIM ON UNCLAIMED MONEY.
    #
    # The core is the REMAINDER -- everything no sleeve claims -- so the moment a
    # sleeve is retired or swapped, its old instrument falls in here and rule 1
    # of `plan_realign` says "not in the core's basket, sell it in full". On the
    # live book that produced a card offering to sell 7,476 of TQQQ into VTI and
    # cash, while a second card said the 33% QQQ sleeve was unfunded and needed
    # 7,238. Two cards, two destinations, one position -- and `_fund_sleeve`
    # excludes only tickers a sleeve CURRENTLY claims, so its funding plan would
    # have sold the same TQQQ.
    #
    # That is the duplicate-card shape PR 54.1 fixed one layer up, and the tie
    # breaks toward the sleeve: a sleeve size is a decision the user made on the
    # Plan tab, while the core is what is left over. Reserved WHOLE rather than
    # sliced -- the funding plan decides how much it actually needs, and a core
    # card that sells part of it first is exactly the pre-emption being removed.
    #
    # A basket leg is never deferred: that is core money doing its job.
    deferred: list[dict] = []
    unfunded_ils = 0.0
    for s in (occ.get("sleeves") or []):
        # Same key mistake as the loop above, and it is why PR 60 shipped with
        # the deferral silently dead: unfunded_ils came to 0.00 on a book with a
        # 33% sleeve holding nothing, so the core went on offering to sell TQQQ.
        # The deploy check caught it -- "STILL SELLING TQQQ" -- which is the
        # argument for a step 5 that asks production rather than trusting green
        # tests. PR 60.1.
        gap = float(s.get("target_pct") or 0.0) - float(s.get("held_pct") or 0.0) * 100.0
        if gap > 0:
            unfunded_ils += gap / 100.0 * nav
    if unfunded_ils >= core_realign.MIN_LEG_ILS:
        for tk in sorted(held):
            if tk in {t.upper() for t in basket}:
                continue
            deferred.append({
                "ticker": tk, "ils": round(held[tk], 2),
                "why": (f"your sleeves are about ₪{unfunded_ils:,.0f} short of the "
                        f"sizes you set, and this is money no sleeve claims — "
                        f"funding a sleeve with it is a decision you already made")})
        for d in deferred:
            held.pop(d["ticker"], None)

    from app.core.config import get_settings
    plan_row = await get_plan(session, user)   # module-level import; one loader
    # PR 79: honour buy-back holds -- the realign bought VTI straight back after
    # every rule trim. Read-only here; the Today build releases retreated holds.
    try:
        from app.services import buyback_hold as _bh
        _no_buy = set(_bh.active(await _bh.load(session, user), rows))
    except Exception:  # noqa: BLE001 - a hold read must not cost the plan
        _no_buy = set()
    out = core_realign.plan_realign(
        basket=basket, core_pct=core_pct, nav=nav, held=held,
        cash_ils=cash_ils, cash_floor_ils=cash_target_amount_ils(nav, objective, plan_row),
        sleeve_over_ils=over, cgt_rate=float(get_settings().cgt_rate),
        gain_frac=gain, deferred=deferred, no_buy=_no_buy)
    out["core_strategy_id"] = core_id
    out["core_strategy_name"] = btgt.get("core_strategy_name") or core_id
    out["sleeve_values_ils"] = {t: round(v, 2) for t, v in sorted(sleeve_val.items())}
    out["sleeve_trim_from_ils"] = {t: round(v, 2) for t, v in sorted(trim_from.items())}
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
    from app.services.intake_service import (
        buy_into_book, credit_cash, get_cash, set_cash)

    objective = spec.get("objective")
    rows = await list_positions(session, user)
    nav = book_nav_ils(rows)
    btgt = await _book_target(session, user, objective)
    plan = await _core_plan(session, user, rows, nav, objective, btgt)
    if not plan or not plan.get("ok"):
        return {"note": ((plan or {}).get("detail")
                         or "There is nothing to realign in your core right now.")}

    buys = [leg for leg in plan["legs"] if leg["action"] == "buy"]
    from app.services import buyback_hold as _bh   # PR 139: before the first sale
    await _bh.assert_buyable(session, user, [leg["ticker"] for leg in buys])
    priced: dict[str, tuple[float, str]] = {}
    for leg in buys:
        tk = leg["ticker"]
        try:
            q = await _offload(guarded_quote, tk)
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
        # `shares` and `value_ils` are what the ledger records. With only
        # `ils` here, every core realign sale went into `transactions` as a
        # 0-share, 0-value sell (seen on production: SOXL and BND, 09-02, 09-06).
        sold.append({"ticker": p.ticker, "shares": round(shares, 6), "price": px,
                     "value_ils": round(gross, 2), "ils": round(gross, 2),
                     "tax_ils": round(tax, 2), "full_exit": bool(full)})

    for leg in plan["legs"]:
        if leg["action"] == "sell":
            p = by_ticker.get(leg["ticker"])
            if p is not None:
                await _sell_ils(p, float(leg["ils"]), bool(leg.get("full_exit")))
        elif leg["action"] == "trim_sleeves":
            # FROM THE SLEEVES THAT ARE OVER, ONLY (C15). Spread over each such
            # sleeve's own holdings in proportion to its overshoot -- never a
            # sleeve under its size, which "Fund my sleeves" would buy straight
            # back, and never a ticker the core basket holds.
            vals = plan.get("sleeve_trim_from_ils") or {}
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
    #
    # DEBIT IT FOR REAL. This used to call `credit_cash(-cash_top)`, which is a
    # no-op for any amount <= 0, and then added `cash_top` to the budget anyway:
    # every cash-funded realign bought its ETFs with money the book never had,
    # and the unspent remainder was credited a second time below. Production,
    # 2026-09-29: +3,678 ILS of NAV from one accept, no market open.
    # Clamp to today's balance -- the plan may be sized on an older book.
    cash_top = float(plan.get("cash_used_ils") or 0.0)
    if cash_top > 0:
        cash_before = await get_cash(session, user)
        cash_top = min(cash_top, max(0.0, cash_before))
        if cash_top > 0:
            await set_cash(session, user, round(cash_before - cash_top, 2))
            budget += cash_top

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
        # `price` and `value_ils` so the ledger records what the buy cost: with
        # only `ils`, every realign buy went in at 0 ILS and the audit of the
        # phantom-cash defect had to re-price them from daily closes.
        bought.append({"ticker": tk, "ils": round(spend, 2), "value_ils": round(spend, 2),
                       "price": px, "shares": round(native / px, 4)})

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
    from app.services import buyback_hold as _bh   # PR 139: before the sale
    await _bh.assert_buyable(session, user, [buy_tk])
    try:
        q = await _offload(guarded_quote, buy_tk)
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
