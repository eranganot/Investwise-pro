"""The /plan payload and the book statistics behind it.

PR 118a (L7): moved out of `routes/plan.py`, unchanged, so services (Today,
plan health, the assistant context) can read them without importing a route.
The route keeps validation, `Depends` and response shaping.
"""
import logging

from app.services.intake_service import list_positions
from app.services.plan_service import effective_caps, get_plan

logger = logging.getLogger(__name__)

PERIOD_MULT = {"monthly": 12, "quarterly": 4, "yearly": 1}


async def positions(session, user):
    return await list_positions(session, user)


async def book_measured(session, user) -> dict | None:
    """The book's measured forward assumptions, or None if they cannot be built.

    Wrapped because a plan card that cannot render is worse than one rendering
    on the instrument table and saying so. A failure here degrades to exactly
    the pre-PR-16 behaviour, and `assumption_basis` on the payload still reads
    'static', so the screen never claims a measurement it did not get.
    """
    from app.services.measured_assumptions import book_assumptions
    try:
        m = await book_assumptions(session, user)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.warning("measured assumptions unavailable; using the instrument table",
                       exc_info=False)
        m = None
    # PR 98: ONE return basis. A fresh goal verdict's own figures win, so this
    # screen, the projection and the goal cards quote the same numbers; the
    # measured blend stays alongside under `measured_*` for comparison.
    try:
        from app.services.frontier_service import book_expectation
        fe = await book_expectation(session, user)
    except Exception:  # noqa: BLE001 -- the measured figures are the safe fallback
        fe = None
    if fe:
        m = dict(m or {})
        m["measured_expected_return_pct"] = m.get("expected_return_pct")
        m["measured_volatility_pct"] = m.get("volatility_pct")
        m["expected_return_pct"] = fe["expected_return_pct"]
        m["volatility_pct"] = fe["volatility_pct"]
        m["basis"] = "frontier"
        m["frontier"] = fe
    return m


def frontier_basis_text(fe: dict | None) -> str:
    """PR 98: the basis sentence when the goal verdict supplies the numbers."""
    fe = fe or {}
    return (f" (your goal model: {fe.get('confidence') or 'blended'} estimate, recent volatility weighted "
            f"{float(fe.get('recent_weight') or 0):.0%}, market {fe.get('regime') or 'unknown'}; "
            f"the same numbers as your goal cards)")


