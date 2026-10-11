"""Apply a strategy (preset + rebalance trades) and fund or load its basket.

Two very different things used to sit one click apart under near-identical
wording. They are now separate modes with separate labels:

* ``mode="fund"`` -- the default for the rule-based "Beat the Market" family.
  A sleeve **coexists** with the rest of the book by definition, so funding it
  raises only the shortfall: spendable cash down to the objective's cash floor
  first, then the worst-fitting holdings ranked by plan fit. Everything not
  named as a funding leg survives untouched.
* ``mode="replace"`` -- the original behaviour, retained for the four static
  model-basket families, where wholesale replacement *is* the point. The caller
  must confirm it against a list of exactly what would be deleted.

Neither mode places a brokerage order. Both update the tracked book only, and
say so.
"""
from __future__ import annotations


from sqlalchemy.ext.asyncio import AsyncSession

from app.core.offload import offload
from app.engines.allocation_engine import AllocationEngine
from app.models.tables import User, money
from app.providers.registry import guarded_quote
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import strategies as cat
from app.services import strategy_catalog
from app.services.allocation_mix import current_mix
from app.services.funding_service import (
    describe_funding, min_trade_ils, plan_funding)
# The single asset class a set of buy legs lands in, or None if mixed. Lives in
# funding_service beside the argument it feeds: `describe_funding` cannot do its
# job without one, and owning a private copy here is exactly how the Today path
# ended up never computing one at all.
from app.services.funding_service import buying_class_of as _buying_class
from app.services.fx import fx_rate, price_currency, quote_in_ils
from app.services.intake_service import (
    buy_into_book, delete_position, ensure_account, ensure_entity, get_cash,
    is_cash_position, list_positions, set_cash, upsert_positions)
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot

# Modes `load_basket` understands. "fund" is additive, "replace" is destructive.
FUND = "fund"
REPLACE = "replace"
# Whole-plan funding is not a `load_basket` mode: it is not about one basket.
FUND_PLAN = "fund_plan"

# C3b: multi-sleeve funding EXECUTES.
#
# It shipped False in C3a deliberately -- spending one budget across several
# sleeves is the first thing in this phase that can sell shares nobody looked at
# first. Flipped only after the preview was read against the real book: a 15%
# factor sleeve on a 21,297 NAV planned 3,195 of buys, funded by trimming VXUS
# and SCHD for 10 shekels of tax, with a 38 shekel residual (0.18% of NAV). The
# arithmetic, the exclusion set and the refusal were all checked there first.
#
# Kept as a switch rather than deleted: it is the fastest way to take the
# multi-sleeve write out of service without a revert, and a test asserts that
# setting it False still refuses.
PLAN_FUNDING_EXECUTION = True

BROKER_NOTE = "Tracked book updated — no brokerage order was placed."

# How far short of the chosen sleeve we may land before refusing to execute.
#
# Measured in POINTS OF NAV, because that is the unit the user actually chose --
# the slider sets a share of the book and the label renders it as a whole
# percent. A gap smaller than one point cannot change the number they were
# looking at when they decided; a gap larger than one point means the sleeve
# they get is not the sleeve they asked for, and installing it anyway is a
# position at a size nobody chose.
#
# Sized against MIN_TRADE_ILS deliberately: the funding engine stops raising
# money once the remainder falls below the minimum worthwhile trade, so it can
# legitimately land up to MIN_TRADE_ILS short. On a small book that is several
# points of NAV -- which is exactly the case that must abstain, and exactly the
# case an absolute-shekel threshold waves through.
SLEEVE_SHORTFALL_TOLERANCE_PCT = 1.0

# Marks a `max_weight` rule as armed BY the sleeve system, in the `strategy_id`
# column migration 0013 already added. Only a marked cap may be retired when a
# sleeve goes away -- without it, applying a sleeve disarms every hand-set cap on
# the book, which is exactly what happened the first time C2 ran against a real
# one. A single sentinel rather than the contributing strategy id, because a
# ticker two sleeves both want has two owners and one column.
SLEEVE_OWNED = "sleeve"

# `trading_rules.note` is VARCHAR(160). SQLite does not enforce that and
# Postgres does, so an over-long note is green locally and a 500 in production.
# Mirrored from the model rather than guessed, so the two cannot drift.
MAX_RULE_NOTE = 160


def _pdicts(rows) -> list[dict]:
    # meta is carried deliberately: it holds `price_currency`, without which an
    # ILS-native cash row would be valued as if it traded in USD.
    return [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
             "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
             "meta": p.meta if isinstance(p.meta, dict) else {}} for p in rows]


def _snapshot(rows) -> dict:
    return compute_snapshot(_pdicts(rows)) if rows else {"nav": 0.0, "exposure_ticker": {}}


def _nav(rows) -> float:
    return _snapshot(rows)["nav"] if rows else 0.0


def _cap_note(ticker: str, level: float, sleeve_ids: list[str]) -> str:
    """The explanation stored on a sleeve-armed cap. **Must fit MAX_RULE_NOTE.**

    ``trading_rules.note`` is ``VARCHAR(160)``. SQLite ignores that; Postgres
    raises ``StringDataRightTruncationError`` and the whole apply 500s. The
    first version of the two-sleeve wording came to **161 characters** for
    "Trend-Filtered Leveraged Nasdaq + Volatility-Targeted Leverage" and 187 for
    three sleeves -- green on the local SQLite suite, red on CI's Postgres job,
    and a live 500 waiting for the first book to run two sleeves on one ticker.

    So the long form is *attempted*, and the short form -- whose length does not
    depend on how the catalog names things -- is used when it does not fit. The
    truncation at the end is a backstop, not the plan: a note that has to be
    chopped has already lost the sentence that mattered.
    """
    names = [(strategy_catalog.get(i) or {}).get("name", i) for i in sleeve_ids]
    if len(names) > 1:
        # Say the sum out loud. A 35% cap that is 20 from one sleeve and 15 from
        # another is not a number anyone can reconstruct from the screen.
        tail = (f"want {ticker} at {level:g}% of the book between them. "
                f"One cap at the total, not one per sleeve.")
        # No "your sleeves:" lead-in -- dropping it buys 14 characters, which is
        # the difference between naming both sleeves and falling back to a count
        # for the commonest case (two strategies sharing TQQQ came to 161).
        long_form = f"{' + '.join(names)} {tail}"
        if len(long_form) <= MAX_RULE_NOTE:
            return long_form
        return f"{len(names)} sleeves {tail}"[:MAX_RULE_NOTE]
    who = names[0] if names else "Your sleeve"
    return (f"{who}: the sleeve you chose. Caps {ticker} at {level:g}% of the book "
            f"-- it does not make the rebalancer aim for it.")[:MAX_RULE_NOTE]


