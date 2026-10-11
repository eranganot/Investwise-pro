"""PR 16 -- the book's forward numbers stop guessing.

Eran, 2026-08-29, on the live app: *"the portfolio expected return is not
updated based on the strategy/performance - check it"*, and separately that the
projection, the year-6 chart and the health score all looked wrong.

They are one defect. `plan._portfolio_stats` and
`portfolio_analytics.compute_snapshot` both derive the book's expected return
and volatility from `strategy_profile.assumptions_for()` -- a static per-ticker
lookup keyed by instrument character, which prices TQQQ and SOXL at **12%/yr
and 55% volatility, BUY-AND-HOLD**. Grepping `portfolio_analytics.py`,
`strategy_profile.py` and the plan routes for `StrategyBacktest` returns
**nothing**: every measurement Phase T produced is invisible to the numbers on
Today.

He does not hold those tickers buy-and-hold. He holds them inside
`btm_swing_dip` and `btm_trend_soxl` -- rules whose entire purpose is to be OUT
of the instrument some of the time.

Reproduced before writing any of this, against his live screens:

    Risk chip 15        <- weighted volatility 45.6% against a 25% cap
    Health 69           <- 0.30*15 + 0.25*100 + 0.25*100 + 0.20*71 = 68.7
    Projection 21,723   <- NAV 21,689 at 10.87%/yr and 46% vol over 6 years

Every screen's arithmetic is correct. One input is wrong and four screens
inherit it.

THE TWO HALVES ARE NOT EQUALLY EVIDENCED, AND THIS MODULE REFUSES TO PRETEND
THEY ARE.
================================================================================

**Volatility: measured wherever a measurement exists.** Volatility is the stable
half of a backtest. A trend rule that sits in cash through drawdowns genuinely
realises less volatility than the instrument it trades, and that is a property
of the RULE rather than of the decade it was measured in. A static 55% describes
something he does not run.

**Return: measured OUT OF SAMPLE, by default, and never silently.** W1 measured
this exact question on 2026-08-27 and found that **not one strategy in the
catalog reaches FITTING_HELPS** -- nothing here shows a stable out-of-sample
edge. A full-sample CAGR for a 3x leveraged trend strategy over 2015-2025 is a
bull-market extrapolation, and shipping it as "your expected return" would be
the overfitting W1 exists to detect wearing a better label. So the default reads
`robustness.out_of_sample.cagr_pct`, which the app already computes and already
RANKS on, and the full-sample figure travels alongside so the gap is visible.

Both defaults are stated here, before the numbers, and both are settings.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ASSUMPTIONS_VERSION = "m1"

# Basis for the RETURN half. Volatility does not get one of these: there is no
# defensible reading under which a static guess beats a measurement of the rule
# actually being run.
RETURN_BASIS_OOS = "measured_oos"      # default -- what the app already ranks on
RETURN_BASIS_FULL = "measured_full"    # the whole window; a bull-market number
RETURN_BASIS_STATIC = "static"         # ignore the measurements entirely

# The floor, again. `strategy_backtest._MIN_OBSERVATIONS` is 260 with the
# comment "~1 trading year; below this, CAGR is noise". This is now the FOURTH
# design it has decided (PR 13's ranking window, W1's fold geometry, W1's
# abstention, and here). Not imported: it is private to the engine, and a public
# copy that drifts is worse than a stated duplicate. The tests assert they agree.
MIN_OBSERVATIONS = 260

# Why a component could not be measured. Each is a different fact, and none of
# them is a silent fallback -- investing-discipline S1: ground or abstain, and
# say why with a fix hint.
NOT_MEASURED_NO_ROW = "NO_MEASUREMENT"        # the strategy has never been backtested
NOT_MEASURED_FAILED = "MEASUREMENT_FAILED"    # the row exists and ok is False
NOT_MEASURED_STALE = "ENGINE_VERSION_MOVED"   # measured by an engine we no longer run
NOT_MEASURED_SHORT = "TOO_FEW_OBSERVATIONS"   # under the 260-session floor
NOT_MEASURED_NO_OOS = "NO_OUT_OF_SAMPLE"      # asked for OOS, the row has none
# PR 37. The core IS measured, and the book is not holding it. Distinct from
# every reason above, all of which say the measurement is missing: this one says
# the measurement is fine and does not describe these holdings.
CORE_NOT_HELD = "CORE_NOT_HELD"
CORE_NOT_SET = "NO_CORE_STRATEGY"             # the book names no core at all

BASIS_MEASURED = "measured"
BASIS_MIXED = "mixed"
BASIS_STATIC = "static"

SOURCE_MEASURED = "measured"
SOURCE_STATIC = "static"

# The sentence that has to travel with any measured return, because the app's
# own W1 result contradicts the naive reading of it.
RETURN_CAVEAT = (
    "A measured CAGR is what happened over one window, not what will happen. "
    "W1 walked every overlay forward and not one reached FITTING_HELPS, so "
    "treat this as the book's character rather than as a forecast."
)


def _pick_return(metrics: dict, robustness: dict, basis: str) -> tuple[float | None, str | None]:
    """The return half, and the reason if there isn't one.

    Kept separate from `_pick_volatility` on purpose: a component may
    legitimately end up with a measured volatility and a static return, and a
    blended number whose halves came from different places has to be able to say
    so. Collapsing them into one "is this measured" flag is how that gets lost.
    """
    if basis == RETURN_BASIS_STATIC:
        return None, None                       # not an abstention; a choice
    if basis == RETURN_BASIS_FULL:
        v = metrics.get("cagr_pct")
        return (float(v), None) if v is not None else (None, NOT_MEASURED_NO_ROW)
    oos = ((robustness or {}).get("out_of_sample") or {})
    inner = oos.get("out_of_sample") if isinstance(oos.get("out_of_sample"), dict) else oos
    v = (inner or {}).get("cagr_pct")
    if v is None:
        return None, NOT_MEASURED_NO_OOS
    return float(v), None


def _pick_core_return(metrics: dict, robustness: dict, basis: str) -> tuple[float | None, str | None]:
    """The return half for a CORE, which is not the same question as for a rule.

    `_pick_return` defaults to the out-of-sample fold because a rule has
    parameters, and a full-sample CAGR for a tuned rule is the overfitting W1
    exists to detect wearing a better label.

    A FIXED BASKET HAS NO PARAMETER. There is nothing to tune, so there is
    nothing for an out-of-sample split to catch -- splitting a 60/40's history
    measures the decade, not the strategy. `backtest_service.measure_cores`
    stores that as `NO_FIT_TO_TEST` rather than leaving `robustness` empty, and
    here it means the full-window figure is the honest reading rather than a
    fallback.

    Which is NOT the same as saying the number is a forecast. `RETURN_CAVEAT`
    still travels with it, and a 60/40 measured over 2015-2025 is still a
    bull-market window; the component records `full_window_because` so the
    reason is on the payload rather than in this docstring only.
    """
    if basis == RETURN_BASIS_STATIC:
        return None, None
    v = metrics.get("cagr_pct")
    if v is None:
        return None, NOT_MEASURED_NO_ROW
    return float(v), None


def _pick_volatility(metrics: dict) -> tuple[float | None, str | None]:
    v = (metrics or {}).get("volatility_pct")
    if v is None:
        return None, NOT_MEASURED_NO_ROW
    return float(v), None


def usable(row, *, engine_version: str) -> tuple[bool, str | None]:
    """Is this stored measurement fit to price a forward number?

    Three gates, and each rejection is reported by name rather than degrading
    into the static table without comment (investing-discipline S1, and S3 on
    stale pins: a version change INVALIDATES a pin, it does not soften it).
    """
    if row is None:
        return False, NOT_MEASURED_NO_ROW
    if not getattr(row, "ok", False):
        return False, NOT_MEASURED_FAILED
    if (getattr(row, "engine_version", "") or "") != engine_version:
        return False, NOT_MEASURED_STALE
    if int(getattr(row, "observations", 0) or 0) < MIN_OBSERVATIONS:
        return False, NOT_MEASURED_SHORT
    return True, None


def blend(components: list[dict]) -> dict:
    """NAV-weighted expected return and volatility, with the provenance kept.

    PURE -- list of dicts in, dict out, no ORM and no I/O, matching
    `split_solver` / `core_search` / `sleeve_count` / `walk_forward`. Every rule
    below is testable without a database or a provider.

    Each component: `{weight_pct, return_pct, volatility_pct, return_source,
    volatility_source, ...}`. A component with no usable number on a side is
    EXCLUDED from that side's weighting and its weight is reported, rather than
    being counted as a zero -- a holding the app cannot characterise is not a
    holding that returns nothing, which is the exact bug the `or 0.0` in
    `_portfolio_stats` used to have.
    """
    ret_num = ret_den = vol_num = vol_den = 0.0
    measured_w = 0.0
    for c in components:
        w = max(0.0, float(c.get("weight_pct") or 0.0))
        if not w:
            continue
        r, v = c.get("return_pct"), c.get("volatility_pct")
        if r is not None:
            ret_num += w * float(r)
            ret_den += w
        if v is not None:
            vol_num += w * float(v)
            vol_den += w
        if c.get("return_source") == SOURCE_MEASURED or c.get("volatility_source") == SOURCE_MEASURED:
            measured_w += w

    total_w = sum(max(0.0, float(c.get("weight_pct") or 0.0)) for c in components)
    srcs = {c.get("volatility_source") for c in components
            if c.get("weight_pct")} | {c.get("return_source") for c in components
                                       if c.get("weight_pct")}
    srcs.discard(None)
    if srcs == {SOURCE_MEASURED}:
        basis = BASIS_MEASURED
    elif SOURCE_MEASURED in srcs:
        basis = BASIS_MIXED
    else:
        basis = BASIS_STATIC

    return {
        "version": ASSUMPTIONS_VERSION,
        "expected_return_pct": (round(ret_num / ret_den, 2) if ret_den else None),
        "volatility_pct": (round(vol_num / vol_den, 2) if vol_den else None),
        # WHAT THAT NUMBER IS, beside it. Only one of the two above is
        # mathematically a portfolio figure. Expectation is linear, so a
        # NAV-weighted average of component returns IS the book's expected
        # return. Volatility is not: sqrt(w'Sw) equals sum(w.sigma) only when
        # every pair is perfectly correlated, and is strictly smaller
        # otherwise. So this is an UPPER BOUND on the volatility of any
        # portfolio built from these components, not an estimate of it.
        #
        # It has been read as the book's volatility because nothing beside it
        # said otherwise. On the live book it reads 44.62 while the realised
        # figure from the real covariance is 67.48 -- above the bound, which is
        # a contradiction rather than a difference of method and is now flagged
        # where both numbers exist (`portfolio_risk.analyze`).
        #
        # The docstring above already reached for half of this, about the core:
        # "a per-ticker table has no covariance term -- so it overstates the
        # core's volatility by construction". True of the whole blend.
        "volatility_method": "weighted_average",
        "volatility_is_upper_bound": True,
        # WITHDRAWN 2026-09-04, one deploy after it shipped. `volatility_conditional_pct`
        # scaled each component by `1/sqrt(exposure)`, which requires the
        # out-of-market days to be FLAT. They are not: `_weights_or_off` holds
        # the sleeve's BASE, never cash, and says so in its own docstring.
        # Measured directly -- btm_swing_dip's true in-market volatility is
        # 67.97%, not the 85.96% the formula gave, and its out-of-market days
        # run at 22.46% rather than zero.
        #
        # Not replaced with a corrected version, because a corrected one does
        # not reconcile either: blending the TRUE in-market figures gives
        # 60.01% against a realised 67.48%, and a weighted average cannot sit
        # below the portfolio it bounds. The remaining gap is the WINDOW --
        # these components are ten-year backtests and the realised figure is
        # 251 days. See STATUS.md B1.
        #
        # The number that needs no model is already computed elsewhere:
        # `portfolio_risk.analyze` measures the real covariance of the actual
        # holdings. This one describes the STRATEGIES, and now says so.
        "volatility_window": "strategy backtests, full measured window",
        "basis": basis,
        "measured_pct_of_book": (round(measured_w / total_w * 100, 1) if total_w else 0.0),
        # Weight that could not be priced at all on each side. Named, because a
        # weighted average over 60% of the book that presents itself as the
        # book's number is a quiet lie.
        "return_coverage_pct": (round(ret_den / total_w * 100, 1) if total_w else 0.0),
        "volatility_coverage_pct": (round(vol_den / total_w * 100, 1) if total_w else 0.0),
        "components": components,
    }


async def book_assumptions(session, user, *, return_basis: str | None = None) -> dict:
    """The book's forward return and volatility, measured where it can be.

    Weighting is by each sleeve's HELD share, never its target. That distinction
    is the whole of PR 6: this book's sleeves target 64% and held 15% of it, and
    a forward number weighted by targets would describe a book that does not
    exist. `sleeve_occupancy` is the one function that answers "how much of my
    book is this strategy actually running".

    Everything no sleeve claims -- the core, loose holdings, anything bought by
    hand -- keeps the static per-ticker table, and says so.

    THAT IS A KNOWN GAP, NOT A FINISHED DESIGN. The original comment here read
    "a static basket held buy-and-hold is exactly what that table is for; the
    defect was only ever applying it to rule-based sleeves", and that is wrong
    on its own terms. A CORE IS A STRATEGY TOO: `bal_6040` is a basket plus a
    rebalance discipline, and `core_search` already measures fourteen of them
    through `blend.core_spec` + `blend.measure_blend`. Pricing a rebalanced
    60/40 as the weighted average of a VTI row and a BND row cannot express the
    one thing rebalancing between them does, because a per-ticker table has no
    covariance term -- so it overstates the core's volatility by construction.

    This function does not yet ask for the core row at all, so even a core held
    exactly to its basket lands in `unallocated`. Fixing that is its own change;
    until then `_headline` says so in the user's words rather than implying the
    core has nothing to measure.
    """
    from app.core.config import get_settings
    from app.services.backtest_service import ENGINE_VERSION
    from app.services.intake_service import list_positions
    from app.services.backtest_service import NO_FIT_TO_TEST
    from app.services.sleeve_service import core_strategy_id, sleeve_occupancy
    from app.services.strategy_profile import assumptions_for

    st = get_settings()
    basis = (return_basis or getattr(st, "assumption_return_basis", RETURN_BASIS_OOS)
             or RETURN_BASIS_OOS)

    occ = await sleeve_occupancy(session, user)
    nav = float(occ.get("nav_ils") or 0.0)
    if not nav:
        return {**blend([]), "abstained": [], "pins": [], "return_basis": basis,
                "caveat": RETURN_CAVEAT,
                "note": "no holdings, so there is nothing to characterise."}

    sleeve_rows = occ.get("sleeves") or []
    # The core's measurement is fetched in the SAME query as the sleeves'. Two
    # queries would be two chances for the two halves of one book to be read at
    # different moments, and they are compared against each other below.
    core_id = await core_strategy_id(session, user)
    ids = [s["strategy_id"] for s in sleeve_rows if s.get("held_pct")]
    if core_id:
        ids = ids + [core_id]
    by_id = {}
    if ids:
        from sqlalchemy import select

        from app.models.tables import StrategyBacktest
        res = await session.execute(
            select(StrategyBacktest).where(StrategyBacktest.strategy_id.in_(ids)))
        by_id = {r.strategy_id: r for r in res.scalars().all()}

    components: list[dict] = []
    abstained: list[dict] = []
    pins: list[dict] = []
    claimed: set[str] = set()

    for s in sleeve_rows:
        w = round(float(s.get("held_pct") or 0.0) * 100.0, 4)   # held_pct is a fraction
        claimed |= set(s.get("tickers") or [])
        if w <= 0:
            continue
        sid = s["strategy_id"]
        row = by_id.get(sid)
        ok, why = usable(row, engine_version=ENGINE_VERSION)
        # The static answer for this sleeve's tickers, needed either as the
        # fallback or as the comparison the card shows beside the measured one.
        st_r, st_v = _static_for(s.get("tickers") or [], assumptions_for)
        if not ok:
            abstained.append({"kind": "sleeve", "id": sid, "weight_pct": w,
                              "reason": why,
                              "detail": _why_detail(why, sid, row, ENGINE_VERSION)})
            components.append({
                "kind": "sleeve", "id": sid, "weight_pct": w,
                "tickers": s.get("tickers") or [],
                "return_pct": st_r, "volatility_pct": st_v,
                "return_source": SOURCE_STATIC, "volatility_source": SOURCE_STATIC,
                "note": f"not measured ({why}); priced from the instrument table",
            })
            continue

        metrics = dict(row.metrics or {})
        robustness = dict(row.robustness or {})
        m_r, r_why = _pick_return(metrics, robustness, basis)
        m_v, v_why = _pick_volatility(metrics)
        if m_r is None and r_why:
            abstained.append({"kind": "sleeve", "id": sid, "weight_pct": w,
                              "reason": r_why, "side": "return",
                              "detail": f"{sid} has no {basis} return to read; "
                                        f"the instrument table is used for the return half only."})
        if m_v is None and v_why:
            abstained.append({"kind": "sleeve", "id": sid, "weight_pct": w,
                              "reason": v_why, "side": "volatility",
                              "detail": f"{sid} stored no volatility."})
        components.append({
            "kind": "sleeve", "id": sid, "weight_pct": w,
            "tickers": s.get("tickers") or [],
            "return_pct": m_r if m_r is not None else st_r,
            "volatility_pct": m_v if m_v is not None else st_v,
            "return_source": SOURCE_MEASURED if m_r is not None else SOURCE_STATIC,
            "volatility_source": SOURCE_MEASURED if m_v is not None else SOURCE_STATIC,
            # HOW MUCH OF THE MEASURED WINDOW THIS RULE WAS ACTUALLY HOLDING
            # ITS AGGRESSIVE INSTRUMENT. REPORTED, NOT USED to scale anything.
            #
            # PR 55.1 divided the volatility by `sqrt(exposure)` to recover an
            # "in-market" figure. That is only valid if the out-of-market days
            # are FLAT, and they are not: `_weights_or_off` holds the sleeve's
            # BASE, never cash. On the live book btm_swing_dip is out 84.4% of
            # the time and those days run at 22.46% volatility, not zero -- so
            # the formula returned 85.96% where the truth is 67.97%.
            #
            # The field stays because it is the fact that MAKES the blend
            # readable: a 34.24% volatility over a window the rule was invested
            # in 15.6% of describes something very different from the same
            # number at full exposure. It says so; it no longer pretends to
            # convert it.
            "exposure_pct": (metrics.get("time_in_market_pct")
                             if m_v is not None else None),
            # Both readings travel together so the extrapolation is visible
            # rather than inferred, the same reason W2 put would_improve_at_limit
            # on the card.
            "measured_full_return_pct": metrics.get("cagr_pct"),
            "measured_oos_return_pct": _pick_return(metrics, robustness, RETURN_BASIS_OOS)[0],
            "static_return_pct": st_r, "static_volatility_pct": st_v,
            "measured_max_drawdown_pct": metrics.get("max_drawdown_pct"),
        })
        pins.append({"id": sid, "engine_version": row.engine_version,
                     "period_start": row.period_start, "period_end": row.period_end,
                     "observations": row.observations,
                     "computed_at": (row.computed_at.isoformat()
                                     if getattr(row, "computed_at", None) else None)})

    # Everything no sleeve claims: the core region, plus cash and anything
    # bought by hand.
    # `list_positions` returns ORM Position rows, not dicts -- attribute access,
    # and the same FX normalisation `_portfolio_stats` uses, so the two cannot
    # disagree about what a holding is worth (investing-discipline S2).
    rows = await list_positions(session, user)
    from app.services.fx import fx_rate, price_currency

    unclaimed: list[tuple[str, dict, float]] = []      # (ticker, meta, value_ils)
    for p in rows:
        tk = str(getattr(p, "ticker", "") or "").upper()
        if not tk or tk in claimed:
            continue
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = fx_rate(price_currency(p.market, meta))
        val = float(p.quantity or 0.0) * float(p.current_price or 0.0) * rate
        if val <= 0:
            continue
        unclaimed.append((tk, meta, val))

    # ----------------------------------------------------------------- #
    # PR 37 -- THE CORE IS A STRATEGY, AND IT GETS MEASURED LIKE ONE.
    #
    # Until now this function walked the sleeves and then priced every
    # remaining holding one ticker at a time, which meant a core never had a
    # measurement to be read even when one existed. It does now
    # (`backtest_service.measure_cores`), so the core is asked for by name and
    # gated exactly as a sleeve is.
    #
    # With ONE extra gate that a sleeve does not need. A sleeve's tickers are
    # its tickers by definition; a core's basket is a TARGET, and a book can
    # hold something else entirely. Pricing holdings from a measurement of a
    # basket they are not is the same class of error as PR 6's target-weighting,
    # so the composition is checked first and a mismatch abstains BY NAME.
    # ----------------------------------------------------------------- #
    core_tickers: set[str] = set()
    core_row_id = core_id
    if core_row_id:
        from app.services import thresholds
        from app.services.recommendations import SLEEVE_DRIFT_BAND_PCT
        from app.services.strategies import core_weights as _core_weights
        from app.services.strategies import get as _static_get

        basket = _core_weights(core_row_id) or {}
        cname = ((_static_get(core_row_id) or {}).get("name") or core_row_id)
        # Cash is in no basket, so it is not part of the composition question.
        # PR 71: a class carved out of the core (Commodities, when the core
        # names none) is not part of the core's composition either.
        from app.services.allocation_mix import classify as _cls
        from app.services.book_target import carved_classes
        from app.services.plan_service import get_plan as _gp
        _plan = await _gp(session, user)
        _carved = carved_classes(((_static_get(core_row_id) or {}).get("target_allocation")),
                                 getattr(_plan, "objective", None))
        held = {tk: val for tk, meta, val in unclaimed
                if str(meta.get("asset_class") or "") != "Cash" and tk != "CASH"
                and (str(meta.get("asset_class") or "") or _cls(tk, "")) not in _carved}
        matched, evidence = core_matches_basket(held, basket,
                                                band_pct=thresholds.get("sleeve_drift_band_pts", SLEEVE_DRIFT_BAND_PCT))
        crow = by_id.get(core_row_id)
        cok, cwhy = usable(crow, engine_version=ENGINE_VERSION)
        core_w = round(sum(held.values()) / nav * 100.0, 4)

        if not matched:
            # The measurement is fine. It just is not a measurement of this.
            abstained.append({"kind": "core", "id": core_row_id, "weight_pct": core_w,
                              "reason": CORE_NOT_HELD,
                              "detail": _core_not_held_detail(cname, core_row_id, evidence),
                              "evidence": evidence})
        elif not cok:
            abstained.append({"kind": "core", "id": core_row_id, "weight_pct": core_w,
                              "reason": cwhy,
                              "detail": _why_detail(cwhy, core_row_id, crow, ENGINE_VERSION)})
        elif core_w > 0:
            cmetrics = dict(crow.metrics or {})
            crobust = dict(crow.robustness or {})
            m_r, r_why = _pick_core_return(cmetrics, crobust, basis)
            m_v, v_why = _pick_volatility(cmetrics)
            st_r, st_v = _static_for(sorted(held), assumptions_for)
            if m_r is None and r_why:
                abstained.append({"kind": "core", "id": core_row_id, "weight_pct": core_w,
                                  "reason": r_why, "side": "return",
                                  "detail": f"{cname} has no stored CAGR to read."})
            if m_v is None and v_why:
                abstained.append({"kind": "core", "id": core_row_id, "weight_pct": core_w,
                                  "reason": v_why, "side": "volatility",
                                  "detail": f"{cname} stored no volatility."})
            core_tickers = set(held)
            components.append({
                "kind": "core", "id": core_row_id, "name": cname,
                "weight_pct": core_w, "tickers": sorted(held),
                "return_pct": m_r if m_r is not None else st_r,
                "volatility_pct": m_v if m_v is not None else st_v,
                "return_source": SOURCE_MEASURED if m_r is not None else SOURCE_STATIC,
                "volatility_source": SOURCE_MEASURED if m_v is not None else SOURCE_STATIC,
                "measured_full_return_pct": cmetrics.get("cagr_pct"),
                "measured_max_drawdown_pct": cmetrics.get("max_drawdown_pct"),
                "static_return_pct": st_r, "static_volatility_pct": st_v,
                # WHY THE FULL WINDOW, on the payload rather than only in the
                # code: a fixed basket has no parameter to overfit, so there is
                # no out-of-sample fold to prefer. See `_pick_core_return`.
                "full_window_because": NO_FIT_TO_TEST,
                "composition": evidence,
                "note": (f"your core runs {cname}, and the book is holding it, so it is "
                         f"priced from its own measurement rather than ticker by ticker"),
            })
            pins.append({"id": core_row_id, "kind": "core",
                         "engine_version": crow.engine_version,
                         "period_start": crow.period_start, "period_end": crow.period_end,
                         "observations": crow.observations,
                         "computed_at": (crow.computed_at.isoformat()
                                         if getattr(crow, "computed_at", None) else None)})

    for tk, meta, val in unclaimed:
        if tk in core_tickers:
            continue
        w = round(val / nav * 100.0, 4)
        s_r, s_v = assumptions_for(tk, meta.get("asset_class"))
        components.append({
            "kind": "unallocated", "id": tk, "weight_pct": w, "tickers": [tk],
            # An explicit per-position figure the user entered still wins: it is
            # something they said about their own book.
            "return_pct": (meta.get("expected_return_pct")
                           if meta.get("expected_return_pct") is not None else s_r),
            "volatility_pct": (meta.get("volatility_pct")
                               if meta.get("volatility_pct") is not None else s_v),
            "return_source": SOURCE_STATIC, "volatility_source": SOURCE_STATIC,
            "static_return_pct": s_r, "static_volatility_pct": s_v,
            "note": "no sleeve claims this holding, so it is priced as held",
        })

    out = blend(components)
    out.update({"abstained": abstained, "pins": pins, "return_basis": basis,
                "caveat": RETURN_CAVEAT})
    out["note"] = _headline(out, basis)
    return out


def core_matches_basket(held_ils: dict[str, float], weights: dict[str, float],
                        *, band_pct: float) -> tuple[bool, dict]:
    """Is the core region actually holding the basket its core strategy names?

    PURE -- two dicts in, a verdict and its evidence out, no ORM and no I/O,
    matching `blend` / `split_solver` / `core_search`.

    This gate exists because measuring `bal_6040` and then pricing a book that
    holds no VTI with it would describe a book that does not exist. That is the
    defect PR 6 removed at the other end, where sleeves were weighted by their
    TARGET rather than by what they held. A measurement is only allowed to price
    the holdings it is a measurement OF.

    `band_pct` is `recommendations.SLEEVE_DRIFT_BAND_PCT` -- the app's existing
    answer to "how far may this wander before Today says so". A second, private
    tolerance here would be a second answer to one question.

    Cash is the caller's business: it is not in any basket, so the caller keeps
    it out of `held_ils` rather than this function knowing what cash is.
    """
    total = sum(v for v in held_ils.values() if v > 0)
    basket_total = sum(w for w in weights.values() if w > 0)
    if total <= 0 or basket_total <= 0:
        return False, {"reason": "nothing is held in the core region"
                       if total <= 0 else "the core names no basket"}
    want = {t.upper(): w / basket_total * 100.0
            for t, w in weights.items() if w > 0}
    have = {t.upper(): v / total * 100.0
            for t, v in held_ils.items() if v > 0}
    missing = sorted(t for t in want if t not in have)
    extra = sorted(t for t in have if t not in want)
    drift = {t: round(have.get(t, 0.0) - want[t], 2) for t in want}
    worst = max((abs(d) for d in drift.values()), default=0.0)
    ok = not missing and not extra and worst <= band_pct
    return ok, {"missing": missing, "extra": extra, "drift_pct": drift,
                "worst_drift_pct": round(worst, 2), "band_pct": band_pct,
                "held_pct": {t: round(v, 2) for t, v in sorted(have.items())},
                "basket_pct": {t: round(v, 2) for t, v in sorted(want.items())}}


def _core_not_held_detail(name: str, sid: str, ev: dict) -> str:
    """Say which holding is wrong, not that something is."""
    if ev.get("reason"):
        return f"{name} ({sid}): {ev['reason']}."
    bits = []
    if ev.get("missing"):
        bits.append(f"you hold none of {', '.join(ev['missing'])}")
    if ev.get("extra"):
        bits.append(f"{', '.join(ev['extra'])} is not in the basket")
    if not bits:
        bits.append(f"the biggest leg is {ev.get('worst_drift_pct')}pp off, "
                    f"past the {ev.get('band_pct')}pp band")
    return (f"{name} ({sid}) IS measured, and your core is not holding it: "
            f"{'; '.join(bits)}. The measurement describes the basket, so it is "
            f"not used to price holdings that are not that basket -- these are "
            f"priced from the instrument table instead. Bring the core back to "
            f"its basket and it will be priced from its own measurement.")


def _static_for(tickers: list[str], assumptions_for) -> tuple[float | None, float | None]:
    """The instrument table's answer for a sleeve's tickers, equal-weighted.

    Equal-weighted rather than value-weighted because this is the counterfactual
    -- "what the old code would have said" -- and the old code weighted by
    position value inside the same sleeve. On a one-ticker sleeve, which is what
    this book runs, the two agree exactly.
    """
    vals = [assumptions_for(t, None) for t in tickers if t]
    if not vals:
        return None, None
    return (round(sum(v[0] for v in vals) / len(vals), 2),
            round(sum(v[1] for v in vals) / len(vals), 2))


def _why_detail(why: str, sid: str, row, engine_version: str) -> str:
    if why == NOT_MEASURED_NO_ROW:
        return (f"{sid} has never been backtested. The 03:30 job measures it; "
                f"until then the instrument table is used and labelled.")
    if why == NOT_MEASURED_FAILED:
        return (f"{sid}'s last measurement failed "
                f"({getattr(row, 'reason', '') or 'no reason recorded'}). A failed "
                f"measurement is not a low number, so it is not used as one.")
    if why == NOT_MEASURED_STALE:
        return (f"{sid} was measured by engine {getattr(row, 'engine_version', '')!r} "
                f"and this build runs {engine_version!r}. A version change "
                f"invalidates the pin rather than softening it.")
    if why == NOT_MEASURED_SHORT:
        return (f"{sid} has {getattr(row, 'observations', 0)} observations against a "
                f"{MIN_OBSERVATIONS}-session floor -- the engine's own comment for "
                f"that floor is 'below this, CAGR is noise'.")
    return f"{sid} could not be used ({why})."


def _headline(out: dict, basis: str) -> str:
    if out["basis"] == BASIS_STATIC:
        return ("Nothing on this book is measured yet, so every figure comes from the "
                "instrument table -- which prices a leveraged fund as if you held it "
                "buy-and-hold.")
    pct = out["measured_pct_of_book"]
    label = {RETURN_BASIS_OOS: "out-of-sample measured",
             RETURN_BASIS_FULL: "full-window measured",
             RETURN_BASIS_STATIC: "instrument-table"}.get(basis, basis)
    # NAME THE REST, AND DO NOT EXCUSE IT. "78.8% of the book measured" reads as
    # a coverage hole and invites the reader to supply the missing half himself,
    # so the other side of the percentage has to be said out loud.
    #
    # It must NOT be said the way the first draft of this line said it -- "the
    # core and cash, which run no strategy to measure". THAT IS FALSE. A core is
    # a strategy: `bal_6040` is a basket with a rebalance discipline, this app
    # already measures fourteen candidate cores in `core_search` through
    # `blend.core_spec` + `blend.measure_blend`, and it ranks them on exactly
    # the numbers this function says cannot be had. The book is managed by TWO
    # kinds of strategy, sleeves and a core, and only one of them is measured
    # here. So the sentence names a gap in the app instead of inventing a
    # property of the core.
    #
    # PR 37 CLOSED THAT GAP, so the sentence now has three cases to tell apart,
    # and the difference between the last two is the whole point:
    #
    #   * the core is measured and priced from its own measurement;
    #   * the core is measured and the book is NOT HOLDING IT, so the
    #     measurement is deliberately not applied -- that is a fact about the
    #     holdings, and the reader has to be able to act on it;
    #   * there is no core, or it has no usable measurement yet.
    rest = round(100.0 - float(pct), 1)
    head = (f"{pct:g}% of the book is priced from its own measured strategies "
            f"({label} returns, measured volatility)")
    kinds = {c.get("kind") for c in (out.get("components") or [])
             if c.get("weight_pct") and c.get("return_source") == SOURCE_MEASURED}
    core_measured = "core" in kinds
    not_held = next((a for a in (out.get("abstained") or [])
                     if a.get("reason") == CORE_NOT_HELD), None)

    if core_measured:
        return (f"{head} -- your sleeves AND your core, each measured as the "
                f"strategy it is. The remaining {rest:g}% is cash, which is "
                f"priced as cash.")
    if not_held:
        ev = not_held.get("evidence") or {}
        gaps = []
        if ev.get("missing"):
            gaps.append(f"you hold none of {', '.join(ev['missing'])}")
        if ev.get("extra"):
            gaps.append(f"{', '.join(ev['extra'])} is not in it")
        why = ("; ".join(gaps) if gaps
               else f"its biggest leg is {ev.get('worst_drift_pct')}pp off")
        return (f"{head} -- that is your sleeves. Your core IS measured too, and "
                f"the remaining {rest:g}% is not priced from it because the book "
                f"is not holding that basket: {why}. Those holdings are priced "
                f"one ticker at a time until the core holds its basket again.")
    return (f"{head} -- that is your sleeves. The remaining {rest:g}% is your core "
            f"and your cash. The core has no usable measurement yet, so it is "
            f"priced holding by holding from the instrument table.")
