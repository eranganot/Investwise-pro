"""PR 104d: the card thresholds are settings (Eran, 2026-10-07: "I want every
decision to be controlled and changeable through the app").

Each threshold keeps its DEFAULT where it always lived (a module constant beside
the code that uses it); `plans.thresholds` (migration 0028) holds the user's
OVERRIDES. The overrides for the user being served are set once per request /
job (`use(plan)`) in a context variable, and every reader asks
`get(key, its_default)` -- so code that runs without a plan in hand (the funding
planner, rule firing, the goal solve's worker threads) still honours them, and
a context with nothing set behaves exactly as before.
"""
from __future__ import annotations

from contextvars import ContextVar

_OVERRIDES: ContextVar[dict | None] = ContextVar("iw_threshold_overrides", default=None)

# key -> (label, what it decides, lower bound, upper bound). Defaults come from the
# constants themselves (`defaults()`), never a second copy here.
SPEC: dict[str, tuple[str, str, float, float]] = {
    "min_card_tax_ils": ("Smallest tax-harvest card (₪ saved)",
                         "A harvest that saves less than this is not offered.", 0.0, 10_000.0),
    "min_card_trade_ils": ("Smallest trade card (₪)",
                           "A trade card smaller than this (and than the % below) becomes a heads-up.", 0.0, 100_000.0),
    "min_card_trade_pct": ("Smallest trade card (% of the book)",
                           "The larger of this and the ₪ amount above is the floor.", 0.0, 10.0),
    "min_funded_trade_ils": ("Smallest funded trade (₪)",
                             "No buy, sale or funding leg smaller than this is planned.", 1.0, 100_000.0),
    "cash_floor_ils": ("Cash hard floor (₪)",
                       "Cash never spent below this, whatever the card.", 0.0, 1_000_000.0),
    "rebalance_band_pts": ("Rebalance band (± points)",
                           "How far a holding may drift from its target before a band rule acts; also sets "
                           "\"raise the cap to target + band\".", 0.5, 20.0),
    "take_profit_fraction_pct": ("Take-profit slice (% of the position)",
                                 "What a take-profit sells when its level is hit (a rule's own size wins).", 5.0, 100.0),
    "gain_line_pct": ("Gain worth a line (% since you bought)",
                      "A holding up this much gets a \"+X% since you bought\" note.", 5.0, 500.0),
    "material_odds_pts": ("Smallest goal-odds gain worth a card (points)",
                          "A goal change that moves the odds less than this is not offered.", 0.5, 50.0),
    "guard_levels": ("Drawdown-guard levels the retune tries (%)",
                     "The guard levels compared when the app looks for better protection.", 1.0, 90.0),
    # PR 121: the guard's other two dials, and the values the retune tries for each.
    "guard_sell_fraction_pct": ("Drawdown guard sells (% of the most volatile holding)",
                                "What the guard sells when the book falls past its level.", 5.0, 100.0),
    "guard_rearm_gap_pts": ("Drawdown guard re-arms (points under its level)",
                            "The guard re-arms once the drawdown is this far back under its level "
                            "(never more than half way).", 0.5, 50.0),
    "guard_fractions_tried": ("Guard sell sizes the retune tries (%)",
                              "The guard sell sizes compared when the app looks for better protection.", 5.0, 100.0),
    "guard_rearm_gaps_tried": ("Guard re-arm gaps the retune tries (points)",
                               "The re-arm gaps compared when the app looks for better protection.", 0.5, 50.0),
    # PR 115 (D-d, owner 2026-10-11: "all of them become editable"). Each default
    # still lives beside the code that uses it.
    # PR 148 (owner, 2026-10-23: 44%): a goal-level limit on the 2x/3x bet.
    "leveraged_ceiling_pct": ("Largest 2x/3x share of the book (%)",
                              "No app buy takes the 2x/3x funds (TQQQ, SOXL, UPRO, ...) past this share of the "
                              "book; above it, Today offers a trim. 0 = no ceiling.", 0.0, 100.0),
    # C18 (owner, 2026-10-23): switched-off protection comes back on by itself.
    "rearm_after_days": ("Re-arm switched-off protection after (days)",
                         "A stop, trailing stop, profit-protect, trend break or drawdown guard that has been off "
                         "this long is switched back on -- never one whose price is already past it. 0 = never.",
                         0.0, 60.0),
    "concentration_cap_pct": ("Largest single holding (% of the book)",
                              "No buy takes one name past this; a holding above it gets a trim card. "
                              "Default follows your risk tolerance.", 5.0, 100.0),
    "sleeve_drift_band_pts": ("Sleeve drift band (± points)",
                              "How far a sleeve may drift from the size you chose before Today asks to resize it.",
                              0.5, 30.0),
    # PR 133: the book-effect rule (D93) -- "improves your book" on every strategy card.
    "book_effect_ratio_gain": ("A strategy improves your book at (return per unit of risk gained)",
                               "Adding it must raise the whole book's return per unit of volatility by at "
                               "least this much.", 0.0, 0.5),
    "book_effect_drop_tolerance_pts": ("…and deepens its worst drop by no more than (points)",
                                       "Adding it may make the whole book's worst drop at most this many "
                                       "points deeper.", 0.0, 30.0),
    # PR 136a: the goal optimiser keeps a step only if it gains at least this much.
    "optimizer_min_step_pts": ("Smallest step the goal optimiser keeps (points of goal chance)",
                               "When the app searches for the best book for your goal, a change is kept only if "
                               "it raises the chance by at least this much.", 0.1, 20.0),
    # PR 132 (D97): every sleeve must be a different bet.
    "sleeve_overlap_alert_pct": ("Two sleeves are the same bet at (% shared market)",
                                 "Sleeves whose holdings are bets on the same market (TQQQ and QQQ are both the "
                                 "Nasdaq-100) for at least this share of the smaller one count as one bet.", 5.0, 100.0),
    "sleeve_correlation_limit": ("Two sleeves are the same bet at (correlation)",
                                 "Sleeves whose daily returns move together at least this much (0-1, measured over "
                                 "ten years) count as one bet.", 0.3, 1.0),
    "min_resize_pts": ("Smallest sleeve resize worth a card (points)",
                       "A goal-solver resize smaller than this is not offered.", 0.1, 20.0),
    "material_gap_close_pct": ("Resize must close at least (% of the gap)",
                               "A resize earns its card if it closes this much of the gap to the goal...", 1.0, 100.0),
    "material_vol_drop_pts": ("...or cut volatility by at least (points)",
                              "...or lowers the book's volatility by this much.", 0.1, 30.0),
    "ignore_mute_days": ("Ignore hides a card for (days)",
                         "How long Ignore keeps a card off Today.", 1.0, 90.0),
    "done_mute_days": ("Done hides a card for (days)",
                       "How long Done keeps a card off Today (a HIGH card uses the Ignore window).", 1.0, 180.0),
    "refire_change_pct": ("A hidden card returns if its number moves (%)",
                          "A muted card comes back early when its amount changes by more than this.", 1.0, 500.0),
    "near_rule_pct": ("\"Close to your take-profit\" within (%)",
                      "How near the price must be to a take-profit level for a heads-up.", 1.0, 50.0),
    "near_trail_giveback_pct": ("\"Closing on your trailing stop\" after giving back (%)",
                                "How much of the trail's room must be used up before a heads-up.", 10.0, 100.0),
    "near_cap_pts": ("\"Nearly at your cap\" within (points)",
                     "How close a holding must be to its max-weight cap for a heads-up.", 0.1, 20.0),
    "ideas_shown": ("Buy ideas listed", "How many screener ideas the cash card lists.", 1.0, 20.0),
    # D134: commodities live inside the core; this carves a fixed share out of it (PR 71's 10%).
    "commodities_carve_out_pct": ("Commodities carved out of your core (% of the book)",
                                  "0 = your core's own basket decides commodities, and a commodity bet beyond it is "
                                  "a sleeve. 10 restores the fixed 10% Commodities slice beside the core.", 0.0, 50.0),
    # PR 116 (D80): 1 = when a core is chosen, commodity buys use its commodity
    # tickers (All-Weather: IAU, DBC); 0 = the best-ranked commodity of all.
    "commodity_buys_core_only": ("Commodity buys from your core only (1 = yes, 0 = any)",
                                 "With a core chosen, commodity cards buy only the core's commodity funds "
                                 "(e.g. IAU, DBC), the ones your goal is modelled on.", 0.0, 1.0),
    "max_idea_cards": ("Buy ideas that become cards", "How many screener names may become cards on Today.",
                       0.0, 10.0),
}