async def _arm_sleeve_caps(session: AsyncSession, user: User) -> list[dict]:
    """Arm one ``max_weight`` per ticker, at the SUM of its weight across sleeves.

    C2 changed the unit this works in. It used to take one strategy and arm caps
    from that strategy's targets alone; it now reads every sleeve on the book and
    arms one cap per ticker at their combined size.

    **That is not a tidy-up, it is the P1 duplicate bug pre-empted at N scale.**
    Two sleeves both wanting TQQQ, each arming its own cap, means two
    ``max_weight`` rules on one ticker at two levels, and whichever fires first
    wins. One cap at the sum is the only answer that means anything.

    Anything armed for a ticker no sleeve wants any more is retired here, which
    is how removing a sleeve stops leaving a ceiling behind it.

    Why sleeve caps exist at all: ``as_legacy_strategy`` hardcodes
    ``{"Equities": 1.0}`` for every strategy in this family, so ``sleeve_pct``
    only ever changed the *basket*. Applying at 20% and at 90% wrote an identical
    plan -- the sleeve was a number the app collected and then ignored.
    Asset-class allocation cannot express it either: TQQQ and QQQ are both
    Equities. So the sleeve becomes a rule.

    Idempotent: applying twice re-levels, never stacks.

    **Ownership is MARKED, not assumed** -- ``trading_rules.strategy_id`` carries
    ``SLEEVE_OWNED`` on every cap this system arms, and only a marked cap is ever
    retired.

    The first version of this skipped the marker and retired any active
    ``max_weight`` whose ticker no sleeve wanted. That reads fine until you
    remember people set caps by hand: applying one sleeve silently disarmed
    live caps on V, SCHD and MSFT -- including the 20% MSFT cap the 2026-08-11
    notification fix is built around. Reproduced, not theorised. The docstring
    claimed ownership of "max_weight on a sleeve ticker"; the code claimed every
    max_weight rule there was.

    A hand-set cap on a ticker a sleeve wants is ADOPTED (re-levelled and marked,
    which is what this already did to the level) and reported as ``adopted`` so
    the caller can say so. A hand-set cap on any other ticker is never touched.

    Retired, not deleted -- ``active = False``, history kept, the way P4 retires
    rules on positions no longer held. The return value names every ticker it
    touched and what it did.
    """
    from sqlalchemy import select

    from app.models.tables import TradingRule
    from app.services import sleeve_service as sv

    sleeves = await sv.list_sleeves(session, user)
    targets = await sv.all_sleeve_targets(session, user)
    _live = await sv.live_sleeve_targets(session, user)
    # Which sleeves want each ticker, so a summed cap can say where it came from.
    contributors: dict[str, list[str]] = {}
    for s in sleeves:
        for tk in (sleeve_targets(s.strategy_id, s.sleeve_pct,
                                  live=sv._live_for(_live, s.strategy_id)) or {}):
            contributors.setdefault(tk.upper(), []).append(s.strategy_id)

    existing = {(r.ticker or "").upper(): r for r in (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.rule_type == "max_weight"))).all()}
    out: list[dict] = []
    for tk, weight in sorted(targets.items()):
        tk = tk.upper()
        level = round(float(weight) * 100.0, 1)
        if level <= 0:
            # A 0% cap is breached by any holding at all, so it would fire the
            # instant it is armed. "I want none of this" is a decision to act on,
            # not a rule to spring on someone.
            continue
        note = _cap_note(tk, level, sorted(contributors.get(tk, [])))
        row = existing.get(tk)
        if row is not None:
            was = float(row.level)
            adopting = row.strategy_id != SLEEVE_OWNED
            row.level = float(level)
            row.note = note
            row.active = True
            row.triggered = False
            row.strategy_id = SLEEVE_OWNED
            out.append({"ticker": tk, "level": level, "previous_level": was,
                        "action": ("adopted" if adopting else
                                   "relevelled" if was != level else "unchanged")})
        else:
            session.add(TradingRule(subject=user.email, ticker=tk, rule_type="max_weight",
                                    mode="pct", level=float(level), note=note,
                                    strategy_id=SLEEVE_OWNED))
            out.append({"ticker": tk, "level": level, "previous_level": None,
                        "action": "armed"})

    for tk, row in sorted(existing.items()):
        # ONLY caps this system armed. A cap the user set by hand on a ticker no
        # sleeve wants is none of our business -- see the docstring for what
        # happened the one time this loop did not check.
        if tk in targets or not row.active or row.strategy_id != SLEEVE_OWNED:
            continue
        row.active = False
        row.triggered = False
        out.append({"ticker": tk, "level": float(row.level),
                    "previous_level": float(row.level), "action": "retired"})
    await session.commit()
    return out


async def retire_strategy_rules(session: AsyncSession, user: User,
                                strategy_id: str) -> list[dict]:
    """Deactivate the standing rules a departing strategy armed.

    `_arm_sleeve_caps` already retires the sleeve CAP for a ticker no sleeve
    wants, and its docstring says why: a row removed without re-levelling leaves
    a ceiling on a position now held for some other reason. The stop and the
    take-profit had no such path -- not because anyone decided they should
    survive, but because they could not be IDENTIFIED. `discipline_rules` emitted
    them without a `strategy_id`, `apply` passes that field straight through, and
    they landed in the database with NULL. Nothing could say which sleeve they
    belonged to, so nothing could retire them. Fixed in the same PR that added
    this (59.1); `signal_rules` already stamped its own.

    OWNERSHIP IS MARKED, NOT ASSUMED -- the same rule `_arm_sleeve_caps` learned
    the hard way. Only a rule stamped with THIS strategy id is touched. A stop
    the user set by hand on the same ticker carries NULL and is none of our
    business, and a rule armed by a DIFFERENT sleeve that happens to share the
    ticker keeps its own owner's stamp.

    RETIRED, NOT DELETED: `active = False`, history kept, so the Rules screen can
    still show what was armed and when it stopped.

    Rules armed BEFORE the stamp existed carry NULL and cannot be attributed
    retroactively. They are not guessed at -- this returns them separately as
    `unattributable` so the caller can name them rather than silently leaving
    them armed or silently killing a rule the user set themselves.
    """
    from sqlalchemy import select

    from app.models.tables import TradingRule

    rows = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True)))).all()

    retired, unattributable = [], []
    # THE UNION OF BOTH LEGS, not today's. A sleeve arms caps while it runs, and
    # since PR 63 the leg it targets follows its signal -- so a swing sleeve that
    # has flipped once has armed a cap on the aggressive instrument AND on the
    # base. Retiring it with only today's leg in hand would leave the other one
    # armed with no owner, which is the exact failure PR 59.1 exists to prevent.
    from app.services import sleeve_service as _sv_live
    _sig = _sv_live._live_for(await _sv_live.live_sleeve_targets(session, user),
                              strategy_id)
    wanted = set(sleeve_targets(strategy_id) or {}) | set(
        sleeve_targets(strategy_id, live=_sig) or {})
    for r in rows:
        sid = r.strategy_id
        if sid == strategy_id:
            r.active = False
            r.triggered = False
            retired.append({"ticker": r.ticker, "rule_type": r.rule_type,
                            "level": float(r.level or 0.0), "action": "retired"})
        elif sid is None and (r.ticker or "").upper() in {t.upper() for t in wanted}:
            # Predates the stamp, or hand-set. Named, never touched.
            unattributable.append({"ticker": r.ticker, "rule_type": r.rule_type,
                                   "level": float(r.level or 0.0),
                                   "action": "left armed - no owner recorded"})
    await session.flush()
    return retired + unattributable


