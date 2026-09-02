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
    from app.services.sleeve_service import sleeve_occupancy
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
    ids = [s["strategy_id"] for s in sleeve_rows if s.get("held_pct")]
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

    # Everything no sleeve claims. Priced from the instrument table, which is
    # what that table is FOR -- a static basket held buy-and-hold. The bug was
    # only ever pointing it at rule-based sleeves.
    # `list_positions` returns ORM Position rows, not dicts -- attribute access,
    # and the same FX normalisation `_portfolio_stats` uses, so the two cannot
    # disagree about what a holding is worth (investing-discipline S2).
    rows = await list_positions(session, user)
    from app.services.fx import fx_rate, price_currency
    for p in rows:
        tk = str(getattr(p, "ticker", "") or "").upper()
        if not tk or tk in claimed:
            continue
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = fx_rate(price_currency(p.market, meta))
        val = float(p.quantity or 0.0) * float(p.current_price or 0.0) * rate
        if val <= 0:
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
    rest = round(100.0 - float(pct), 1)
    return (f"{pct:g}% of the book is priced from its own measured strategies "
            f"({label} returns, measured volatility) -- that is your sleeves. "
            f"The remaining {rest:g}% is your core and your cash, and it is priced "
            f"holding by holding from the instrument table because this app does "
            f"not yet measure a core the way it measures a sleeve. That is a gap "
            f"here, not a verdict on your core.")