# List settings: one to four values, each inside the bounds.
LISTS = {"guard_levels", "guard_fractions_tried", "guard_rearm_gaps_tried"}

# Whole-number settings: stored and returned as int.
_INTEGER = {"ideas_shown", "max_idea_cards", "ignore_mute_days", "done_mute_days", "commodity_buys_core_only"}


def defaults(plan=None) -> dict:
    """Today's values, read from the constants that own them.

    `plan` only matters for `concentration_cap_pct`, whose default is the row of
    your risk tolerance (Low 15 / Medium 25 / High 40).
    """
    from app.services import frontier_service as fs
    from app.services import funding_service as fund
    from app.services import headsup
    from app.services import recommendations as rec
    from app.services import book_effect as bfx
    from app.services import book_optimizer as bopt
    from app.services import book_target as _bt
    from app.services import sleeve_diversity as div
    from app.services.advanced_rules import DEFAULTS as ADV
    from app.services.advanced_rules import DERISK_FRACTION, REARM_GAP_PTS
    from app.services.plan_service import RISK_CAPS
    _row = RISK_CAPS.get(getattr(plan, "risk_tolerance", None) or "Medium", RISK_CAPS["Medium"])
    from app.services.leveraged_ceiling import CEILING_PCT
    from app.services.rules_service import REARM_AFTER_DAYS
    return {"leveraged_ceiling_pct": CEILING_PCT,
            "rearm_after_days": REARM_AFTER_DAYS,
            "concentration_cap_pct": round(_row["concentration_cap"] * 100.0, 2),
            "sleeve_drift_band_pts": rec.SLEEVE_DRIFT_BAND_PCT,
            "optimizer_min_step_pts": bopt.MIN_STEP_PTS,
            "book_effect_ratio_gain": bfx.RATIO_GAIN,
            "book_effect_drop_tolerance_pts": bfx.DD_TOLERANCE_PTS,
            "sleeve_overlap_alert_pct": div.OVERLAP_ALERT_PCT,
            "sleeve_correlation_limit": div.CORRELATION_LIMIT,
            "min_resize_pts": fs.MIN_MOVE_PCT,
            "material_gap_close_pct": round(fs.MATERIAL_GAP_CLOSE * 100.0, 2),
            "material_vol_drop_pts": fs.MATERIAL_VOL_DROP_PCT,
            "ignore_mute_days": rec._DISMISS_TTL_DAYS,
            "done_mute_days": rec._DONE_TTL_DAYS,
            "refire_change_pct": round(rec._REFIRE_DELTA * 100.0, 2),
            "near_rule_pct": round(rec._NEAR_RULE_FRAC * 100.0, 2),
            "near_trail_giveback_pct": round(rec._TRAIL_GIVEBACK * 100.0, 2),
            "near_cap_pts": rec._NEAR_CAP_PCT_POINTS,
            "ideas_shown": rec.IDEAS_SHOWN,
            "max_idea_cards": rec._MAX_BUY_IDEA_CARDS,
            "commodity_buys_core_only": rec.COMMODITY_BUYS_CORE_ONLY,
            "commodities_carve_out_pct": _bt.COMMODITIES_CARVE_OUT_PCT,
            "min_card_tax_ils": headsup.MIN_TAX_SAVING_ILS,
            "min_card_trade_ils": headsup.MIN_CARD_TRADE_ILS,
            "min_card_trade_pct": headsup.MIN_TRADE_PCT_OF_BOOK,
            "min_funded_trade_ils": fund.MIN_TRADE_DEFAULT_ILS,
            "cash_floor_ils": fund.CASH_HARD_FLOOR_DEFAULT_ILS,
            "rebalance_band_pts": ADV["rebalance_band"],
            "take_profit_fraction_pct": 25.0,
            "gain_line_pct": rec._GAIN_CARD_PCT,
            "material_odds_pts": fs.MATERIAL_PROB_PTS,
            "guard_levels": list(fs.GUARD_LEVELS),
            "guard_sell_fraction_pct": DERISK_FRACTION * 100.0,
            "guard_rearm_gap_pts": REARM_GAP_PTS,
            "guard_fractions_tried": list(fs.GUARD_FRACTIONS),
            "guard_rearm_gaps_tried": list(fs.GUARD_REARM_GAPS)}