async def apply_strategy(session: AsyncSession, user: User, strategy_id: str,
                         sleeve_pct: float | None = None) -> dict:
    """Apply a strategy. For the rule-based family this ADDS a sleeve (C2).

    Applying used to overwrite: one ``plans.strategy``, so choosing a second
    strategy silently dropped the first. It now adds a row alongside whatever is
    already running, and applying the same strategy again re-sizes it rather than
    stacking a duplicate.

    **Over-allocating refuses and writes nothing.** Not "clamps to what fits" --
    a sleeve installed at a size nobody chose is the failure ``_fund_sleeve``
    already abstains over, and clamping would do it silently.

    Two things it no longer does, both deliberate:

    * **A SLEEVE no longer writes ``objective`` or ``risk_tolerance``.** Those set
      the concentration cap and the cash floor for the WHOLE book, so with N
      sleeves "whichever one you applied last decides your guardrails" is not a
      rule anyone would choose. They are yours to set on the Plan tab, and
      existing values are left exactly as they are -- a refactor must not move a
      live book's guardrails as a side effect.

      **A static family still writes them, and that is the point of the
      distinction.** The four static families are model PORTFOLIOS, not sleeves:
      "Grow AI & Semis" is a whole-book allocation and its objective is part of
      what you chose. Only the rule-based sleeves were overwriting guardrails
      they do not govern.
    * **It does not arm a cap per strategy.** ``_arm_sleeve_caps`` arms one per
      ticker at the summed size across every sleeve.
    """
    # Either catalog: static baskets live in `strategies`, rule-based ones in
    # `strategy_catalog`. Adapting rather than forking keeps one apply path.
    s = cat.get(strategy_id) or strategy_catalog.as_legacy_strategy(strategy_id, sleeve_pct)
    if not s:
        return {"ok": False, "error": "unknown strategy"}

    is_sleeve = strategy_catalog.get(strategy_id) is not None
    sleeve_result = None
    core_result = None
    if not is_sleeve:
        # C6: a static family IS a core choice, and now says so. Before this the
        # only trace was `plans.strategy` -- overwritten by the next sleeve you
        # applied, so the book's target mix depended on which card you pressed
        # last. `recommendations` reads the core row instead now.
        from app.services import sleeve_service as sv
        core_result = await sv.set_core(session, user, strategy_id)
        if not core_result.get("ok"):
            return {"ok": False, "strategy_id": strategy_id,
                    "error": core_result["error"], "reason": core_result["reason"]}
    if is_sleeve:
        from app.services import sleeve_service as sv
        pct = s.get("sleeve_pct")
        sleeve_result = await sv.add_or_resize(session, user, strategy_id, float(pct or 0.0))
        if not sleeve_result.get("ok"):
            # Return before the plan is touched, so a refused apply cannot leave
            # the book half-changed.
            #
            # NO session.rollback() HERE. `add_or_resize` validates before it
            # writes, so on a refusal there is nothing to undo -- and rollback
            # EXPIRES every ORM object in the session, including the caller's
            # `user`. The next attribute read then re-queries and blows up. That
            # is the same expiry hazard `_agent_tx` uses a SAVEPOINT to avoid,
            # and it is recorded in CLAUDE.md; a plain rollback here would have
            # reintroduced it on the one path nobody exercises.
            return {"ok": False, "strategy_id": strategy_id,
                    "error": sleeve_result["error"], "reason": sleeve_result["reason"],
                    "allocated_pct": sleeve_result["allocated_pct"]}

    # The legacy columns are still written for one more release: a rollback to
    # C1 must not lose the applied strategy, and everything not yet converted to
    # THE LEGACY POINTER IS GONE. `plans.strategy` + `plans.strategy_sleeve_pct`
    # held the most recently applied strategy -- a pointer, never the truth once
    # a book could run more than one sleeve. C4 removed the last reader and this
    # removes the write. Measured on the live book the day it went: the columns
    # said "btm_swing_dip at 15%" while `plan_sleeves` said 38.4%. Two answers to
    # one question, and the older one was wrong by 23 points.
    #
    # objective / risk_tolerance only for a static family. See the docstring.
    _guardrails = {} if is_sleeve else {"objective": s["objective"],
                                        "risk_tolerance": s["risk_tolerance"]}
    await upsert_plan(session, user, preferred_depth=s.get("preferred_depth"),
                      **_guardrails)
    await session.commit()
    # The sleeve stops being decorative here: it becomes an enforced ceiling.
    caps = await _arm_sleeve_caps(session, user) if is_sleeve else []
    # rebalance trades toward the strategy's target allocation
    rows = await list_positions(session, user)
    nav = _nav(rows)
    actions = []
    if nav > 0:
        mix, _ = current_mix(rows)
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]
    return {"ok": True, "strategy": s, "nav": round(nav, 2), "rebalance_actions": actions,
            "sleeve_caps": caps, "core": core_result,
            # Said out loud for the same reason `sleeve_cap_note` is: applying a
            # core sets the TARGET, it does not trade toward it. The rebalance
            # actions above are advice, and always have been -- nothing here
            # executes them.
            "core_note": (
                "This sets the mix your core is managed to. Nothing was bought or "
                "sold; the rebalance cards on Today move you there one asset class "
                "at a time, each with its own preview and tax estimate."
            ) if core_result and core_result.get("ok") else None,
            # What the book now runs, so the caller never has to infer from the
            # single legacy column that this was additive.
            "sleeve": sleeve_result,
            # Said out loud because it is the honest limit of option (a): the cap
            # stops the sleeve growing past the chosen size, it does not grow the
            # book INTO it. Funding is what does that.
            "sleeve_cap_note": (
                "This caps the sleeve at the size you chose; it does not make the "
                "rebalancer aim for it. Use 'Fund this sleeve' to grow into it."
            ) if caps else None}


async def retire_sleeve(session: AsyncSession, user: User, strategy_id: str) -> dict:
    """Remove a sleeve and put its caps back where they belong.

    The two halves must happen together, which is why this exists rather than
    letting a caller delete the row. Dropping the row alone leaves a live
    ``max_weight`` on a position now held for some other reason -- a ceiling with
    nothing behind it, which is the stale-AMZN-stop shape P4 already had to fix.

    A ticker another sleeve still wants keeps its cap, re-levelled to what the
    remaining sleeves ask for. Only a ticker nobody wants any more is retired,
    and retired means ``active = False`` with the history kept.
    """
    from app.services import sleeve_service as sv

    removed = await sv.remove(session, user, strategy_id)
    if not removed.get("ok"):
        return removed
    # Rules BEFORE caps: `_arm_sleeve_caps` commits, and a stop left armed by a
    # commit that succeeded is exactly the ceiling-with-nothing-behind-it this
    # function was written to prevent, one rule type over. PR 59.1.
    rules = await retire_strategy_rules(session, user, strategy_id)
    caps = await _arm_sleeve_caps(session, user)

    return {**removed, "sleeve_caps": caps,
            "retired_caps": [c["ticker"] for c in caps if c["action"] == "retired"],
            "retired_rules": rules,
            "broker_note": BROKER_NOTE}