def portfolio_stats(rows, measured: dict | None = None) -> dict:
    """NAV plus the book's forward return and volatility.

    PR 16 -- `measured` is the payload from
    `measured_assumptions.book_assumptions()`. Passed, its figures REPLACE the
    per-ticker table for return and volatility, because that table prices TQQQ
    and SOXL as buy-and-hold instruments and this book holds them inside rules
    whose purpose is to be out of them some of the time.

    OMITTED, THIS FUNCTION IS EXACTLY WHAT IT WAS. Eight screens read it and a
    silent change to any of them would be the defect, not the fix -- the same
    discipline W3's engine branch used. NAV is never taken from `measured`: it
    is FX-normalised here and must stay the one number every card sizes against
    (investing-discipline S2).
    """
    from app.services.fx import price_currency, fx_rate
    from app.services.strategy_profile import assumptions_for
    nav = ret = vol = 0.0
    for p in rows:
        m = p.meta or {}
        rate = fx_rate(price_currency(p.market, m if isinstance(m, dict) else None))
        val = float(p.quantity) * float(p.current_price or 0) * rate  # base-currency value
        nav += val
        # Holdings added through the UI carry no expected_return_pct, and the old
        # `or 0.0` counted them as returning exactly nothing -- so a normal equity
        # book reported "~0%/yr vs your 10% target - behind", which was an
        # artefact of missing metadata, not a fact about the portfolio. Fall back
        # to the instrument's character instead of to zero.
        _ret, _vol = assumptions_for(p.ticker, m.get("asset_class"))
        ret += val * (m.get("expected_return_pct") if m.get("expected_return_pct") is not None else _ret)
        vol += val * (m.get("volatility_pct") if m.get("volatility_pct") is not None else _vol)
    if not nav:
        return {"nav": 0.0, "expected_roi": None, "volatility": None}
    out = {"nav": nav, "expected_roi": round(ret / nav, 2), "volatility": round(vol / nav, 2),
           "assumption_basis": "static"}
    if measured:
        # Each side is replaced only if the measurement actually produced one.
        # A half-measured blend keeps the table's answer for the other half and
        # the payload says which -- never a number whose halves came from
        # different places without a label.
        if measured.get("expected_return_pct") is not None:
            out["expected_roi"] = round(float(measured["expected_return_pct"]), 2)
        if measured.get("volatility_pct") is not None:
            # ONE VOLATILITY, the blend of what the strategies measured over
            # their own windows. PR 55.1 briefly made the headline a
            # sigma/sqrt(f) "in-market" rescaling of this and kept the blend
            # beside it under `volatility_strategy_longrun`; both keys are gone
            # because the rescaling was wrong. It assumed a sleeve sits flat
            # when its rule is off, and these sleeves hold their base instead --
            # measured, the off days run at 22.46% volatility, not zero. See
            # STATUS.md B1. Until something is measured that this endpoint can
            # actually stand behind, it reports the blend and `/portfolio/risk`
            # reports the realised figure, each labelled.
            out["volatility"] = round(float(measured["volatility_pct"]), 2)
        out["assumption_basis"] = measured.get("basis", "static")
        out["measured_pct_of_book"] = measured.get("measured_pct_of_book")
        out["assumptions"] = measured
        # PR 98: when the goal verdict supplies the numbers, say so and keep the
        # measured blend beside them.
        out["expectation"] = measured.get("frontier")
        out["measured_expected_roi"] = measured.get("measured_expected_return_pct")
        # What the screen used to say, kept beside what it says now. A number
        # that moved without the old one visible is a number the reader cannot
        # check.
        out["static_expected_roi"] = round(ret / nav, 2)
        out["static_volatility"] = round(vol / nav, 2)
    return out


_TARGET_PERIODS = {"monthly": 12, "quarterly": 4, "yearly": 1}


async def goal_of(session, user, plan, nav) -> dict:
    """PR 74 -- the ONE goal (`goal_service.compute_goal`). Replaces
    `_auto_target`, which derived the target from LIVE NAV, so progress was
    ``NAV / (NAV x (1+roi)^n)`` -- a constant no market move could change."""
    from app.services.goal_service import compute_goal
    return await compute_goal(session, user, plan, nav)


