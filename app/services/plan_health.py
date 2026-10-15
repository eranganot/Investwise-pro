"""PR 70 -- the health score answers two questions: am I on plan, am I on track.

Eran, 2026-09-22: "let's re-think on how to calculate the health score and what
does it mean". The old score averaged Risk / Tax / Spread / Cash, and Risk was
volatility against a generic cap -- 3/100 on a book that runs a 3x SOXL sleeve
ON PURPOSE, so the biggest chip on the screen scored the plan he chose as a
failure. Tax read 100 because nothing was underwater, which says nothing about
anything he can act on.

He chose the replacement (proposal doc, 2026-09-22):

  * ON PLAN  -- are you doing what you decided? Five parts, each 0-100, each
    measured against HIS plan, never an outside yardstick:
        sleeves 30% . mix 20% . rules 20% . concentration 15% . cash 15%
  * ON TRACK -- will you reach the goal? The projection's own probability of
    meeting the target (the Today "Chance of hitting your goal" row), using a
    standing order when one is set up because that is a declared deposit.
  * THE SCORE -- the mean of the two, or On plan alone when there is no goal.

Every input is read from the function the matching screen already uses
(`sleeve_occupancy`, `mix_check`, `list_rules`, `goal_projection`, `_core_plan`),
so this module has no second opinion about any number it scores.

The scoring rules are pure functions below, so their arithmetic is tested
without a database. Not financial advice.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("investwise.plan_health")

WEIGHTS = {"sleeves": 0.30, "mix": 0.20, "rules": 0.20, "concentration": 0.15, "cash": 0.15}
#: A drift inside this many multiples of the band is on plan; at 4x it scores 0.
_BAND_ZERO_AT = 4.0
#: Points off the concentration part per percentage point over the limit.
_CONC_PER_POINT = 20.0
#: Points off the rules part per rule that has fired and is still waiting.
_RULE_WAITING = 25.0
_PROTECTIVE = ("stop_loss", "trailing_stop")


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def label(score: float | None) -> str | None:
    """The words on screen. Same cut-offs the app has always used."""
    if score is None:
        return None
    return ("Strong" if score >= 80 else "Healthy" if score >= 60
            else "Needs attention" if score >= 40 else "At risk")


def band_score(drift_pts: float, band_pts: float) -> float:
    """100 inside the band, falling linearly to 0 at 4x the band."""
    if band_pts <= 0:
        return 100.0 if drift_pts <= 0 else 0.0
    ratio = abs(float(drift_pts)) / float(band_pts)
    if ratio <= 1.0 + 1e-9:
        return 100.0
    return _clamp(100.0 * (_BAND_ZERO_AT - ratio) / (_BAND_ZERO_AT - 1.0))


def score_sleeves(sleeves: list[dict], core_pct: float, core_in_basket: float | None,
                  band_pts: float) -> tuple[float, str]:
    """Each sleeve against its own size, the core against its own basket.

    ``sleeves``: [{"name", "target_pct", "held_pct"}] in percentage points.
    ``core_in_basket``: the share (0-1) of the core's target value that is held
    in the basket it names, or None when there is no basket to hold it to.
    Weighted by each piece's share of the book.
    """
    parts, bits = [], []
    for s in sleeves:
        tgt, held = float(s.get("target_pct") or 0.0), float(s.get("held_pct") or 0.0)
        if tgt <= 0:
            continue
        parts.append((tgt, band_score(held - tgt, band_pts)))
        bits.append(f"{s.get('name')} {held:.1f}% vs {tgt:g}%")
    if core_pct > 0 and core_in_basket is not None:
        parts.append((core_pct, _clamp(100.0 * core_in_basket)))
        bits.append(f"core holds {core_in_basket:.0%} of its basket")
    total = sum(w for w, _ in parts)
    if total <= 0:
        return 100.0, "no sleeves or core basket to follow"
    return sum(w * s for w, s in parts) / total, "; ".join(bits)


def score_mix(max_drift_pts: float, band_pts: float) -> float:
    return band_score(max_drift_pts, band_pts)


def score_rules(protected: int, holdings: int, waiting: int) -> float:
    """Share of holdings with a stop, less 25 per fired rule still waiting."""
    cover = (protected / holdings) if holdings else 1.0
    return _clamp(100.0 * cover - _RULE_WAITING * max(0, waiting))


def score_concentration(max_weight_pct: float, limit_pct: float) -> float:
    over = max(0.0, float(max_weight_pct) - float(limit_pct))
    return _clamp(100.0 - _CONC_PER_POINT * over)


def score_cash(cash: float, floor: float, redeploy_at: float | None) -> float:
    """100 between your floor and your redeploy line; less outside it."""
    if floor > 0 and cash < floor:
        return _clamp(100.0 * cash / floor)
    if redeploy_at and redeploy_at > 0 and cash > redeploy_at:
        return _clamp(100.0 - 100.0 * (cash - redeploy_at) / redeploy_at)
    return 100.0


def _age_text(hours) -> str:
    """"3 hours ago" / "2 days ago" for a verdict's age."""
    if hours is None:
        return "an earlier solve"
    h = float(hours)
    if h < 1:
        return "less than an hour ago"
    if h < 48:
        return f"{h:.0f} hours ago"
    return f"{h / 24:.0f} days ago"