async def swap_sleeve(session: AsyncSession, user: User, *,
                      from_id: str, to_id: str) -> dict:
    """Replace one sleeve with another at the SAME size, in one operation.

    B2 is the reason this exists. Eran's decision was "retire the overlay, hold
    the base" -- drop the rule steering `btm_swing_dip`, keep the 36% position --
    and the app had no way to express it. `retire_sleeve` retires the whole
    SLEEVE: the 36% collapses into the core, the core goes 27% -> ~55%, and the
    book's Fixed Income target swings 7.8% -> ~22%, so the app starts asking to
    sell equities into BND. Dropping a rule and dropping an allocation were the
    same operation, and only one of them was ever wanted.

    ORDER IS NOT COSMETIC. `validate` measures the new sleeve against the free
    share of the book, and the old sleeve still occupies its own. Adding first
    would put this book at 109% and be refused; removing first frees exactly the
    space the replacement needs. Doing it as two API calls leaves a window where
    the book is under-allocated and the core is oversized -- harmless for a few
    seconds, except that anything reading the book in that window (a refresh, a
    card, the 06:15 job) sees an allocation nobody chose. One function, one
    flush.

    CAPS RE-LEVEL ONCE, AT THE END, for the reason `retire_sleeve`'s docstring
    gives: a row removed without re-levelling leaves a live `max_weight` on a
    position now held for some other reason -- a ceiling with nothing behind it.
    Re-levelling between the two halves would arm caps against a book that
    exists for one statement.

    **NOTHING IS BOUGHT OR SOLD.** This changes what the app steers toward, not
    what you hold. After it, the old sleeve's ticker is a holding no sleeve
    wants and the new one's is unfunded -- both of which the existing cards
    already know how to say. Claiming otherwise here would be the exact failure
    `test_card_claims` exists to catch.
    """
    from app.services import sleeve_service as sv

    from_id = (from_id or "").strip()
    to_id = (to_id or "").strip()

    # Checked HERE and not in `validate`, which only ever asked about size and
    # duplication -- so a typo'd id would have been accepted as a sleeve for a
    # strategy that does not exist, sized, capped and funded against nothing.
    if strategy_catalog.get(to_id) is None and not cat.get(to_id):
        return {"ok": False, "error": f"'{to_id}' is not a strategy in the catalogue"}

    sleeves = await sv.list_sleeves(session, user)
    src = next((x for x in sleeves if x.strategy_id == from_id), None)
    if src is None:
        return {"ok": False, "error": f"'{from_id}' is not a sleeve on this book"}
    if any(x.strategy_id == to_id for x in sleeves):
        # A merge, not a swap: two sizes for one strategy, and no honest way to
        # pick which survives. The caller can resize then retire, deliberately.
        return {"ok": False, "error": f"'{to_id}' is already a sleeve on this book",
                "reason": ("swapping into it would merge two sleeves at two sizes. "
                           "Resize that one and retire this one instead, so the "
                           "size you end up with is one you chose.")}

    pct = float(src.sleeve_pct or 0.0)
    removed = await sv.remove(session, user, from_id)
    if not removed.get("ok"):
        return removed
    added = await sv.add_or_resize(session, user, to_id, pct)
    if not added.get("ok"):
        # `remove` only flushed; nothing is committed yet, so the caller's
        # rollback puts the old sleeve back. Returning the refusal unchanged
        # keeps the reason `validate` gave rather than inventing a new one.
        return {**added, "swap_aborted": True, "from_id": from_id, "to_id": to_id}

    # The departing strategy's standing rules go with it. Its stop and
    # take-profit were sized from ITS measured volatility and drawdown; left
    # armed after a swap they are levels derived from a rule the book no longer
    # runs, sitting on a ticker no sleeve wants. PR 59.1.
    rules = await retire_strategy_rules(session, user, from_id)
    caps = await _arm_sleeve_caps(session, user)
    after = await sv.list_sleeves(session, user)
    _live_sw = await sv.live_sleeve_targets(session, user)
    _live_from = sv._live_for(_live_sw, from_id)
    _live_to = sv._live_for(_live_sw, to_id)
    return {
        "ok": True, "swapped": True, "from_id": from_id, "to_id": to_id,
        "retired_rules": rules,
        "sleeve_pct": pct,
        # What each sleeve actually targets, signal included -- the swap note is
        # read as "you held these, you now hold those", and the catalogue's
        # static leg is not what a swing sleeve was holding if its rule was off.
        "from_tickers": sorted(sleeve_targets(from_id, pct, live=_live_from)),
        "to_tickers": sorted(sleeve_targets(to_id, pct, live=_live_to)),
        "sleeve_caps": caps,
        "retired_caps": [c["ticker"] for c in caps if c["action"] == "retired"],
        "allocated_pct": sv.total_pct(after), "core_pct": sv.remainder_pct(after),
        "broker_note": BROKER_NOTE,
    }


# --------------------------------------------------------------------------- #
# Which tickers ARE the sleeve
# --------------------------------------------------------------------------- #
def sleeve_targets(strategy_id: str, sleeve_pct: float | None = None,
                   *, live: dict | None = None) -> dict[str, float]:
    """Target weight of NAV per ticker for the part that must actually be bought.

    For a rule-based strategy the sleeve is the *aggressive* leg — the core
    (``base``) is what the rest of the book already is, so funding it would mean
    selling your holdings to buy an index fund you never asked for. For a static
    basket every leg is the target, because there the basket IS the portfolio.

    ``live`` IS WHAT THE RULE CURRENTLY WANTS, AND IT WINS.

    Eran, 2026-09-06, on a swing sleeve sitting in TQQQ while its rule said to
    step back: *"the swing should be a card that replace the TQQQ with QQQ
    automatically."* Until now this function could not express that. It read the
    catalogue's STATIC weights, so a sleeve's target was the aggressive
    instrument on every day of the year — including the ~85% of days
    ``btm_swing_dip`` is out of its setup. The Plan tab, every drift card, the
    funding engine and the armed caps all described a position the rule was not
    asking for, and nothing in the app could tell the difference between "the
    rule wants TQQQ" and "the rule has never been consulted".

    So when a stored signal exists, its target replaces the catalogue's leg
    outright — including when that target IS the base. The docstring above says
    funding the base means "buying an index fund you never asked for"; that is
    true of the catalogue default and false here, because you asked for it by
    choosing a strategy whose spec says the money sits in the base between
    setups. ``strategy_catalog`` says so in its own words: *"``base`` is the core
    holding, not cash. A swing setup is live perhaps 10-15% of the time."*

    Pure and synchronous on purpose. The stored signal is loaded once per request
    by ``sleeve_service.live_sleeve_targets`` and passed down, rather than making
    this async and dragging a session through ``card_scope.scope_for`` — which is
    documented as pure "so the mapping is testable without a book".

    Falls back to the catalogue when there is no stored signal. It does NOT fall
    back on a STALE one: see ``live_sleeve_targets`` for why reverting to the
    catalogue is the dangerous direction.
    """
    entry = strategy_catalog.get(strategy_id)
    if entry is not None:
        pct = sleeve_pct if sleeve_pct is not None else entry.get("sleeve_pct")
        frac = 1.0 if pct is None else max(0.0, min(1.0, float(pct) / 100.0))

        want = {str(tk).upper(): float(w)
                for tk, w in (live or {}).items() if float(w or 0) > 0}
        if not want:
            weights = entry.get("weights") or {}
            base = entry.get("base") or {}
            want = {tk: float(w) for tk, w in weights.items() if tk not in base}
        if not want:                             # nothing to fund beyond the core
            return {}
        total_w = sum(want.values()) or 1.0
        return {tk: (w / total_w) * frac for tk, w in want.items()}
    s = cat.get(strategy_id)
    if not s:
        return {}
    return {tk: float(w) for tk, w in (s.get("basket") or [])}


def _held_ils(rows, snap) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in rows:
        tk = (p.ticker or "").upper()
        if is_cash_position(tk, p.meta if isinstance(p.meta, dict) else None):
            continue
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = fx_rate(price_currency(p.market, meta))
        out[tk] = out.get(tk, 0.0) + float(p.quantity) * float(p.current_price or 0.0) * rate
    return out


def _default_mode(strategy_id: str) -> str:
    # Rule-based strategies are sleeves; static families are model portfolios.
    return FUND if strategy_catalog.get(strategy_id) is not None else REPLACE