def plan_dict(plan, stats: dict, goal: dict | None = None) -> dict:
    nav = stats["nav"]
    goal = goal or {}
    if plan is None:
        base = {"configured": False, "objective": "Balanced", "risk_tolerance": "Medium",
                "horizon_years": 10, "target_amount": None, "target_date": None, "currency": "ILS",
                "target_roi_pct": None, "target_roi_period": "yearly",
                "target_yield_pct": None, "target_yield_period": "yearly", "preferred_depth": None,
                "cash_target_pct": None, "cash_target_ils": None, "cash_redeploy_at_ils": None,
                "caps": effective_caps(None), "goal_progress": None}
    else:
        target = goal.get("target")
        base = {"configured": True, "objective": plan.objective, "risk_tolerance": plan.risk_tolerance,
                "horizon_years": plan.horizon_years, "target_amount": target,
                "target_date": plan.target_date, "currency": plan.currency,
                "target_roi_pct": plan.target_roi_pct, "target_roi_period": plan.target_roi_period or "yearly",
                "target_yield_pct": plan.target_yield_pct, "target_yield_period": plan.target_yield_period or "yearly",
                "preferred_depth": plan.preferred_depth,
                # None means nobody has been asked, which is not a deliberate 0.
                "cash_target_pct": getattr(plan, "cash_target_pct", None),
                "cash_target_ils": (float(plan.cash_target_ils)
                                    if getattr(plan, "cash_target_ils", None) is not None else None),
                "cash_redeploy_at_ils": (float(plan.cash_redeploy_at_ils)
                                         if getattr(plan, "cash_redeploy_at_ils", None) is not None
                                         else None),
                "caps": effective_caps(plan), "current_value": nav,
                "goal_progress": goal.get("progress")}
    # PR 74. The whole goal, so the screen can show WHY the target is what it
    # is ("₪20,000 put in, compounding 30%/yr to 31 Dec 2031 -> ₪82,7xx") and
    # where you should be today (`on_track_value`) against where you are.
    base["goal"] = goal or None
    base["portfolio_expected_roi_pct"] = stats["expected_roi"]
    # PR 16 -- where that figure came from. The banner reading "~10.87%/yr vs
    # your 30%/yr target" never said it was an instrument-table guess about
    # buy-and-hold TQQQ; a forward number with no stated basis is not checkable.
    base["assumption_basis"] = stats.get("assumption_basis", "static")
    # PR 98: the goal model's basis when it supplies the number, and the
    # measured blend it replaced, so the screen can show both.
    base["expectation"] = stats.get("expectation")
    base["portfolio_expected_roi_pct_measured"] = stats.get("measured_expected_roi")
    base["measured_pct_of_book"] = stats.get("measured_pct_of_book")
    base["portfolio_expected_roi_pct_static"] = stats.get("static_expected_roi")
    base["portfolio_volatility_pct"] = stats.get("volatility")
    # PR 55.1 also added `portfolio_volatility_pct_strategy_longrun` HERE, under
    # a different name from the one it used in `_portfolio_stats`. PR 56 removed
    # the producer and missed this mapper, so the payload went on carrying the
    # retracted number's NAME with a null value -- the same shape as PR 43, a
    # second call site surviving the fix to the first. A key that only ever
    # holds null is worse than an absent one: it reads as "we measure this and
    # it came back empty". Removed. See STATUS.md B1 / PR 56.1.
    base["portfolio_volatility_pct_static"] = stats.get("static_volatility")
    base["assumptions"] = stats.get("assumptions")
    roi = base.get("target_roi_pct")
    if roi and stats["expected_roi"] is not None:
        annual_target = roi * PERIOD_MULT.get(base.get("target_roi_period", "yearly"), 1)
        base["roi_annual_target_pct"] = round(annual_target, 2)
        base["roi_on_track"] = stats["expected_roi"] >= annual_target
    # PR 91: the frontier's dials, effective value and source. `kelly_fraction`
    # / `return_confidence` above-the-fold are the raw stored values (None =
    # default); this block is what the frontier actually uses.
    from app.services.frontier_service import settings_of
    base["kelly_fraction"] = getattr(plan, "kelly_fraction", None) if plan is not None else None
    base["return_confidence"] = getattr(plan, "return_confidence", None) if plan is not None else None
    base["frontier"] = settings_of(plan)
    # PR 104d: every card threshold as it applies, which ones are the user's, and
    # what each one decides (the Card-thresholds sheet renders from this).
    from app.services import thresholds as _thr
    base["thresholds"] = _thr.effective(plan)
    base["thresholds_overrides"] = _thr.overrides_of(plan)
    base["thresholds_defaults"] = _thr.defaults(plan)
    # PR 115 (D-c): the ONE card minimum in force today, in shekels.
    _thr.use(plan)
    base["thresholds_effective_card_floor_ils"] = round(_thr.card_floor_ils(float(nav or 0.0)), 2)
    base["thresholds_spec"] = {k: {"label": v[0], "detail": v[1], "min": v[2], "max": v[3]}
                               for k, v in _thr.SPEC.items()}
    # PR 148: which funds `leveraged_ceiling_pct` counts (the 2x/3x set, not -1x).
    from app.services.leveraged_ceiling import geared as _geared
    base["leveraged_funds"] = sorted(_geared())
    return base