def clean(raw: dict | None) -> dict:
    """Only known keys, inside their bounds. Raises ValueError on a bad value."""
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in SPEC:
            raise ValueError(f"unknown threshold: {k}")
        if v is None:
            continue
        lo, hi = SPEC[k][2], SPEC[k][3]
        if k in LISTS:
            vals = sorted({round(float(x), 2) for x in (v or [])})
            if not 1 <= len(vals) <= 4 or any(not lo <= x <= hi for x in vals):
                raise ValueError(f"{k}: one to four levels between {lo:g} and {hi:g}")
            out[k] = vals
        else:
            x = float(v)
            if not lo <= x <= hi:
                raise ValueError(f"{k}: must be between {lo:g} and {hi:g}")
            out[k] = int(round(x)) if k in _INTEGER else x
    return out


def overrides_of(plan) -> dict:
    try:
        return clean(getattr(plan, "thresholds", None) or {})
    except ValueError:
        return {}


def effective(plan) -> dict:
    """Every threshold as it applies to this plan: the override, else the default."""
    return {**defaults(plan), **overrides_of(plan)}


def card_floor_ils(nav: float) -> float:
    """THE smallest trade a card may ask for (PR 115, D-c): the largest of the
    card's ₪ and %-of-book minimums AND the smallest funded trade.

    There were two minimums. A card could be ₪100+ while funding refused any
    leg under ₪250, so below a ~₪50k book the ₪100 setting was inert and a
    commodity card for ₪180 degraded into an un-actable heads-up. One number now.
    """
    from app.services import headsup
    from app.services.funding_service import min_trade_ils
    return max(headsup.min_trade_ils(nav), float(min_trade_ils()))


def use(plan) -> None:
    """Set the overrides for the user being served (this request / job pass)."""
    _OVERRIDES.set(overrides_of(plan))


def get(key: str, default):
    """The override for `key` if this context has one, else `default`."""
    o = _OVERRIDES.get()
    if o and key in o:
        return o[key]
    return default