# --------------------------------------------------------------------------- #
# load_basket
# --------------------------------------------------------------------------- #
async def _held_back_tickers(session: AsyncSession, user: User, rows) -> set[str]:
    """PR 75: tickers under an active take-profit buy-back hold. A card on Today
    must not buy them back; a buy the user starts on the Plan screen may."""
    from app.services import buyback_hold as bh
    try:
        return set(bh.active(await bh.load(session, user), rows))
    except Exception:  # noqa: BLE001
        return set()


async def load_basket(session: AsyncSession, user: User, strategy_id: str,
                      total: float | None = None, sleeve_pct: float | None = None,
                      mode: str | None = None, dry_run: bool = False,
                      honour_holds: bool = False) -> dict:
    if sleeve_pct is None:
        # C3: size from the sleeve ROW. The old fallback read
        # plans.strategy_sleeve_pct, and only when plans.strategy happened to
        # name this strategy -- so on a book running two sleeves, funding the one
        # that was not applied most recently silently fell through to the
        # catalog default instead of the size you chose.
        from app.services import sleeve_service as sv
        row = next((x for x in await sv.list_sleeves(session, user)
                    if x.strategy_id == strategy_id), None)
        if row is not None:
            sleeve_pct = float(row.sleeve_pct)
    s = cat.get(strategy_id) or strategy_catalog.as_legacy_strategy(strategy_id, sleeve_pct)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    mode = (mode or _default_mode(strategy_id)).lower()
    if mode not in (FUND, REPLACE):
        return {"ok": False, "error": f"unknown mode '{mode}'"}
    if mode == FUND:
        return await _fund_sleeve(session, user, strategy_id, s, total=total,
                                  sleeve_pct=sleeve_pct, dry_run=dry_run,
                                  honour_holds=honour_holds)
    return await _replace_book(session, user, strategy_id, s, total=total, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# mode="fund"
# --------------------------------------------------------------------------- #
def _legs_for(targets: dict[str, float], nav: float, held: dict[str, float],
              *, total: float | None = None) -> list[dict]:
    """What to buy, per ticker, to bring a sleeve up to its target.

    Extracted so single-sleeve and whole-plan funding size legs with the SAME
    code. Two implementations of "how much of this do I need" would eventually
    disagree, and the one that disagreed would be whichever the user happened to
    press -- the duplicated-reprice-loop shape.

    A ``total`` override means "spend exactly this much", split across the sleeve
    by its own weights. Otherwise the sleeve is sized as a share of NAV and only
    the shortfall against what is already held is bought.
    """
    legs: list[dict] = []
    weight_sum = sum(targets.values()) or 1.0
    for tk, w in sorted(targets.items()):
        tk = tk.upper()
        if total and total > 0:
            want = float(total) * (w / weight_sum)
            short = want
        else:
            want = nav * w
            short = want - held.get(tk, 0.0)
        if short < min_trade_ils():
            continue
        legs.append({"ticker": tk, "target_ils": round(want, 2),
                     "held_ils": round(held.get(tk, 0.0), 2),
                     "buy_ils": round(short, 2),
                     "target_pct": round(w * 100, 1)})
    return legs


async def _fund_sleeve(session: AsyncSession, user: User, strategy_id: str, s: dict,
                       *, total: float | None, sleeve_pct: float | None,
                       dry_run: bool, honour_holds: bool = False) -> dict:
    from app.services import sleeve_service as _sv_sig
    targets = sleeve_targets(
        strategy_id, sleeve_pct,
        live=_sv_sig._live_for(await _sv_sig.live_sleeve_targets(session, user),
                               strategy_id))
    if not targets:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id,
                "error": "this strategy has no sleeve to fund — its basket is the core itself"}

    rows = await list_positions(session, user)
    if honour_holds:
        _hb = await _held_back_tickers(session, user, rows)
        targets = {k: v for k, v in targets.items() if k.upper() not in _hb}
        if not targets:
            return {"ok": False, "mode": FUND, "strategy_id": strategy_id,
                    "error": "every ticker in this sleeve is on a take-profit buy-back hold"}
    snap = _snapshot(rows)
    nav = snap.get("nav") or 0.0
    if nav <= 0:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id,
                "error": "no tracked holdings to size a sleeve against — add holdings or cash first"}
    plan = await get_plan(session, user)
    objective = plan.objective if plan else "Grow"
    cap = effective_caps(plan)["concentration_cap"]
    cash_ils = await get_cash(session, user)
    held = _held_ils(rows, snap)

    legs = _legs_for(targets, nav, held, total=total)
    amount = round(sum(x["buy_ils"] for x in legs), 2)
    if amount < min_trade_ils():
        return {"ok": True, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
                "nothing_to_do": True, "buys": [], "funding": None,
                "message": (f"Your book already holds the sleeve at roughly its "
                            f"{', '.join(f'{k} {v * 100:.0f}%' for k, v in sorted(targets.items()))} "
                            f"target — nothing to buy.")}

    # EVERY sleeve's tickers are off-limits as a funding source, not just this
    # one's. Once a book can run two sleeves, `exclude=set(targets)` means
    # funding Factor Stack can sell the SOXL sleeve to pay for it -- the plan
    # holds both on purpose, and one of them is not a piggy bank for the other.
    # (Pulled forward from C3: it stopped being deferrable the moment C2 made
    # apply additive.)
    from app.services import sleeve_service as sv
    exclude = set(targets) | await sv.sleeve_tickers(session, user)
    # THE SLEEVE'S OWN CLASS, so a same-class swap is allowed to fund it.
    #
    # Without this the class-overweight budget gates every sale, and on an
    # equity sleeve funded out of equities that budget is the wrong constraint
    # entirely: the proceeds come straight back, so the class weight does not
    # move and there is nothing for the budget to protect. It is why a 64%
    # target sat at 15% held while funding abstained -- see plan_funding.
    buying_class = _buying_class([{"ticker": tk} for tk in targets])
    # THE POSITION THIS MOVE CONVERTS, SOLD FIRST.
    #
    # Funding is ranked on fit and tax, which is the right question when paying
    # for a purchase out of whatever the book can best spare, and the wrong one
    # when the purchase IS a conversion. Live on 2026-09-06, funding a 33% QQQ
    # sleeve on a book holding TQQQ: the ranking chose VTI 2,925 alongside TQQQ
    # 4,145, because Equities was the most overweight class and VTI carried the
    # smaller gain. Both true, and the plan still sold core money doing its job
    # while the sleeve's funding sat in the leveraged position the sleeve
    # replaces. `steps_back_to` is the same relationship the Today card uses to
    # decide it may act in one tap -- one rule, two callers, so the card cannot
    # be offered on a plan the funder would not build. PR 61.1.
    from app.services import strategy_catalog as _cat
    prefer = {tk for tk in held
              if tk not in exclude
              and any(_cat.steps_back_to(tk, want) for want in targets)}
    fund = plan_funding(rows, snap, plan, objective, cap, amount,
                        cash_ils=cash_ils, exclude=exclude,
                        buying_class=buying_class, prefer=prefer)

    # Abstain rather than half-execute: a partially funded sleeve is a position
    # nobody chose, at a size nobody chose. Judged in points of NAV, not shekels
    # -- see SLEEVE_SHORTFALL_TOLERANCE_PCT.
    held_sleeve = sum(held.get(tk.upper(), 0.0) for tk in targets)
    wanted_ils = held_sleeve + amount
    achievable_ils = held_sleeve + float(fund.get("funded_ils") or 0.0)
    chosen_pct = wanted_ils / nav * 100.0
    achievable_pct = achievable_ils / nav * 100.0
    short_points = chosen_pct - achievable_pct
    if short_points >= SLEEVE_SHORTFALL_TOLERANCE_PCT:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
                "error": "not enough fundable money for this sleeve",
                # Actionable, not just "no": it names the sleeve that WOULD work.
                "reason": (f"You asked for a {chosen_pct:.0f}% sleeve (₪{wanted_ils:,.0f}). "
                           f"The most this book can fund right now is {achievable_pct:.0f}% "
                           f"— ₪{fund['shortfall_ils']:,.0f} short, after spending "
                           f"₪{fund['from_cash_ils']:,.0f} of cash above your "
                           f"{fund['cash_floor_pct']:.0%} {objective} floor and trimming what "
                           f"the plan says is overweight. Lower the sleeve to about "
                           f"{achievable_pct:.0f}%, or add cash."),
                "chosen_sleeve_pct": round(chosen_pct, 1),
                "achievable_sleeve_pct": round(achievable_pct, 1),
                "buys": legs, "funding": fund,
                "funding_summary": describe_funding(fund, _buying_class(legs))}

    preview = {"ok": True, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
               "sleeve_pct": sleeve_pct, "amount_ils": amount,
               "chosen_sleeve_pct": round(chosen_pct, 1),
               "achievable_sleeve_pct": round(achievable_pct, 1),
               "buys": legs, "funding": fund,
               "funding_summary": describe_funding(fund, _buying_class(legs)),
               "broker_note": BROKER_NOTE}
    if dry_run:
        return {**preview, "dry_run": True}

    executed = await _execute_funded_sleeve(session, user, legs, fund)
    return {**preview, "dry_run": False, **executed}