async def sleeves_view(session, user) -> dict:
    """The strategy sleeves on this book, and the core they leave behind.

    Read-only in every sense: it arms nothing, funds nothing, and writes nothing
    -- not even a self-healing row. ``strategy_service.apply_strategy`` still
    writes the single ``plans.strategy`` pair and is still what the rest of the
    app acts on; this endpoint exists so the sleeve table can be seen and
    verified in production before anything starts depending on it.

    ``core_pct`` is the implicit remainder -- the share of the book the sleeves
    have not claimed, still governed by the objective exactly as today. It is
    computed, not stored, because in this model the core is not a row.

    There is no ``legacy`` block any more. It carried ``plans.strategy`` and
    ``plans.strategy_sleeve_pct`` through the C1-C4 transition so a reader could
    see a strategy that existed only in the columns. Both columns are gone, and
    the last thing they said on the live book was "btm_swing_dip at 15%" while
    the sleeve row said 38.4% -- a second answer that had quietly gone wrong.
    """
    from app.services import sleeve_service as sv

    from app.services import strategies as static_cat

    sleeves = await sv.list_sleeves(session, user)
    core_row = await sv.get_core(session, user)
    core = None
    if core_row is not None:
        entry = static_cat.get(core_row.strategy_id) or {}
        core = {"strategy_id": core_row.strategy_id,
                "name": entry.get("name") or core_row.strategy_id,
                "objective": entry.get("objective"),
                "risk_tolerance": entry.get("risk_tolerance"),
                "target_allocation": entry.get("target_allocation")}
    # TARGET AND ACTUAL, SIDE BY SIDE, because the card printed one and meant
    # the other. `allocated_pct` is what the sleeves are SET to; occupancy is
    # what they HOLD. On this book that is 64% against 15%, and nothing in this
    # payload made the difference visible -- so the screen rendered a target in
    # the present tense and called it the book.
    occ = await sv.sleeve_occupancy(session, user)
    _by_id = {o["strategy_id"]: o for o in occ["sleeves"]}
    from app.services import strategy_catalog as _cat
    _nav_occ = float(occ.get("nav_ils") or 0.0)

    def _holdings(sid: str, o: dict) -> list[dict]:
        # PR 166 (V4): what this sleeve is made of, one row per ticker. A ticker
        # in the strategy's `base` and not its `weights` is where it parks when
        # its signal is off (2x Gold Trend -> BIL while gold is under its trend).
        spec = _cat.get(sid) or {}
        _on = sorted(t.upper() for t in (spec.get("weights") or {}))
        parked = {t.upper() for t in (spec.get("base") or {})} - set(_on)
        hb, tb = o.get("held_by_ticker_ils") or {}, o.get("target_by_ticker") or {}
        return [{"ticker": tk, "held_ils": round(float(hb.get(tk, 0.0)), 2),
                 "held_pct": round(float(hb.get(tk, 0.0)) / _nav_occ, 4) if _nav_occ else 0.0,
                 "target_pct": round(float(tb.get(tk, 0.0)), 6),
                 "shared_with": (o.get("shared_with") or {}).get(tk) or [],
                 "parked": tk in parked,
                 # What the signal holds when it is ON, so the screen can say
                 # "signal off, instead of SOXL" rather than an unexplained SMH.
                 "instead_of": _on if tk in parked else []}
                for tk in sorted(set(hb) | set(tb))]
    _sleeves = []
    for d in sv.as_dicts(sleeves):
        o = _by_id.get(d["strategy_id"]) or {}
        _sleeves.append({**d, "held_pct": o.get("held_pct"),
                         "held_ils": o.get("held_ils"),
                         "gap_pct": o.get("gap_pct"),
                         # WHICH LEG, AND WHEN IT WAS DECIDED. Since PR 63
                         # `tickers` follows the rule's stored signal rather than
                         # the catalogue's static weights, so a screen showing
                         # QQQ where the catalogue says TQQQ must be able to say
                         # why. Without these two the payload cannot distinguish
                         # "the rule stepped back on 09-05" from a default nobody
                         # produced -- which is the distinction PR 63 exists to
                         # make possible.
                         "following_signal": o.get("following_signal"),
                         "signal_as_of": o.get("signal_as_of"),
                         "tickers": o.get("tickers"),
                         # PR 131: a shared ticker is split between its sleeves;
                         # this says which ones share it.
                         "shared_with": o.get("shared_with") or {},
                         "holdings": _holdings(d["strategy_id"], o)})
    # PR 71: what the unclaimed remainder is split into, from THE book target,
    # so the Plan tab can say the core is not all of the remainder.
    _comm = _cashr = 0.0
    _core_tgt = None
    try:
        from app.services.book_target import book_target as _bt
        _p = await get_plan(session, user)
        _btv = await _bt(session, user, getattr(_p, "objective", None) or "Balanced")
        _comm, _cashr = float(_btv.get("commodities_pct") or 0.0), float(_btv.get("cash_pct") or 0.0)
        # PR 151: the core's target AS THE CARDS USE IT. A stop-held sleeve's
        # share goes to the core (PR 149, D127), so the planned remainder below
        # (`core_pct`) is not what anything is managed to while a hold lasts.
        _core_tgt = round(float(_btv.get("core_pct") or 0.0), 4)
    except Exception:  # noqa: BLE001 -- the sleeve table must still render
        logger.warning("book target unavailable for the sleeves view", exc_info=False)
    # PR 132 (D97): every sleeve must be a different bet. `same_bet` says, per
    # catalogue strategy you do not hold, which of your sleeves it would repeat
    # (Explore marks it and offers "replace" instead of "add"); `held_same_bets`
    # names the pairs you already hold that are one bet (the wave-4 card).
    _same, _held_pairs = {}, []
    try:
        from app.services import sleeve_diversity as div
        from app.services import strategy_catalog as _cat
        _corr = await div.load_correlations(session)
        _held = [s.strategy_id for s in sleeves]
        for _sid in _cat.active_ids():
            if _sid not in _held:
                _c = div.same_bets_for(_sid, _held, _corr)
                if _c:
                    _same[_sid] = _c
        _held_pairs = await div.held_clashes(session, user)
    except Exception:  # noqa: BLE001 -- the sleeve table must still render
        logger.warning("same-bet check unavailable for the sleeves view", exc_info=True)
    # C24 (D125): what the 2x/3x sleeves are set to, and what they are managed to.
    _lev = None
    try:
        from app.services import leveraged_ceiling as _lc
        _raw = await sv.all_sleeve_targets(session, user, capped=False)
        _capd, _cut = _lc.cap_targets(_raw)
        _lev = {"ceiling_pct": _lc.ceiling_pct(),
                "chosen_pct": round(100.0 * sum(v for k, v in _raw.items() if _lc.is_geared(k)), 2),
                "managed_pct": round(100.0 * sum(v for k, v in _capd.items() if _lc.is_geared(k)), 2),
                "cut_pct": {k: round(100.0 * v, 2) for k, v in _cut.items()}}
    except Exception:  # noqa: BLE001 -- the sleeve table must still render
        logger.warning("leveraged ceiling unavailable for the sleeves view", exc_info=True)
    # PR 151: which sleeve legs a stop hold blocks, so My mix can say "on hold"
    # rather than count them as money to fund.
    try:
        from app.services import buyback_hold as _bh151
        _stopped = set(_bh151.stop_held(await _bh151.load(session, user)))
    except Exception:  # noqa: BLE001 -- the sleeve table must still render
        _stopped = set()
    for d in _sleeves:
        _tks = [str(t).upper() for t in (d.get("tickers") or [])]
        d["on_stop_hold"] = sorted(t for t in _tks if t in _stopped)
    return {
        "sleeves": _sleeves,
        "leveraged_ceiling": _lev,
        "same_bet": _same,
        "held_same_bets": _held_pairs,
        "commodities_pct": round(_comm, 4),
        "cash_reserve_pct": round(_cashr, 4),
        "allocated_pct": sv.total_pct(sleeves),
        "allocated_held_pct": occ["allocated_held_pct"],
        "allocated_held_ils": occ["allocated_held_ils"],
        "core_pct": sv.remainder_pct(sleeves),
        # PR 151: what the core is managed to (a fraction), stop holds included.
        "core_target_pct": _core_tgt,
        # The core's ACTUAL share against its target. Same distinction.
        "core_held_pct": occ["core_held_pct"],
        # PR 166 (V4): the core's holdings -- its basket and anything held in no plan.
        "core_holdings": occ.get("core_holdings") or [],
        "nav_ils": occ["nav_ils"],
        # C6. Which strategy manages the core, or None for "the objective does".
        # The core's SIZE is still `core_pct` above and still computed -- this
        # names the manager, it does not claim a share of the book.
        "core": core,
        # Still true, and still worth saying: the core is a remainder. C6 gave it
        # a name, not a percentage of its own.
        "core_is_implicit": True,
    }


