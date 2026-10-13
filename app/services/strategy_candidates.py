"""The plan's pre-registered candidates that did not join the catalogue (PR 129, round 2 PR 130).

docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md, section 2, holds their settings and section 6 their
results: each failed the gate. They are kept HERE, as specs, so that what they would do to the owner's
whole book can be measured on demand (owner, 2026-10-13: "add to the test also the make the whole book
better parameter") and so a later re-measure runs exactly what was pre-registered. Nothing in this
module is offered as a sleeve, funded, or signalled.
"""
from __future__ import annotations

from copy import deepcopy

US44 = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'AVGO', 'ORCL', 'CRM', 'ADBE', 'AMD', 'JPM', 'BAC', 'V', 'MA', 'BRK-B', 'UNH', 'JNJ', 'LLY', 'PFE', 'MRK', 'ABBV', 'PG', 'KO', 'PEP', 'COST', 'WMT', 'HD', 'MCD', 'NKE', 'DIS', 'XOM', 'CVX', 'CAT', 'GE', 'HON', 'BA', 'T', 'VZ', 'NEE', 'CHKP', 'WIX', 'MNDY', 'GLOB']
SECTOR_ETFS = ['XLK', 'XLV', 'XLF', 'XLE', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB', 'XLRE']
GICS = {'XLK': ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'CRM', 'ADBE', 'AMD', 'CHKP', 'WIX', 'MNDY', 'GLOB'], 'XLF': ['JPM', 'BAC', 'V', 'MA', 'BRK-B'], 'XLV': ['UNH', 'JNJ', 'LLY', 'PFE', 'MRK', 'ABBV'], 'XLP': ['PG', 'KO', 'PEP', 'COST', 'WMT'], 'XLY': ['AMZN', 'HD', 'MCD', 'NKE'], 'XLE': ['XOM', 'CVX'], 'XLI': ['CAT', 'GE', 'HON', 'BA'], 'XLU': ['NEE'], 'XLB': [], 'XLRE': []}
LEVERAGED_BY_INDEX = {'SMH': 'SOXL', 'XLK': 'TECL', 'XLF': 'FAS', 'XLV': 'CURE', 'XLE': 'ERX', 'XBI': 'LABU', 'XLRE': 'DRN', 'QQQ': 'TQQQ', 'SPY': 'UPRO'}
_SPY_GATE = {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}
_QQQ = {"QQQ": 1.0}