# --------------------------------------------------------------------------- #
# Whole-plan funding (C3)
# --------------------------------------------------------------------------- #
async def fund_plan(session: AsyncSession, user: User, *, dry_run: bool = True,
                    honour_holds: bool = False) -> dict:
    """Fund every under-funded sleeve on the book in one pass.

    **Why one pass rather than calling ``_fund_sleeve`` N times.** Each call
    builds its own funding plan from the same cash and the same trim candidates.
    Run twice, and both would plan to spend the cash above your floor and both
    would plan to trim the same shares -- the money counted once by the book and
    twice by the app. One shared plan is the only version that can be right.

    **Largest sleeve first when the money will not stretch.** Each sleeve is
    funded to the size you chose or skipped entirely -- never installed at a size
    nobody picked, which is what the single-sleeve path already abstains over.
    The order is a preference, not a result: it honours the conviction your
    sleeve sizes encode, and it sells less than the alternative, because what a
    sleeve does not claim simply stays in the objective-managed core rather than
    sitting idle. The cost, said plainly: your largest sleeve is also the one
    least likely to fit, and when it does not, the smaller ones still get a turn.

    **Capacity is asked of the funding engine, never recomputed.** Whether a
    sleeve fits is decided by calling ``plan_funding`` for the running total and
    reading its shortfall. A separate "how much could this book raise" sum would
    be a second implementation of the same question, free to disagree with the
    one that actually does the work.
    """
    from app.services import sleeve_service as sv

    sleeves = await sv.list_sleeves(session, user)
    if not sleeves:
        return {"ok": False, "mode": FUND_PLAN,
                "error": "no sleeves on this book — apply a strategy first"}

    rows = await list_positions(session, user)
    snap = _snapshot(rows)
    nav = snap.get("nav") or 0.0
    if nav <= 0:
        return {"ok": False, "mode": FUND_PLAN,
                "error": "no tracked holdings to size sleeves against — add holdings or cash first"}

    plan = await get_plan(session, user)
    objective = plan.objective if plan else "Grow"
    cap = effective_caps(plan)["concentration_cap"]
    cash_ils = await get_cash(session, user)
    held = _held_ils(rows, snap)
    _hb_plan = await _held_back_tickers(session, user, rows) if honour_holds else set()
    # No sleeve is a funding source for any other, including itself.
    exclude = await sv.sleeve_tickers(session, user)

    # Same reasoning as _fund_sleeve: when every sleeve leg lands in one class,
    # selling that class to buy it back is a swap, not a rebalance, and the
    # class-overweight budget must not gate it. Mixed-class sleeves get None and
    # the original behaviour.
    # NOT named `_buying_class`: that is already the module-level alias for
    # `buying_class_of`, and shadowing it here made every later call in this
    # function a "str object is not callable" -- 18 tests, one letter apart.
    _live_caps = await sv.live_sleeve_targets(session, user)
    _all_targets: dict[str, float] = {}
    for _s in sleeves:
        for _tk, _w in (sleeve_targets(_s.strategy_id, _s.sleeve_pct,
                                       live=sv._live_for(_live_caps, _s.strategy_id))
                        or {}).items():
            _all_targets[_tk.upper()] = _all_targets.get(_tk.upper(), 0.0) + float(_w)
    _sleeve_class = _buying_class([{"ticker": tk} for tk in _all_targets])

    def _fits(total_ils: float) -> dict:
        return plan_funding(rows, snap, plan, objective, cap, total_ils,
                            cash_ils=cash_ils, exclude=exclude,
                            buying_class=_sleeve_class)

    # Largest first. Ties broken by strategy id so the order is deterministic --
    # a funding plan that changes between two identical previews is untrustworthy
    # even when both are correct.
    ordered = sorted(sleeves, key=lambda x: (-float(x.sleeve_pct or 0.0), x.strategy_id))

    out: list[dict] = []
    running = 0.0
    for row in ordered:
        sid = row.strategy_id
        pct = float(row.sleeve_pct or 0.0)
        targets = sleeve_targets(sid, pct, live=sv._live_for(_live_caps, sid))
        if honour_holds and targets:
            targets = {k: v for k, v in targets.items() if k.upper() not in _hb_plan}
        entry = {"strategy_id": sid, "sleeve_pct": pct,
                 "name": (strategy_catalog.get(sid) or {}).get("name", sid)}
        if not targets:
            out.append({**entry, "status": "not_a_sleeve", "amount_ils": 0.0,
                        "reason": "this strategy's basket is the core itself — nothing to fund"})
            continue
        legs = _legs_for(targets, nav, held)
        amount = round(sum(x["buy_ils"] for x in legs), 2)
        if amount < min_trade_ils():
            # Two different situations wore one label until C4.
            #
            # A sleeve AT its target has nothing to do. A sleeve still short by
            # less than one tradeable lot ALSO produces no legs -- `_legs_for`
            # drops anything under MIN_TRADE_ILS -- but it is not at its target
            # and never will be. After C3b executed, the factor sleeve sat 132
            # short with per-leg gaps of ~33/53/46; the preview said "intended
            # 18.0%, would end at 17.4%" and, two lines down, "every sleeve fits".
            # Both true, and together they contradict.
            gap_ils = round(sum(nav * w - held.get(tk.upper(), 0.0)
                                for tk, w in targets.items()), 2)
            if gap_ils >= min_trade_ils() / 10.0:
                out.append({**entry, "status": "as_close_as_a_lot_allows",
                            "amount_ils": 0.0, "buys": [], "shortfall_ils": gap_ils,
                            "reason": (f"₪{gap_ils:,.0f} under its target, but no single leg "
                                       f"reaches the ₪{min_trade_ils():,.0f} minimum trade — "
                                       f"this is as close as a whole lot gets")})
            else:
                out.append({**entry, "status": "nothing_to_do", "amount_ils": 0.0, "buys": [],
                            "reason": "already held at roughly its target"})
            continue

        trial = _fits(round(running + amount, 2))
        trial_short = float(trial.get("shortfall_ils") or 0.0)
        # Judged in POINTS OF NAV, matching the single-sleeve rule, and the
        # detour that got here is worth recording.
        #
        # A stricter "the plan must be payable to the shekel" was tried first,
        # after a probe showed two sleeves reported funded with a 621 shekel hole
        # in the plan. But probing further showed the residual is STRUCTURAL:
        # `plan_funding` sells whole shares and nets capital-gains tax out of the
        # proceeds, so it essentially always lands a little short of the exact
        # figure -- 339 shekels on a 5,765 sleeve, from one share of rounding
        # plus tax. A shekel-exact rule rejects almost everything, which is
        # precisely why the single-sleeve path judges in points of NAV.
        #
        # The residual was never the real hazard. The hazard was that
        # `_execute_funded_sleeve` lets the LAST leg absorb all of it and drops
        # that leg entirely if what is left falls below MIN_TRADE_ILS -- so one
        # arbitrary sleeve loses a whole position. That is fixed where it lives,
        # by scaling every leg proportionally, rather than by refusing to fund.
        if trial_short / nav * 100.0 >= SLEEVE_SHORTFALL_TOLERANCE_PCT:
            out.append({**entry, "status": "skipped", "amount_ils": amount, "buys": legs,
                        "shortfall_ils": round(trial_short, 2),
                        "reason": (f"needs ₪{amount:,.0f} and the book is "
                                   f"₪{trial_short:,.0f} short of "
                                   f"funding it on top of the sleeves above it")})
            continue
        running = round(running + amount, 2)
        out.append({**entry, "status": "funded", "amount_ils": amount, "buys": legs})

    funded = [x for x in out if x["status"] == "funded"]
    fund = _fits(running) if running >= min_trade_ils() else None
    # Read off the plan that will actually run, not the trial that accepted the
    # last sleeve, and reported rather than buried: whole-share rounding and tax
    # mean this is normally small but rarely zero.
    residual = float((fund or {}).get("shortfall_ils") or 0.0)

    # Intended vs resulting, stated rather than left to be worked out. A pass
    # that funds two of three sleeves is not a success and must not read as one.
    intended_pct = round(sum(float(s.sleeve_pct or 0.0) for s in sleeves), 1)
    held_sleeve_ils = sum(held.get(tk, 0.0) for tk in exclude)
    resulting_pct = round((held_sleeve_ils + running) / nav * 100.0, 1)

    result = {
        "ok": True, "mode": FUND_PLAN, "nav": round(nav, 2),
        "sleeves": out,
        "amount_ils": running,
        "intended_sleeve_pct": intended_pct,
        "resulting_sleeve_pct": resulting_pct,
        # "Fully funded" has to mean the book ends where it was asked to. A
        # sleeve that is as close as a lot allows is a good outcome and still
        # not a complete one.
        "fully_funded": (all(x["status"] in ("funded", "nothing_to_do") for x in out)
                         and residual < min_trade_ils()
                         and abs(resulting_pct - intended_pct) < 0.05),
        "plan_shortfall_ils": round(residual, 2),
        "funding": fund,
        "funding_summary": (describe_funding(
            fund, _buying_class([lg for x in funded for lg in x["buys"]]))
            if fund else "Nothing to fund."),
        "broker_note": BROKER_NOTE,
    }
    if not result["fully_funded"]:
        skipped = [x["name"] for x in out if x["status"] == "skipped"]
        lot_bound = [x["name"] for x in out if x["status"] == "as_close_as_a_lot_allows"]
        result["message"] = (
            f"This would leave the book at {resulting_pct:g}% in sleeves against the "
            f"{intended_pct:g}% you chose."
            + (f" Not funded: {', '.join(skipped)}." if skipped else "")
            + (f" As close as a whole lot allows: {', '.join(lot_bound)} — the remaining"
               f" gap is smaller than the minimum trade, so it will stay there."
               if lot_bound else "")
            + (f" The plan is still ₪{residual:,.0f} short even so — that is a bug,"
               f" not a choice; do not execute it." if residual >= min_trade_ils() else "")
            + " What the sleeves do not claim stays in the core.")

    if dry_run:
        return {**result, "dry_run": True}

    # C3a ships the sizing and the preview; execution of the MULTI-SLEEVE path
    # stays off until the numbers have been read against a real book. The
    # single-sleeve path is untouched and still executes -- it has been live
    # since P0.1.
    if not PLAN_FUNDING_EXECUTION:
        return {**result, "ok": False, "dry_run": True,
                "error": "whole-plan funding is preview-only in this release",
                "reason": ("The sizing and the funding plan are real and shown above. "
                           "Executing several sleeves against one budget is held back "
                           "until those numbers have been checked against this book. "
                           "Fund a single sleeve to execute today.")}

    if not funded or fund is None:
        return {**result, "dry_run": False, "sold": [], "bought": [], "skipped": []}

    # **Spread the residual across every leg instead of letting the last one
    # wear it.** `_execute_funded_sleeve` walks the legs spending
    # min(want, budget) and skips any leg whose share drops below
    # MIN_TRADE_ILS -- so an unscaled list hands the entire shortfall to
    # whichever leg happens to be last, and can drop it outright. One sleeve
    # would then quietly end up smaller, or missing a position, after this
    # function reported it funded.
    #
    # Scaling proportionally keeps every sleeve's composition intact and leaves
    # them all a hair under the chosen size, which is the same "close to what you
    # asked for" the single-sleeve tolerance already accepts -- applied evenly
    # rather than dumped on an arbitrary ticker.
    payable = float(fund.get("funded_ils") or 0.0)
    scale = min(1.0, payable / running) if running > 0 else 1.0
    all_legs = [{**leg, "buy_ils": round(float(leg["buy_ils"]) * scale, 2)}
                for x in funded for leg in x["buys"]]
    executed = await _execute_funded_sleeve(session, user, all_legs, fund)
    return {**result, "dry_run": False, "leg_scale": round(scale, 6), **executed}