def _r(x: float) -> int:
    """Half-up. `round()` is banker's rounding: a 26.5% chance would show as 26."""
    return int(float(x) + 0.5)


def combine(parts: dict[str, float], on_track: float | None) -> dict:
    on_plan = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
    score = on_plan if on_track is None else (on_plan + on_track) / 2.0
    return {"on_plan": _r(on_plan), "on_track": (None if on_track is None else _r(on_track)),
            "score": _r(score), "label": label(_r(score))}


async def plan_health(session, user) -> dict | None:
    """The score, its two halves and the five parts, each with what it measured."""
    from app.services.book_target import mix_report as mix_check
    from app.services.projection_service import project as goal_projection
    from app.core.config import get_settings
    from app.services.allocation_mix import classify_holding
    from app.services.fx import fx_rate, price_currency
    from app.services.funding_service import cash_target_amount_ils
    from app.services.intake_service import list_positions
    from app.services.plan_service import effective_caps, get_plan
    from app.services.rules_service import list_rules
    from app.services.sleeve_service import sleeve_occupancy
    from app.services.strategies import get as strategy_get

    rows = await list_positions(session, user)
    plan = await get_plan(session, user)
    objective = getattr(plan, "objective", None) or "Balanced"

    def _val(p):
        meta = p.meta if isinstance(p.meta, dict) else None
        return (float(p.quantity or 0.0) * float(p.current_price or 0.0)
                * fx_rate(price_currency(p.market, meta)))

    vals = {}
    cash = 0.0
    for p in rows:
        tk = str(p.ticker or "").upper()
        v = _val(p)
        if tk == "CASH" or classify_holding(p) == "Cash":
            cash += v
        elif v > 0:
            vals[tk] = vals.get(tk, 0.0) + v
    nav = cash + sum(vals.values())
    if nav <= 0:
        return None
    band = float(get_settings().allocation_drift_threshold or 0.03) * 100.0
    out_parts: dict[str, dict] = {}

    # ---- sleeves ---------------------------------------------------------
    occ = await sleeve_occupancy(session, user)
    sl = []
    for s in occ.get("sleeves") or []:
        sid = s.get("strategy_id")
        sl.append({"name": (strategy_get(sid) or {}).get("name") or sid,
                   "target_pct": float(s.get("target_pct") or 0.0),
                   "held_pct": float(s.get("held_pct") or 0.0) * 100.0})
    core_pct, core_in = 0.0, None
    try:
        from app.services.recommendations import _book_target, _core_plan
        btgt = await _book_target(session, user, objective)
        core_pct = float(btgt.get("core_pct") or 0.0) * 100.0
        cp = await _core_plan(session, user, rows, nav, objective, btgt)
        if cp and cp.get("want_ils"):
            want, have = cp["want_ils"], cp.get("have_ils") or {}
            need = sum(want.values())
            core_in = (sum(min(float(have.get(t, 0.0)), float(w)) for t, w in want.items())
                       / need) if need > 0 else None
    except Exception:  # noqa: BLE001 -- the core half is reported missing, not guessed
        logger.warning("core basket unavailable for the health score", exc_info=True)
    s_sleeves, d_sleeves = score_sleeves(sl, core_pct, core_in, band)
    out_parts["sleeves"] = {"score": round(s_sleeves), "detail": d_sleeves}

    # ---- mix -------------------------------------------------------------
    mx = await mix_check(session=session, user=user)
    drift = mx.get("drift_percentage") or {}
    worst = max(drift.items(), key=lambda kv: abs(kv[1]), default=(None, 0.0))
    s_mix = score_mix(abs(float(worst[1])) * 100.0, band)
    out_parts["mix"] = {"score": round(s_mix),
                        "detail": (f"{worst[0]} {float(worst[1]) * 100:+.1f} points from target "
                                   f"(band {band:g})" if worst[0] else "no target to drift from")}

    # ---- rules -----------------------------------------------------------
    rules = [r for r in await list_rules(session, user) if r.get("active")]
    protected = {str(r["ticker"]).upper() for r in rules if r.get("rule_type") in _PROTECTIVE}
    held = [t for t in vals if t != "CASH"]
    covered = sum(1 for t in held if t in protected)
    waiting = sum(1 for r in rules if r.get("triggered"))
    s_rules = score_rules(covered, len(held), waiting)
    out_parts["rules"] = {"score": round(s_rules),
                          "detail": f"{covered} of {len(held)} holdings have a stop; "
                                    f"{waiting} fired rule{'s' if waiting != 1 else ''} waiting"}

    # ---- concentration ---------------------------------------------------
    limit = float(effective_caps(plan)["concentration_cap"]) * 100.0
    top = max(vals.items(), key=lambda kv: kv[1], default=(None, 0.0))
    top_pct = top[1] / nav * 100.0
    s_conc = score_concentration(top_pct, limit)
    out_parts["concentration"] = {"score": round(s_conc),
                                  "detail": f"{top[0]} {top_pct:.1f}% vs your {limit:g}% limit"}

    # ---- cash ------------------------------------------------------------
    floor = float(cash_target_amount_ils(nav, objective, plan))
    redeploy = getattr(plan, "cash_redeploy_at_ils", None)
    redeploy = float(redeploy) if redeploy is not None else None
    s_cash = score_cash(cash, floor, redeploy)
    if cash < floor:
        _where = f"below your ₪{floor:,.0f} cash target"
    elif redeploy and cash > redeploy:
        _where = f"above your ₪{redeploy:,.0f} redeploy line"
    else:
        _where = (f"between your ₪{floor:,.0f} cash target"
                  + (f" and ₪{redeploy:,.0f} redeploy line" if redeploy else " and no redeploy line"))
    out_parts["cash"] = {"score": round(s_cash), "detail": f"₪{cash:,.0f}, {_where}"}

    # ---- on track ----------------------------------------------------------
    on_track, track_detail, track_basis = None, "no goal set", None
    try:
        pj = await goal_projection(contribution_per_year=None, session=session, user=user)
        wc = pj.get("with_contributions") or {}
        # PR 120: THE GOAL'S ONE CHANCE, the number the Plan tab leads with.
        # This quoted the projection chart's own run -- one lognormal over the
        # whole book, no protective rules, no rebalancing -- so on 2026-10-11
        # Today said 35.3% while Plan said 16.3% (31% once the guard was
        # paused), for the same goal at the same moment. PR 99 promised one
        # figure everywhere; this was the screen it missed, and it is half the
        # health score. The goal model already counts active standing orders.
        gh = pj.get("goal_headline") or {}
        tail = f"₪{float(pj.get('target_amount') or 0):,.0f} in {pj.get('years')} years"
        p = None
        if gh.get("p_goal_pct") is not None:
            on_track, track_basis = float(gh["p_goal_pct"]), "goal_model"
            track_detail = (f"{on_track:g}% chance of {tail}, with your protective rules as they are"
                            + (f" ({float(gh['without_rules_pct']):g}% without them)"
                               if gh.get("without_rules_pct") is not None else ""))
            # PR 131 (D96): an old verdict is still the goal's chance -- quoted
            # with its age and why, never swapped for the rough no-rules run.
            if gh.get("current") is False:
                track_basis = "goal_model_stale"
                track_detail += (f". As of {_age_text(gh.get('age_hours'))}; "
                                 f"{gh.get('stale_text') or 'it is out of date'}, so it is being re-solved")
        elif wc.get("available") and wc.get("source") == "standing_order" \
                and wc.get("probability_meets_target") is not None:
            p, track_basis = float(wc["probability_meets_target"]), "standing_order"
        elif pj.get("probability_meets_target") is not None:
            p, track_basis = float(pj["probability_meets_target"]), "no_deposits"
        if p is not None:
            # No fresh goal verdict: the projection's figure, labelled as what
            # it is rather than passed off as the goal's chance.
            on_track = 100.0 * p
            track_detail = (f"{p:.1%} chance of {tail}"
                            + (" with your standing order" if track_basis == "standing_order"
                               else ", with no new deposits")
                            + " (rough estimate: protective rules not simulated)")
    except Exception:  # noqa: BLE001 -- On plan still stands on its own
        logger.warning("projection unavailable for the health score", exc_info=True)

    scores = {k: v["score"] for k, v in out_parts.items()}
    res = combine({"sleeves": s_sleeves, "mix": s_mix, "rules": s_rules,
                   "concentration": s_conc, "cash": s_cash}, on_track)
    return {**res, "parts": out_parts, "part_scores": scores, "weights": WEIGHTS,
            "on_track_detail": track_detail, "on_track_basis": track_basis,
            "nav_ils": round(nav, 2)}