CANDIDATES: list[dict] = [
    {"id": "btm_low_vol_quality", "code": "M1", "name": "Low-Volatility Quality Stocks", "risk": "Medium", "sleeve_pct": 25, "result": "failed the gate",
     **{"base": {}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "JPM", "BAC", "V", "MA", "BRK-B", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "DIS", "XOM", "CVX", "CAT", "GE", "HON", "BA", "T", "VZ", "NEE", "CHKP", "WIX", "MNDY", "GLOB"], "score": "low_vol", "vol_days": 252, "top_n": 12, "buffer": 24, "cadence_days": 63, "weighting": "inverse_vol", "sweep_param": "top_n", "sweep_values": [8, 10, 12, 15, 20]}, "weights": {}}},
    {"id": "btm_accel_dual_momentum", "code": "M2", "name": "Accelerating Dual Momentum", "risk": "Medium", "sleeve_pct": 30, "result": "failed the gate",
     **{"base": {}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["SPY", "SCZ"], "score": "blend", "top_n": 1, "buffer": 1, "cadence_days": 21, "require_positive": True, "fallback": ["TLT", "BIL"], "sweep_param": "cadence_days", "sweep_values": [10, 15, 21, 30, 42]}, "weights": {}}},
    {"id": "btm_sector_rotation", "code": "M3", "name": "Sector Rotation with Trend Veto", "risk": "Medium", "sleeve_pct": 25, "result": "failed the gate",
     **{"base": {"QQQ": 1.0}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE"], "score": "ret", "lookback_days": 126, "trend_veto_days": 200, "top_n": 3, "buffer": 5, "cadence_days": 21, "sweep_param": "lookback_days", "sweep_values": [63, 105, 126, 168, 252]}, "weights": {}}},
    {"id": "btm_momentum_10", "code": "MH1", "name": "Large-Cap Momentum 10", "risk": "Medium-high", "sleeve_pct": 25, "result": "failed the gate",
     **{"base": {"QQQ": 1.0}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "JPM", "BAC", "V", "MA", "BRK-B", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "DIS", "XOM", "CVX", "CAT", "GE", "HON", "BA", "T", "VZ", "NEE", "CHKP", "WIX", "MNDY", "GLOB"], "score": "mom_12_1", "top_n": 10, "buffer": 20, "cadence_days": 21, "market_gate": {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}, "sweep_param": "top_n", "sweep_values": [6, 8, 10, 12, 15]}, "weights": {}}},
    {"id": "btm_magic_formula", "code": "MH2", "name": "Magic Formula (US + Tel Aviv)", "risk": "Medium-high", "sleeve_pct": 25, "result": "failed the gate",
     **{"weights": {}, "proxy": {"ticker": "QVAL"}, "overlay": {"kind": "buy_hold"}}},
    {"id": "btm_quality_momentum_5", "code": "H1", "name": "Quality Momentum 5", "risk": "High", "sleeve_pct": 15, "result": "failed the gate",
     **{"base": {"QQQ": 1.0}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "JPM", "BAC", "V", "MA", "BRK-B", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "DIS", "XOM", "CVX", "CAT", "GE", "HON", "BA", "T", "VZ", "NEE", "CHKP", "WIX", "MNDY", "GLOB"], "score": "risk_adj_mom", "top_n": 5, "buffer": 10, "cadence_days": 21, "market_gate": {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}, "sweep_param": "top_n", "sweep_values": [3, 4, 5, 6, 8]}, "weights": {}}},
    {"id": "btm_52w_high", "code": "H2", "name": "52-Week-High Leaders", "risk": "High", "sleeve_pct": 15, "result": "failed the gate",
     **{"base": {"QQQ": 1.0}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "JPM", "BAC", "V", "MA", "BRK-B", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "DIS", "XOM", "CVX", "CAT", "GE", "HON", "BA", "T", "VZ", "NEE", "CHKP", "WIX", "MNDY", "GLOB"], "score": "high_52w", "top_n": 6, "buffer": 12, "cadence_days": 21, "market_gate": {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}, "sweep_param": "top_n", "sweep_values": [4, 5, 6, 8, 10]}, "weights": {}}},
    {"id": "btm_sector_leaders", "code": "H3", "name": "Top-Down Sector Leaders", "risk": "High", "sleeve_pct": 10, "result": "failed the gate",
     **{"base": {"QQQ": 1.0}, "drift_band": 0.1, "overlay": {"kind": "rank_select", "score": "mom_12_1", "cadence_days": 21, "two_stage": {"sectors": {"XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "CHKP", "WIX", "MNDY", "GLOB"], "XLF": ["JPM", "BAC", "V", "MA", "BRK-B"], "XLV": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV"], "XLP": ["PG", "KO", "PEP", "COST", "WMT"], "XLY": ["AMZN", "HD", "MCD", "NKE"], "XLE": ["XOM", "CVX"], "XLI": ["CAT", "GE", "HON", "BA"], "XLU": ["NEE"], "XLB": [], "XLRE": []}, "sector_lookback_days": 126, "sector_trend_days": 200, "min_names": 2, "top_sectors": 2, "per_sector": 2, "sector_exit_rank": 3, "stock_exit_rank": 4}, "sweep_param": "cadence_days", "sweep_values": [10, 15, 21, 30, 42]}, "weights": {}}},
    {"id": "btm_lev_risk_parity", "code": "VH1", "name": "Leveraged Risk Parity with Trend Brakes", "risk": "Very high", "sleeve_pct": 15, "result": "failed the gate",
     **{"weights": {"UPRO": 0.4, "TMF": 0.6}, "base": {}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "risk_parity", "legs": {"UPRO": "SPY", "TMF": "TLT"}, "cash": "BIL", "vol_days": 60, "trend_days": 200, "cadence_days": 21, "sweep_param": "vol_days", "sweep_values": [20, 40, 60, 90, 120]}}},
    {"id": "btm_lev_sector_momentum", "code": "VH2", "name": "Leveraged Sector Momentum", "risk": "Very high", "sleeve_pct": 10, "result": "failed the gate",
     **{"weights": {}, "base": {"QQQ": 1.0}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "rank_select", "universe": ["SMH", "XLK", "XLF", "XLV", "XLE", "XBI", "XLRE", "QQQ", "SPY"], "score": "blend", "trend_veto_days": 200, "top_n": 1, "buffer": 1, "cadence_days": 21, "switch_margin": 0.02, "hold_map": {"SMH": "SOXL", "XLK": "TECL", "XLF": "FAS", "XLV": "CURE", "XLE": "ERX", "XBI": "LABU", "XLRE": "DRN", "QQQ": "TQQQ", "SPY": "UPRO"}, "sweep_param": "cadence_days", "sweep_values": [10, 15, 21, 30, 42]}}},
    {"id": "btm_qqq_trend_cash", "code": "R1", "name": "Nasdaq Trend to Cash", "risk": "Medium", "sleeve_pct": 30, "result": "failed the gate (test 1)",
     **{"weights": {"QQQ": 1.0}, "base": {"BIL": 1.0}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200, "confirm_days": 3, "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]}}},
    {"id": "btm_qqq_vol_managed", "code": "R2", "name": "Volatility-Managed Nasdaq", "risk": "Medium", "sleeve_pct": 30, "result": "failed the gate (test 1)",
     **{"weights": {}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "vol_managed", "legs": {"cash": "BIL", "x1": "QQQ", "x2": "QLD", "x3": "TQQQ"}, "signal_ticker": "QQQ", "vol_days": 20, "target_vol_pct": 18, "max_exposure": 1.0, "rebalance_band": 0.15, "sweep_param": "target_vol_pct", "sweep_values": [14, 16, 18, 20, 22]}}},
    {"id": "btm_qqq_vol_managed_15", "code": "R3", "name": "Volatility-Managed Nasdaq to 1.5x", "risk": "Medium-high", "sleeve_pct": 25, "result": "failed the gate (test 1: not 25% shallower than QQQ)",
     **{"weights": {}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "vol_managed", "legs": {"cash": "BIL", "x1": "QQQ", "x2": "QLD", "x3": "TQQQ"}, "signal_ticker": "QQQ", "vol_days": 20, "target_vol_pct": 25, "max_exposure": 1.5, "rebalance_band": 0.15, "sweep_param": "target_vol_pct", "sweep_values": [20, 22.5, 25, 27.5, 30]}}},
    {"id": "btm_qld_trend_cash", "code": "R4", "name": "2x Nasdaq Trend to Cash", "risk": "Medium-high", "sleeve_pct": 20, "result": "passed; a near-duplicate of R7 in its tier with a weaker edge (D94)",
     **{"weights": {"QLD": 1.0}, "base": {"BIL": 1.0}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200, "confirm_days": 3, "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]}}},
    {"id": "btm_qqq_vol_managed_2x", "code": "R5", "name": "Volatility-Managed 2x Nasdaq", "risk": "High", "sleeve_pct": 15, "result": "passed; a near-duplicate of R6 in its tier with a weaker edge (D94)",
     **{"weights": {}, "drift_band": 0.1, "long_history": True, "overlay": {"kind": "vol_managed", "legs": {"cash": "BIL", "x1": "QQQ", "x2": "QLD", "x3": "TQQQ"}, "signal_ticker": "QQQ", "vol_days": 20, "target_vol_pct": 35, "max_exposure": 2.0, "rebalance_band": 0.15, "sweep_param": "target_vol_pct", "sweep_values": [30, 32.5, 35, 37.5, 40]}}},
]

_BY_ID = {c["id"]: c for c in CANDIDATES}


def get(strategy_id: str) -> dict | None:
    c = _BY_ID.get((strategy_id or "").strip())
    return deepcopy(c) if c else None


def spec(strategy_id: str) -> dict | None:
    """The engine's view: the mechanical keys only."""
    c = _BY_ID.get((strategy_id or "").strip())
    if not c:
        return None
    keys = ("id", "weights", "base", "risk_off", "overlay", "drift_band", "proxy", "long_history")
    return deepcopy({k: c[k] for k in keys if k in c})