async def _execute_funded_sleeve(session: AsyncSession, user: User,
                                 legs: list[dict], fund: dict) -> dict:
    """Sell the named legs, then buy the sleeve. Nothing else is touched."""
    rows = await list_positions(session, user)
    by_ticker = {(p.ticker or "").upper(): p for p in rows}
    sold, raised, tax_total = [], 0.0, 0.0
    for leg in fund.get("sells") or []:
        p = by_ticker.get((leg.get("ticker") or "").upper())
        if p is None:
            continue
        shares = min(float(leg.get("shares") or 0), float(p.quantity))
        if shares <= 0:
            continue
        from app.core.config import get_settings
        rate = fx_rate(price_currency(p.market, p.meta if isinstance(p.meta, dict) else {}))
        price, basis = float(p.current_price or 0), float(p.cost_basis or 0)
        gross = shares * price * rate
        tax = max(0.0, (price - basis) * shares * rate) * float(get_settings().cgt_rate)
        remaining = float(p.quantity) - shares
        if remaining <= 0:
            await delete_position(session, user, p.ticker, p.market)
        else:
            p.quantity = money(remaining)
        raised += max(0.0, gross - tax)
        tax_total += tax
        sold.append({"ticker": p.ticker, "shares": round(shares, 6),
                     "value_ils": round(gross, 2), "tax_ils": round(tax, 2),
                     "reason": leg.get("reason")})
    await session.flush()

    # Spend only cash that exists. `from_cash_ils` was planned against the book as
    # it was when the preview ran; clamping the debit at zero afterwards would fund
    # a purchase out of nothing.
    cash_now = await get_cash(session, user)
    from_cash = round(min(float(fund.get("from_cash_ils") or 0.0), max(0.0, cash_now)), 2)
    budget = round(from_cash + raised, 2)

    bought, skipped = [], []
    for leg in legs:
        tk, want = leg["ticker"], float(leg["buy_ils"])
        spend = min(want, budget)
        if spend < min_trade_ils():
            skipped.append({"ticker": tk, "reason": "not enough funded money left"})
            continue
        try:
            q = await offload(guarded_quote, tk)
        except Exception:  # noqa: BLE001
            q = None
        price = float(getattr(q, "price", 0) or 0) if q is not None else 0.0
        if price <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        # PR 105: major units first -- a `.TA` quote arrives in agorot.
        price, _ccy, price_ils = quote_in_ils(price, getattr(q, "currency", None) or "USD")
        if price_ils <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        shares = spend / price_ils
        is_new = await buy_into_book(session, user, ticker=tk,
                                     market=getattr(q, "market", None) or "NASDAQ",
                                     price=price, shares=shares, depth=3)
        budget = round(budget - spend, 2)
        bought.append({"ticker": tk, "shares": round(shares, 6),
                       "amount_ils": round(spend, 2), "price": price,
                       "new_position": is_new})
    await session.flush()

    # Cash pays only its own share; the sale proceeds went straight into the buy.
    # Anything left unspent (an unpriceable leg) is credited back rather than
    # evaporating -- money that vanishes is the bug class this whole phase exists
    # to remove.
    leftover = round(max(0.0, budget), 2)
    cash_after = round(cash_now - from_cash + leftover, 2)
    await set_cash(session, user, cash_after)

    # A HOLE IN THE LEDGER, CLOSED. `ledger_service` states the invariant in its
    # own docstring -- "if a write path can move a position and does not call
    # ``record_leg``, that path is a hole in the ledger" -- and this path moved
    # the book without one for its entire life. It is `/plan/sleeves/fund`, and
    # on 2026-09-06 alone it ran five times.
    #
    # The cost was not a missing audit trail. `nav_history` cannot see a swap it
    # has no legs for, so it read "the position count changed and the
    # contributions ledger is silent" as money possibly moving, estimated the
    # difference against a reconstruction, and then reported that
    # reconstruction's own error term to Eran as deposits and withdrawals:
    # +132.11 on 08-31 and -340.67 on 09-01, captioned as -208.56 ILS of "money
    # you moved in or out" on a book where nothing moved in or out.
    from app.services import ledger_service as ledger
    await ledger.record_legs(session, user, sold=sold, bought=bought,
                             source="sleeve_fund",
                             note="sleeve funded -- internal reallocation")
    await session.commit()
    return {"sold": sold, "bought": bought, "skipped": skipped, "ledger_recorded": True,
            "tax_ils": round(tax_total, 2), "unspent_ils": leftover,
            "from_cash_ils": from_cash, "cash_ils": cash_after}


