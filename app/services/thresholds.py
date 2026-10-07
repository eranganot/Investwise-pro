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
}


def defaults() -> dict:
    """Today's values, read from the constants that own them."""
    from app.services import frontier_service as fs
    from app.services import funding_service as fund
    from app.services import headsup
    from app.services import recommendations as rec
    from app.services.advanced_rules import DEFAULTS as ADV
    return {"min_card_tax_ils": headsup.MIN_TAX_SAVING_ILS,
            "min_card_trade_ils": headsup.MIN_TRADE_ILS,
            "min_card_trade_pct": headsup.MIN_TRADE_PCT_OF_BOOK,
            "min_funded_trade_ils": fund.MIN_TRADE_DEFAULT_ILS,
            "cash_floor_ils": fund.CASH_HARD_FLOOR_DEFAULT_ILS,
            "rebalance_band_pts": ADV["rebalance_band"],
            "take_profit_fraction_pct": 25.0,
            "gain_line_pct": rec._GAIN_CARD_PCT,
            "material_odds_pts": fs.MATERIAL_PROB_PTS,
            "guard_levels": list(fs.GUARD_LEVELS)}


def clean(raw: dict | None) -> dict:
    """Only known keys, inside their bounds. Raises ValueError on a bad value."""
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in SPEC:
            raise ValueError(f"unknown threshold: {k}")
        if v is None:
            continue
        lo, hi = SPEC[k][2], SPEC[k][3]
        if k == "guard_levels":
            vals = sorted({round(float(x), 2) for x in (v or [])})
            if not 1 <= len(vals) <= 4 or any(not lo <= x <= hi for x in vals):
                raise ValueError(f"{k}: one to four levels between {lo:g} and {hi:g}")
            out[k] = vals
        else:
            x = float(v)
            if not lo <= x <= hi:
                raise ValueError(f"{k}: must be between {lo:g} and {hi:g}")
            out[k] = x
    return out


def overrides_of(plan) -> dict:
    try:
        return clean(getattr(plan, "thresholds", None) or {})
    except ValueError:
        return {}


def effective(plan) -> dict:
    """Every threshold as it applies to this plan: the override, else the default."""
    return {**defaults(), **overrides_of(plan)}


def use(plan) -> None:
    """Set the overrides for the user being served (this request / job pass)."""
    _OVERRIDES.set(overrides_of(plan))


def get(key: str, default):
    """The override for `key` if this context has one, else `default`."""
    o = _OVERRIDES.get()
    if o and key in o:
        return o[key]
    return default
