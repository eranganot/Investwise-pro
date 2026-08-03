"""Backtestable strategy specs for the "Beat the Market" family.

These are rules, not baskets. ``services/strategies.CATALOG`` describes static
allocations whose risk/return can be derived from a lookup table; a rule cannot
be described that way, so each spec here is fed to the backtest engine and its
numbers are *measured* over real price history.

Two structural decisions worth stating, because both were wrong first:

* ``base`` is the core holding, not cash. A swing setup is live perhaps 10-15%
  of the time; parking the rest in T-bills and reporting the ten-year CAGR
  measures a savings account with a strategy attached. Measured on TQQQ
  2016-2026, the identical dip-buy rule scored 4.15%/yr against T-bills and
  15.64%/yr against a QQQ core.
* Risk overlays gate on the instrument being **held**, not on the index. Vol
  targeting a TQQQ sleeve off QQQ's volatility produces a full weight every
  single day and quietly reproduces buy-and-hold.

``sleeve_pct`` is a suggested share of the portfolio, not a rule the app
enforces. A strategy with a strong per-trade edge that is only deployed a
fraction of the time earns a larger sleeve than its headline CAGR implies.
"""
from __future__ import annotations

GOAL = "Beat the Market"

_QQQ_CORE = {"QQQ": 1.0}

