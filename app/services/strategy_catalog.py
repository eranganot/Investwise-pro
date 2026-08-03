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
