"""PR 134b: round 2, as the owner approved it on 2026-10-15
(docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md).

**Option 2 -- "hot stocks".** S1-S3 rank 115 of today's largest US stocks (US115; survivorship-biased, so gate
test S2 judges each against an equal-weight hold of the same universe). Each holds T-bills when SPY is below its
200-day for 3 closes. Judged by the Beat-the-Market gate (PR 127): S1 beats QQQ after tax; S2 positive selection
edge; S3 not "likely overfitted" across the 2022 split; S4 sweep within 5 points; S5 not a near-duplicate (0.90).
Recorded beside it: the book effect (D93) and the same-bet verdict (D97) against each sleeve held.

**Option 3 -- funded from the leveraged sleeve.** Every wave-3 candidate (D1-D13) and every S-candidate, taken out of
Volatility-Targeted Leverage at its suggested size AND at double (owner: "both"). F1: the whole book's after-tax
return still beats QQQ over the window; F2: the book-effect rule at the limits in force.

Pre-registered: measured as written, never edited after a result. Read-only: nothing stored, offered or traded.

Not financial advice.
"""
from __future__ import annotations

from copy import deepcopy

from app.engines import strategy_backtest as bt

US115 = [
    # Tech / communication
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "ORCL", "CRM", "ADBE", "AMD", "INTC", "CSCO",
    "QCOM", "TXN", "IBM", "NOW", "INTU", "AMAT", "MU", "LRCX", "KLAC", "ADI", "PANW", "CRWD", "SNPS", "CDNS", "ANET",
    "MRVL", "PLTR", "NFLX", "TMUS", "CMCSA", "DIS", "UBER", "SHOP", "ABNB",
    # Financials
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "AXP", "SCHW", "BLK", "SPGI", "PGR", "CB", "MMC",
    "ICE", "CME",
    # Healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "AMGN", "ISRG", "BSX", "SYK", "GILD", "VRTX",
    "REGN", "MDT", "BMY", "CVS", "ELV",
    # Consumer
    "WMT", "COST", "HD", "PG", "KO", "PEP", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG", "CMG", "MDLZ", "PM", "MO", "CL",
    # Industrials
    "GE", "CAT", "HON", "RTX", "UNP", "UPS", "BA", "DE", "LMT", "ETN", "GEV", "ADP",
    # Energy, utilities, materials, real estate
    "XOM", "CVX", "COP", "NEE", "SO", "DUK", "LIN", "SHW", "PLD", "AMT",
]
_GATE = {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}


def _rank(score: str, top: int, buffer: int, sweep: list[int]) -> dict:
    return {"weights": {}, "base": {"BIL": 1.0}, "drift_band": 0.1,
            "overlay": {"kind": "rank_select", "universe": list(US115), "score": score, "top_n": top,
                        "buffer": buffer, "cadence_days": 21, "market_gate": dict(_GATE),
                        "sweep_param": "top_n", "sweep_values": sweep}}


STOCK_CANDIDATES: list[dict] = [
    {"id": "r2_momentum_10", "code": "S1", "name": "Large-Cap Momentum 10", "expected_risk": "High",
     "sleeve_pct": 20, **_rank("mom_12_1", 10, 20, [6, 8, 10, 12, 15])},
    {"id": "r2_quality_momentum_5", "code": "S2", "name": "Quality Momentum 5", "expected_risk": "High to Very high",
     "sleeve_pct": 15, **_rank("risk_adj_mom", 5, 10, [3, 4, 5, 6, 8])},
    {"id": "r2_52w_high_leaders", "code": "S3", "name": "52-Week-High Leaders", "expected_risk": "High",
     "sleeve_pct": 15, **_rank("high_52w", 6, 12, [4, 5, 6, 8, 10])},
]
_META = {"code", "name", "expected_risk", "sleeve_pct"}
LEVERAGED_SLEEVE = "btm_vol_target_tqqq"     # the owner's: "Volatility-Targeted Leverage"
QQQ_HOLD = {"id": "qqq_hold", "weights": {"QQQ": 1.0}, "overlay": {"kind": "buy_hold"}}


def get(key: str) -> dict | None:
    k = (key or "").strip()
    return next((c for c in STOCK_CANDIDATES if c["id"] == k or c["code"].upper() == k.upper()), None)


def spec(key: str) -> dict | None:
    c = get(key)
    return None if c is None else {k: deepcopy(v) for k, v in c.items() if k not in _META}


def _any(key: str) -> tuple[dict | None, dict | None]:
    """(entry, spec) for an S- or D-candidate."""
    from app.services import diversifier_candidates as dc
    if get(key):
        return get(key), spec(key)
    if dc.get(key):
        return dc.get(key), dc.spec(key)
    return None, None