CATALOG: list[dict] = [
    {
        "id": "btm_trend_tqqq",
        "name": "Trend-Filtered Leveraged Nasdaq",
        "risk": "Very high",
        "sleeve_pct": 20,
        "horizon": "Hold for years; check weekly",
        "description": ("Holds 3x Nasdaq while QQQ is above its 200-day average, and "
                        "steps back to a plain QQQ core when it is not. Leverage's "
                        "penalty scales with volatility, and volatility clusters below "
                        "the long-term trend -- sitting out those stretches removes most "
                        "of the decay instead of paying it."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200,
                    "confirm_days": 3,
                    "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]},
    },
    {
        "id": "btm_trend_soxl",
        "name": "Trend-Filtered Semiconductors",
        "risk": "Very high",
        "sleeve_pct": 10,
        "horizon": "Hold for years; check weekly",
        "description": ("The same discipline applied to 3x semiconductors, gated on the "
                        "SMH sector index. Higher ceiling and a materially worse "
                        "drawdown than the Nasdaq version."),
        "weights": {"SOXL": 1.0}, "base": {"SMH": 1.0},
        "overlay": {"kind": "trend_filter", "gate_ticker": "SMH", "ma_days": 200,
                    "confirm_days": 3,
                    "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]},
    },
    {
        "id": "btm_vol_target_tqqq",
        "name": "Volatility-Targeted Leverage",
        "risk": "High",
        "sleeve_pct": 25,
        "horizon": "Hold for years; rebalances itself",
        "description": ("Scales leverage down as realized volatility rises, keeping risk "
                        "roughly constant instead of exposure constant. This attacks the "
                        "decay term directly rather than trying to time direction -- the "
                        "best-evidenced way to make leverage survivable."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "vol_target", "gate_ticker": "TQQQ", "vol_days": 20,
                    "target_vol_pct": 35, "max_weight": 1.0, "rebalance_band": 0.15,
                    "sweep_param": "target_vol_pct", "sweep_values": [25, 30, 35, 40, 45]},
    },
    {
        "id": "btm_swing_dip",
        "name": "Swing: Dip-Buy in an Uptrend",
        "risk": "High",
        "sleeve_pct": 15,
        "horizon": "Days to weeks per trade; check daily",
        "description": ("Buys the leveraged sleeve when it is short-term oversold, but "
                        "only while the index is above its 200-day, and exits on the "
                        "first strength. Capital sits in the core between setups. Judge "
                        "it on expectancy per trade, not on annual return -- it is only "
                        "deployed a fraction of the year."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "rsi_pullback", "gate_ticker": "QQQ", "signal_ticker": "TQQQ",
                    "ma_days": 200, "rsi_days": 2, "entry": 15, "exit_ma": 5,
                    "sweep_param": "entry", "sweep_values": [5, 10, 15, 20, 25]},
    },
    {
        "id": "btm_swing_breakout",
        "name": "Swing: Channel Breakout",
        "risk": "High",
        "sleeve_pct": 15,
        "horizon": "Weeks per trade; check daily",
        "description": ("Enters on a 20-day high and exits on a 10-day low -- two "
                        "parameters and a long published record, which makes it the "
                        "hardest of these setups to curve-fit."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "donchian", "gate_ticker": "QQQ", "entry_days": 20,
                    "exit_days": 10,
                    "sweep_param": "entry_days", "sweep_values": [10, 15, 20, 30, 40]},
    },
    {
        "id": "btm_factor_stack",
        "name": "Factor Stack",
        "risk": "Medium-high",
        "sleeve_pct": 40,
        "horizon": "10+ years",
        "description": ("Momentum, quality and small-cap value, unleveraged. The "
                        "academically documented route to beating a cap-weighted index; "
                        "the premia are real but modest, so this is the patient option "
                        "rather than the exciting one."),
        "weights": {"MTUM": 0.40, "QUAL": 0.35, "AVUV": 0.25},
        "overlay": {"kind": "buy_hold"},
    },
    {
        "id": "btm_dual_momentum",
        "name": "Dual Momentum Rotation",
        "risk": "Medium",
        "sleeve_pct": 30,
        "horizon": "Months per position; check monthly",
        "description": ("Rotates into whichever of US, international or bonds has the "
                        "strongest 12-month return, and into T-bills when none of them "
                        "is rising at all. Lower ceiling, much shallower drawdowns."),
        "weights": {"QQQ": 1.0}, "risk_off": "BIL",
        "overlay": {"kind": "dual_momentum", "universe": ["QQQ", "VTI", "VXUS"],
                    "lookback_days": 252, "risk_off": "BIL",
                    "sweep_param": "lookback_days", "sweep_values": [126, 189, 252, 315]},
    },
]

_BY_ID = {s["id"]: s for s in CATALOG}


def _spec(entry: dict) -> dict:
    """The engine only wants the mechanical parts; the prose stays behind."""
    keys = ("id", "weights", "base", "risk_off", "overlay")
    return {k: entry[k] for k in keys if k in entry}


def backtestable(only: list[str] | None = None) -> list[dict]:
    wanted = set(only) if only else None
    return [_spec(e) for e in CATALOG if wanted is None or e["id"] in wanted]


def get(strategy_id: str) -> dict | None:
    return _BY_ID.get(strategy_id)


def ids() -> list[str]:
    return [e["id"] for e in CATALOG]


# --- Presentation ----------------------------------------------------------
#
# The Plan renderer was written for static baskets: it reads `risk_tolerance`,
# `basket` as [ticker, weight] pairs and a `profile` of derived assumptions. A
# rule-based strategy has no derived profile -- it has a *measured* one -- so it
# is adapted to that shape here rather than the renderer growing a second code
# path for every field.

# Presentation risk label -> the vocabulary the existing cards already use.
_RISK_TOLERANCE = {"Very high": "High", "High": "High",
                   "Medium-high": "Medium", "Medium": "Medium"}


def as_plan_cards(measured: dict[str, dict] | None = None) -> list[dict]:
    """Catalog entries in the shape the Plan tab renders, with measured numbers.

    ``measured`` is keyed by strategy id (from ``backtest_service.get_many``).
    A strategy with no stored result still returns a card -- it simply carries
    ``backtest: None``, so the UI can say "not measured yet" instead of drawing
    a blank where a number belongs.
    """
    measured = measured or {}
    cards = []
    for e in CATALOG:
        weights = e.get("weights") or {}
        cards.append({
            "id": e["id"],
            "goal": GOAL,
            "name": e["name"],
            "description": e["description"],
            # Applying one of these sets a growth objective; the DB column is
            # String(16) and would not hold the goal label itself.
            "objective": "Grow",
            "risk_tolerance": _RISK_TOLERANCE.get(e.get("risk", ""), "High"),
            "risk_label": e.get("risk"),
            "sleeve_pct": e.get("sleeve_pct"),
            "horizon": e.get("horizon"),
            "basket": sorted((tk, w) for tk, w in weights.items()),
            "base_when_flat": sorted(e.get("base") or {}) or None,
            "rule": _rule_summary(e),
            "measured": True,
            "backtest": measured.get(e["id"]),
        })
    return cards


def _rule_summary(entry: dict) -> str:
    """One line of plain language describing what the rule actually does.

    The description sells the idea; this says the mechanism, so a card cannot
    imply a discipline it does not implement.
    """
    o = entry.get("overlay") or {}
    kind = o.get("kind", "buy_hold")
    base = ", ".join(sorted(entry.get("base") or {})) or o.get("risk_off") or entry.get("risk_off") or "cash"
    if kind == "buy_hold":
        return "Held throughout; rebalanced back to target weights."
    if kind == "trend_filter":
        return (f"Holds the sleeve while {o.get('gate_ticker')} is above its "
                f"{o.get('ma_days', 200)}-day average for {o.get('confirm_days', 1)} "
                f"session(s); otherwise {base}.")
    if kind == "rsi_pullback":
        return (f"Buys when {o.get('signal_ticker') or o.get('gate_ticker')} is short-term "
                f"oversold and {o.get('gate_ticker')} is above its {o.get('ma_days', 200)}-day; "
                f"exits on the first close above its {o.get('exit_ma', 5)}-day. Otherwise {base}.")
    if kind == "donchian":
        return (f"Enters on a {o.get('entry_days', 20)}-day high, exits on a "
                f"{o.get('exit_days', 10)}-day low. Otherwise {base}.")
    if kind == "vol_target":
        return (f"Scales the sleeve so realized volatility stays near "
                f"{o.get('target_vol_pct')}%, rebalancing when the target moves more "
                f"than {int(float(o.get('rebalance_band', 0.15)) * 100)} points. Remainder in {base}.")
    if kind == "dual_momentum":
        return (f"Each month holds whichever of {', '.join(o.get('universe') or [])} has the "
                f"strongest {int(o.get('lookback_days', 252) / 21)}-month return, or {base} "
                f"when none is rising.")
    if kind == "sector_momentum":
        return (f"Holds the top {o.get('top_n', 3)} of "
                f"{', '.join(o.get('universe') or [])} on trailing return, or {base}.")
    if kind == "ma_cross":
        return (f"Holds while the {o.get('fast')}-day average is above the "
                f"{o.get('slow')}-day; otherwise {base}.")
    return f"Rule: {kind}."


def as_legacy_strategy(strategy_id: str) -> dict | None:
    """The shape ``strategy_service`` expects, so apply/preview/load-basket work.

    Those helpers were written against ``services.strategies`` entries and read
    ``objective``, ``risk_tolerance``, ``target_allocation``, ``basket`` and
    ``preferred_depth``. Adapting here keeps one code path for applying a
    strategy rather than forking it by catalog.

    ``target_allocation`` is all-equity for every one of these: the rules move
    between an aggressive instrument and a core holding, both equity. Time spent
    in T-bills is a transient state of the rule, not a target the plan should
    hold the book to -- writing it into the target would make the allocation
    engine permanently demand a cash weight the strategy only wants sometimes.
    """
    e = _BY_ID.get(strategy_id)
    if e is None:
        return None
    weights = e.get("weights") or {}
    return {
        "id": e["id"], "goal": GOAL, "name": e["name"],
        "description": e["description"],
        "objective": "Grow",
        "risk_tolerance": _RISK_TOLERANCE.get(e.get("risk", ""), "High"),
        "preferred_depth": 3,
        "target_allocation": {"Equities": 1.0},
        "basket": sorted((tk, w) for tk, w in weights.items()),
    }
