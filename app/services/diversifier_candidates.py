"""PR 133: the wave-3 candidates, exactly as the owner approved them on 2026-10-15.

docs/plans/DIVERSIFIERS_2026-10-15.md holds each one's rule in words, its expected risk tier and the gate. These
are PRE-REGISTERED: measured as written, never edited after a result -- a changed spec is a new candidate. Nothing
here is offered as a sleeve; a candidate joins `strategy_catalog` only after it passes (PR 134a).

`expected_risk` is the tier the owner saw before measurement; the card always shows the measured one (PR 125).
`short_history`: the fund is younger than ~10 years and has no index to rebuild it from (gate G4 becomes a badge).
"""
from __future__ import annotations

from copy import deepcopy

_TREND = {"kind": "trend_filter", "ma_days": 200, "confirm_days": 3,
          "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]}


def _trend(on: str, gate: str, off: str = "BIL") -> dict:
    return {"weights": {on: 1.0}, "base": {off: 1.0}, "overlay": {**_TREND, "gate_ticker": gate}}


CANDIDATES: list[dict] = [
    # ---- Family A: other assets, held only while they trend ----------------
    {"id": "div_gtaa5", "code": "D1", "family": "A", "name": "Global Tactical Allocation (GTAA-5)",
     "expected_risk": "Medium", "sleeve_pct": 20,
     "weights": {}, "base": {"BIL": 1.0},
     "overlay": {"kind": "rank_select", "universe": ["SPY", "EFA", "IEF", "DBC", "VNQ"], "score": "ret",
                 "lookback_days": 252, "top_n": 5, "buffer": 5, "trend_veto_days": 200, "cadence_days": 21,
                 "sweep_param": "trend_veto_days", "sweep_values": [150, 175, 200, 225, 250]}},
    {"id": "div_trend_risk_parity", "code": "D2", "family": "A", "name": "Trend Risk Parity",
     "expected_risk": "Medium", "sleeve_pct": 20,
     "weights": {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "DBC": 0.25},
     "overlay": {"kind": "risk_parity", "legs": {"SPY": "SPY", "TLT": "TLT", "GLD": "GLD", "DBC": "DBC"},
                 "vol_days": 60, "trend_days": 200, "confirm_days": 1, "cadence_days": 21, "cash": "BIL",
                 "sweep_param": "vol_days", "sweep_values": [20, 40, 60, 90, 120]}},
    {"id": "div_gold_trend", "code": "D3", "family": "A", "name": "Gold Trend",
     "expected_risk": "Medium", "sleeve_pct": 10, **_trend("GLD", "GLD")},
    {"id": "div_treasury_trend", "code": "D4", "family": "A", "name": "Treasury Trend",
     "expected_risk": "Medium", "sleeve_pct": 15, **_trend("IEF", "IEF")},
    {"id": "div_intl_trend", "code": "D5", "family": "A", "name": "Non-US Stocks Trend",
     "expected_risk": "Medium to Medium-high", "sleeve_pct": 15, **_trend("EFA", "EFA")},
    {"id": "div_gem", "code": "D6", "family": "A", "name": "Global Equity Momentum (GEM)",
     "expected_risk": "Medium-high", "sleeve_pct": 20,
     "weights": {}, "base": {},
     "overlay": {"kind": "rank_select", "universe": ["SPY", "EFA"], "score": "ret", "lookback_days": 252,
                 "top_n": 1, "buffer": 1, "require_positive": True, "fallback": ["IEF"], "cadence_days": 21,
                 "sweep_param": "lookback_days", "sweep_values": [126, 189, 252, 315, 378]}},
    # ---- Family B: managed futures, held as a fund -------------------------
    {"id": "div_kmlm", "code": "D7", "family": "B", "name": "Managed Futures (KMLM)",
     "expected_risk": "Medium", "sleeve_pct": 15, "short_history": True,
     "weights": {"KMLM": 1.0}, "overlay": {"kind": "buy_hold"}},
    {"id": "div_dbmf", "code": "D8", "family": "B", "name": "Managed Futures (DBMF)",
     "expected_risk": "Medium", "sleeve_pct": 15, "short_history": True,
     "weights": {"DBMF": 1.0}, "overlay": {"kind": "buy_hold"}},
    # ---- Family C: profit from falls through -1x funds ---------------------
    {"id": "div_qqq_trend_short", "code": "D9", "family": "C", "name": "Nasdaq Trend, Short in Downtrends",
     "expected_risk": "High", "sleeve_pct": 15, **_trend("QQQ", "QQQ", off="PSQ")},
    {"id": "div_spy_trend_short", "code": "D10", "family": "C", "name": "S&P Trend, Short in Downtrends",
     "expected_risk": "High", "sleeve_pct": 15, **_trend("SPY", "SPY", off="SH")},
    # ---- Family D: leveraged diversifiers ----------------------------------
    {"id": "div_2x_trend_risk_parity", "code": "D11", "family": "D", "name": "2x Trend Risk Parity",
     "expected_risk": "High to Very high", "sleeve_pct": 15,
     "weights": {"SSO": 1 / 3, "UBT": 1 / 3, "UGL": 1 / 3},
     "overlay": {"kind": "risk_parity", "legs": {"SSO": "SPY", "UBT": "TLT", "UGL": "GLD"},
                 "vol_days": 60, "trend_days": 200, "confirm_days": 1, "cadence_days": 21, "cash": "BIL",
                 "sweep_param": "vol_days", "sweep_values": [20, 40, 60, 90, 120]}},
    {"id": "div_2x_gold_trend", "code": "D12", "family": "D", "name": "2x Gold Trend",
     "expected_risk": "High", "sleeve_pct": 10, **_trend("UGL", "GLD")},
    {"id": "div_3x_treasury_trend", "code": "D13", "family": "D", "name": "3x Treasury Trend",
     "expected_risk": "Very high", "sleeve_pct": 10, **_trend("TMF", "TLT")},
]

_META = {"code", "family", "name", "expected_risk", "sleeve_pct", "short_history"}


def get(key: str) -> dict | None:
    """By id or by code (D1..D13)."""
    k = (key or "").strip()
    return next((c for c in CANDIDATES if c["id"] == k or c["code"].upper() == k.upper()), None)


def spec(key: str) -> dict | None:
    """The engine spec: what `strategy_backtest` runs. Long history on for G4."""
    c = get(key)
    if c is None:
        return None
    out = {k: deepcopy(v) for k, v in c.items() if k not in _META}
    out.setdefault("base", {})
    out["long_history"] = not c.get("short_history")
    return out