# --------------------------------------------------------------------------- #
# mode="replace"
# --------------------------------------------------------------------------- #
async def _replace_book(session: AsyncSession, user: User, strategy_id: str, s: dict,
                        *, total: float | None, dry_run: bool) -> dict:
    # REFUSED ON A BOOK RUNNING SLEEVES. C6.
    #
    # This deletes every holding and inserts the basket. It is entirely
    # sleeve-unaware, so on a two-sleeve book it sells SOXL, MTUM, QUAL and AVUV
    # and leaves the plan_sleeves rows pointing at positions that no longer
    # exist -- targets with nothing behind them, the stale-cap shape `retire_
    # sleeve` was written to prevent, at book scale.
    #
    # Phase C built sleeves across five sub-phases and never came back to this
    # button. It is refused rather than made sleeve-aware because "replace the
    # whole book" has no sleeve-aware meaning: the sleeves ARE part of the book,
    # and a version that spared them would not be a replacement. The thing a user
    # actually wants here now exists -- set the family as your core, and let the
    # rebalance cards move you there.
    from app.services import sleeve_service as sv
    sleeves = await sv.list_sleeves(session, user)
    if sleeves:
        names = ", ".join(x.strategy_id for x in sleeves)
        return {"ok": False, "mode": REPLACE, "strategy_id": strategy_id,
                "error": "replacing the book would sell your sleeves",
                "reason": (f"this sells every holding and buys the basket, including the "
                           f"positions your {len(sleeves)} sleeve(s) hold ({names}). "
                           f"Make this your core instead — it sets the mix the rest of "
                           f"the book is managed to and sells nothing."),
                "sleeves": [x.strategy_id for x in sleeves]}

    rows = await list_positions(session, user)
    snap = _snapshot(rows)
    budget = total if (total and total > 0) else ((snap.get("nav") or 0.0) or 10000.0)

    # What replacement would destroy, named and valued, so a confirm dialog can
    # show it rather than saying "your current holdings".
    held = _held_ils(rows, snap)
    removing = sorted(({"ticker": tk, "value_ils": round(v, 2)} for tk, v in held.items()),
                      key=lambda x: -x["value_ils"])

    # price the basket, then size by weight / price
    positions: list[IntakePosition] = []
    priced = []
    for ticker, weight in s["basket"]:
        try:
            q = await offload(guarded_quote, ticker)
            # PR 105 (C3+): `budget` is shekels and the quote is in the ticker's
            # own currency. `weight * budget / price` divided one by the other,
            # so replacing a ₪37,100 book with a USD basket booked ₪137,641.
            price, _ccy, price_ils = quote_in_ils(float(q.price), getattr(q, "currency", None) or "USD")
        except Exception:  # noqa: BLE001
            q, price, price_ils = None, 0.0, 0.0
        if price <= 0 or price_ils <= 0:
            continue
        qty = (weight * budget) / price_ils
        _mk = getattr(q, "market", None)
        mk = Market(_mk) if _mk in {m.value for m in Market} else Market.NASDAQ
        positions.append(IntakePosition(
            ticker=ticker, market=mk, depth=s.get("preferred_depth") or 2,
            spot_price=price, listing_price=price, quantity=qty, cost_basis=price,
            asset_class=cat.ticker_asset_class(ticker, s)))
        priced.append({"ticker": ticker, "weight": weight, "price": price,
                       "value": round(qty * price_ils, 2)})
    if not positions:
        return {"ok": False, "mode": REPLACE, "error": "could not price the basket"}

    preview = {"ok": True, "mode": REPLACE, "strategy_id": strategy_id,
               "budget": round(budget, 2), "loaded": priced, "count": len(priced),
               "removing": removing,
               "removing_value_ils": round(sum(x["value_ils"] for x in removing), 2),
               "broker_note": BROKER_NOTE}
    if dry_run:
        return {**preview, "dry_run": True}

    # full replace: delete existing holdings, then insert the basket
    for p in await list_positions(session, user):
        await session.delete(p)
    await session.flush()
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, positions)
    await session.commit()
    return {**preview, "dry_run": False}