async def core_options_view(session, user) -> dict:
    """The strategies that may manage the core, and what choosing each would mean.

    THE CHOICE WAS NEVER PUT IN FRONT OF ANYONE. `core` has been `null` since
    C6 shipped on 2026-08-13 -- not refused, not deferred, simply never asked.
    The only way to set one was to apply a static family card on the Strategies
    tab, where it reads as "add this strategy" and its side effect of naming
    your whole-book target mix is invisible. The Plan tab said "Core - 36%" and
    one line of grey text, which is a statement about SIZE and silent about who
    manages it.

    So this endpoint exists to make the decision legible: every eligible family,
    what mix it would manage the core to, and what that is expected to cost in
    drawdown -- side by side, in one place, instead of fourteen cards.

    ESTIMATED AND MEASURED ARE KEPT APART. `profile.est_max_drawdown_pct` is a
    modelled figure that ships with the catalogue; `measured` comes from the
    backtest store and is a real result over a real window. They are different
    kinds of claim and the payload never merges them -- `drawdown_source` says
    which one a reader is looking at, and `measured` is simply absent when the
    store has nothing.

    That distinction has teeth right now: production could not read the backtest
    store AT ALL until the `benchmark_ticker` column was added to the startup
    DDL, so every "measured" figure in the app was missing and the reason sat
    unread in `backtest_store_error`. `store_error` is echoed here so a screen
    can say "not measured yet" for the right reason.
    """
    from app.services import backtest_service as bt
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat
    from app.services.strategy_profile import with_profiles

    plan = await get_plan(session, user)
    current = await sv.core_strategy_id(session, user)

    ids = [c["id"] for c in static_cat.CATALOG]
    measured = await bt.get_many(session, ids)

    # `profile` is DERIVED, not stored on the catalogue -- the same helper the
    # Strategies tab uses, so the two screens cannot quote different numbers for
    # the same family.
    out = []
    for c in with_profiles(static_cat.CATALOG):
        sid = c["id"]
        why = sv.validate_core(sid)
        prof = c.get("profile") or {}
        m = (measured.get(sid) or {})
        metrics = (m.get("metrics") or {}) if m.get("ok") else {}
        est_dd = prof.get("est_max_drawdown_pct")
        meas_dd = metrics.get("max_drawdown_pct")
        out.append({
            "strategy_id": sid,
            "name": c.get("name"),
            "goal": c.get("goal"),
            "objective": c.get("objective"),
            "risk_tolerance": c.get("risk_tolerance"),
            "description": c.get("description"),
            "target_allocation": c.get("target_allocation") or {},
            "holdings_count": prof.get("holdings_count"),
            "expected_return_pct": prof.get("expected_return_pct"),
            "volatility_pct": prof.get("volatility_pct"),
            # The two kinds of drawdown claim, never merged.
            "est_max_drawdown_pct": est_dd,
            "measured_max_drawdown_pct": meas_dd,
            "drawdown_source": "measured" if meas_dd is not None else (
                "estimate" if est_dd is not None else None),
            "measured_period": (m.get("period") if meas_dd is not None else None),
            "is_current": sid == current,
            # Every family here is eligible; the field exists so a rule-based
            # strategy arriving in this list later cannot be offered silently.
            "eligible": why is None,
            "ineligible_reason": why,
            "matches_your_objective": c.get("objective") == getattr(plan, "objective", None),
            "matches_your_risk": c.get("risk_tolerance") == getattr(plan, "risk_tolerance", None),
        })

    return {
        "current": current,
        "options": out,
        "objective": getattr(plan, "objective", None),
        "risk_tolerance": getattr(plan, "risk_tolerance", None),
        # None means "the objective manages the core", which is a real answer
        # and not an error state -- but it should be a CHOSEN one.
        "core_is_unset": current is None,
        "store_error": bt.store_unavailable,
    }