# --------------------------------------------------------------------------- #
# option 2
# --------------------------------------------------------------------------- #
def evaluate_stock_blocking(key: str, book: dict | None, *, ratio_gain: float, drop_tol: float,
                            fetch=None) -> dict:
    """One S-candidate against the Beat-the-Market gate (S5 is `near_duplicates_blocking`)."""
    from app.services import book_effect, sleeve_diversity as div, strategy_catalog
    from app.services.backtest_service import _fetch, measure

    entry, sp = get(key), spec(key)
    if entry is None:
        return {"ok": False, "reason": "UNKNOWN_CANDIDATE", "detail": f"{key!r} is not a round-2 stock candidate"}
    m = measure({**sp, "id": entry["id"]}, benchmark_ticker="QQQ")
    if not m.get("ok"):
        return {"ok": False, "code": entry["code"], "reason": m.get("reason"), "detail": m.get("detail")}
    rob = m.get("robustness") or {}
    oos = (rob.get("out_of_sample") or {}).get("verdict")
    sw = rob.get("sweep") or {}
    tests = {
        "S1": {"pass": (m.get("benchmark_cagr_pct") is not None and m["cagr_pct"] > m["benchmark_cagr_pct"]),
               "cagr_pct": m["cagr_pct"], "qqq_cagr_pct": m.get("benchmark_cagr_pct")},
        "S2": {"pass": (m.get("selection_edge_pct") or 0) > 0, "selection_edge_pct": m.get("selection_edge_pct")},
        "S3": {"pass": oos is not None and oos != "likely overfitted", "verdict": oos},
        "S4": {"pass": bool(sw.get("robust")), "spread_pct": sw.get("cagr_spread_pct")},
    }
    be = (book_effect.compare_blocking(book, {**sp, "id": entry["id"]}, float(entry["sleeve_pct"]), fetch,
                                       ratio_gain=ratio_gain, drop_tol=drop_tol)
          if book else {"ok": False, "reason": "NO_BOOK"})
    # D97 against each sleeve held: shared market from the profiles, correlation
    # from the rebuilt daily returns on one series.
    same = []
    fetch = fetch or _fetch
    for s in (book or {}).get("sleeves") or []:
        ov, markets = div.overlap_pct(sp, s["spec"])
        need = sorted(set(bt.tickers_needed(sp)) | set(bt.tickers_needed(s["spec"])))
        series, _ = fetch(need)
        ra, rb = div.daily_returns(series, sp), div.daily_returns(series, s["spec"])
        rho = div.correlation(ra, rb) if ra and rb else None
        lim_o, lim_c = div._limits()
        same.append({"sleeve": s["id"], "overlap_pct": ov, "markets": markets,
                     "correlation": round(rho, 3) if rho is not None else None,
                     "same_bet": ov >= lim_o or (rho is not None and rho >= lim_c)})
    passes = all(t["pass"] for t in tests.values())
    return {"ok": True, "code": entry["code"], "id": entry["id"], "name": entry["name"],
            "sleeve_pct": entry["sleeve_pct"], "expected_risk": entry["expected_risk"],
            "measured_risk": strategy_catalog.measured_risk(m.get("max_drawdown_pct")),
            "window": {"cagr_pct": m["cagr_pct"], "max_drawdown_pct": m.get("max_drawdown_pct"),
                       "start": m.get("start"), "end": m.get("end")},
            "tests": tests, "passes_before_s5": passes,
            "book_effect": be if be.get("ok") else None, "same_bet": same}


def near_duplicates_blocking(fetch=None) -> dict:
    """S5: S-candidate pairs whose daily returns correlate above 0.90."""
    from app.services import sleeve_diversity as div
    from app.services.backtest_service import _fetch

    fetch = fetch or _fetch
    specs = {c["code"]: spec(c["id"]) for c in STOCK_CANDIDATES}
    need: set[str] = set()
    for s in specs.values():
        need |= set(bt.tickers_needed(s))
    series, missing = fetch(sorted(need))
    rets = {k: div.daily_returns(series, s) for k, s in specs.items()}
    out, ks = [], sorted(specs)
    for n, a in enumerate(ks):
        for b in ks[n + 1:]:
            rho = div.correlation(rets[a], rets[b]) if rets.get(a) and rets.get(b) else None
            if rho is not None and rho > 0.90:
                out.append({"a": a, "b": b, "correlation": round(rho, 3)})
    return {"near_duplicates": out, "missing_tickers": sorted(missing or [])}


# --------------------------------------------------------------------------- #
# option 3
# --------------------------------------------------------------------------- #
def evaluate_funding_blocking(key: str, book: dict, *, scale: float, ratio_gain: float, drop_tol: float,
                              fetch=None) -> dict:
    """One candidate taken out of the leveraged sleeve at `scale` x its suggested size."""
    from app.services import book_effect

    entry, sp = _any(key)
    if entry is None:
        return {"ok": False, "reason": "UNKNOWN_CANDIDATE", "detail": f"{key!r} is not a candidate"}
    size = float(entry["sleeve_pct"]) * float(scale)
    b = dict(book, bench_tk="QQQ")        # F1 reads QQQ's return over the same window
    be = book_effect.compare_blocking(b, {**sp, "id": entry["id"]}, size, fetch,
                                      ratio_gain=ratio_gain, drop_tol=drop_tol, fund_from=LEVERAGED_SLEEVE)
    if not be.get("ok"):
        return {"ok": False, "code": entry["code"], "scale": scale, "reason": be.get("reason"),
                "detail": be.get("detail")}
    w, wo = be["with"], be["without"]
    qqq = w.get("benchmark_cagr_pct")
    f1 = qqq is not None and w["cagr_pct"] > qqq
    return {"ok": True, "code": entry["code"], "name": entry["name"], "scale": scale,
            "moved_pct": be["sleeve_pct"], "from": LEVERAGED_SLEEVE,
            "book_without": wo, "book_with": w, "qqq_cagr_pct": qqq,
            "tests": {"F1": {"pass": f1}, "F2": {"pass": be["improves"], "failed": be.get("failed"),
                                                 "gain": be.get("return_per_risk_gain"),
                                                 "deeper_pts": be.get("worst_drop_change_pts")}},
            "passes": bool(f1 and be["improves"])}
