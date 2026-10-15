"""PR 133: the wave-3 gate, as the owner approved it on 2026-10-15.

docs/plans/DIVERSIFIERS_2026-10-15.md, "The gate". Owner: "the main goal is not only to diversify but to beat the
market (diversification is just a plus)". So:

  B1  beats the market (required): after tax above QQQ over the window, OR it raises the whole book's after-tax
      return when added at its suggested size with the core making room
  G2  improves your book (required): the book-effect rule (D93), at the limits in force (settings)
  G3  earns its place (required): after tax above T-bills, worst drop shallower than QQQ's, same window
  G4  holds up (required, or a "short history" badge): G3 over the longest history the funds allow
  G5  robust (required): the parameter sweep stays within 5 points (n/a for a plain fund)
  G6  not a near-duplicate (required): correlated above 0.90 with another candidate -> only the better G2 ships
  G1  different engine (badge): daily-return correlation with QQQ <= 0.50

Every comparison inside one test runs on ONE series dict, so the candidate, QQQ and T-bills share one date window
(`run` aligns every series it is handed). Read-only: nothing is stored or offered.

Not financial advice.
"""
from __future__ import annotations

from app.engines import strategy_backtest as bt
from app.services import diversifier_candidates as dc

QQQ_HOLD = {"id": "qqq_hold", "weights": {"QQQ": 1.0}, "overlay": {"kind": "buy_hold"}}
BIL_HOLD = {"id": "bil_hold", "weights": {"BIL": 1.0}, "overlay": {"kind": "buy_hold"}}
DIFFERENT_MAX_CORR = 0.50
NEAR_DUPLICATE_CORR = 0.90
SWEEP_MAX_SPREAD = 5.0
SHORT_HISTORY_YEARS = 9.5


def _row(r: dict) -> dict | None:
    if not r.get("ok"):
        return None
    return {"cagr_pct": r.get("cagr_pct"), "max_drawdown_pct": r.get("max_drawdown_pct"),
            "start": r.get("start"), "end": r.get("end"), "years": r.get("years")}


def _three(series: dict, spec: dict) -> tuple[dict, dict | None, dict | None]:
    """The candidate, QQQ and T-bills on one window."""
    return bt.run(series, spec), _row(bt.run(series, QQQ_HOLD)), _row(bt.run(series, BIL_HOLD))


def _earns(c: dict | None, q: dict | None, b: dict | None) -> bool | None:
    if not (c and q and b):
        return None
    return c["cagr_pct"] > b["cagr_pct"] and c["max_drawdown_pct"] < q["max_drawdown_pct"]


def _long(spec: dict) -> dict:
    """G4: the candidate, QQQ and T-bills over the longest history, on one rebuilt series."""
    from app.services.backtest_service import LONG_END, _fetch_long

    need = set(bt.tickers_needed(spec)) | {"QQQ", "BIL"}
    real = {bt.RATE_TICKER}
    for tk in need:
        if tk in bt.SYNTHETIC:
            real.add(bt.SYNTHETIC[tk][0])
        elif tk not in bt.CASH_LIKE:
            real.add(tk)
    got, missing = _fetch_long(sorted(real))
    if bt.RATE_TICKER in missing:
        return {"ok": False, "detail": "no T-bill history"}
    rate = got[bt.RATE_TICKER]
    series = {}
    for tk in need:
        if tk in bt.SYNTHETIC and got.get(bt.SYNTHETIC[tk][0]):
            series[tk] = bt.synthetic_leveraged(got[bt.SYNTHETIC[tk][0]], bt.SYNTHETIC[tk][1], rate)
        elif tk in bt.CASH_LIKE:
            series[tk] = bt.synthetic_cash(rate)
        elif tk in got:
            series[tk] = got[tk]
    series = {tk: [r for r in rows if r[0] < LONG_END] for tk, rows in series.items()}
    r, q, b = _three(series, spec)
    c = _row(r)
    if c is None:
        return {"ok": False, "detail": f"{r.get('reason')}: {r.get('detail')}"}
    return {"ok": True, "candidate": c, "qqq": q, "tbills": b, "passes": _earns(c, q, b)}


def evaluate_blocking(key: str, book: dict | None, *, ratio_gain: float, drop_tol: float,
                      fetch=None, long=True) -> dict:
    """Every test but G6 for one candidate. Blocking: fetches ten years of prices
    (and the long history). `book` is `target_solver._book_for_solve`'s."""
    from app.services import book_effect
    from app.services import sleeve_diversity as div
    from app.services import strategy_catalog
    from app.services.backtest_service import _fetch

    entry, spec = dc.get(key), dc.spec(key)
    if entry is None:
        return {"ok": False, "reason": "UNKNOWN_CANDIDATE", "detail": f"{key!r} is not a wave-3 candidate"}
    fetch = fetch or _fetch
    need = sorted(set(bt.tickers_needed(spec)) | {"QQQ", "BIL"})
    series, missing = fetch(need)
    if missing:
        return {"ok": False, "code": entry["code"], "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    r, q, b = _three(series, spec)
    c = _row(r)
    if c is None:
        return {"ok": False, "code": entry["code"], "reason": r.get("reason"), "detail": r.get("detail")}

    # G1 badge: correlation with QQQ, from the same daily-return rebuild as D97.
    rc, rq = div.daily_returns(series, spec), div.daily_returns(series, QQQ_HOLD)
    corr_qqq = div.correlation(rc, rq) if rc and rq else None

    # B1 (b) and G2: the owner's book with and without it.
    be = (book_effect.compare_blocking(book, {**spec, "id": entry["id"]}, float(entry["sleeve_pct"]), fetch,
                                       ratio_gain=ratio_gain, drop_tol=drop_tol)
          if book else {"ok": False, "reason": "NO_BOOK"})
    book_cagr_up = (be["with"]["cagr_pct"] > be["without"]["cagr_pct"]) if be.get("ok") else None

    sweep = None
    o = spec.get("overlay") or {}
    if o.get("sweep_param") and o.get("sweep_values"):
        sweep = bt.sweep(series, spec, o["sweep_param"], list(o["sweep_values"]))

    lg = _long(spec) if (long and spec.get("long_history")) else None
    short = bool(entry.get("short_history")) or (c.get("years") or 0) < SHORT_HISTORY_YEARS and not lg

    beats_alone = (q is not None and c["cagr_pct"] > q["cagr_pct"])
    tests = {
        "B1": {"pass": bool(beats_alone or book_cagr_up),
               "alone": beats_alone, "for_the_book": book_cagr_up},
        "G2": {"pass": bool(be.get("improves")) if be.get("ok") else False,
               "failed": be.get("failed"), "gain": be.get("return_per_risk_gain"),
               "deeper_pts": be.get("worst_drop_change_pts")},
        "G3": {"pass": bool(_earns(c, q, b))},
        "G4": ({"pass": bool(lg.get("passes")), "badge": None, "long": lg} if (lg and lg.get("ok")) else
               {"pass": None, "badge": "short history" if short else None,
                "detail": (lg or {}).get("detail") or "no index long enough to rebuild from"}),
        "G5": ({"pass": bool(sweep.get("robust")), "spread_pct": sweep.get("cagr_spread_pct")} if sweep
               else {"pass": None, "detail": "a plain fund: no parameter to sweep"}),
        "G1": {"badge": "different engine" if (corr_qqq is not None and corr_qqq <= DIFFERENT_MAX_CORR) else None,
               "correlation_qqq": round(corr_qqq, 3) if corr_qqq is not None else None},
    }
    g4_ok = tests["G4"]["pass"] is True or (tests["G4"]["pass"] is None and tests["G4"]["badge"])
    passes = bool(tests["B1"]["pass"] and tests["G2"]["pass"] and tests["G3"]["pass"] and g4_ok
                  and tests["G5"]["pass"] is not False)
    return {"ok": True, "code": entry["code"], "id": entry["id"], "name": entry["name"],
            "family": entry["family"], "sleeve_pct": entry["sleeve_pct"],
            "expected_risk": entry["expected_risk"],
            "measured_risk": strategy_catalog.measured_risk(c["max_drawdown_pct"]),
            "window": c, "qqq": q, "tbills": b, "book_effect": be if be.get("ok") else None,
            "tests": tests, "passes_before_g6": passes}


def near_duplicates_blocking(keys: list[str], fetch=None) -> dict:
    """G6: every candidate pair's daily-return correlation; pairs above 0.90 are near-duplicates."""
    from app.services import sleeve_diversity as div
    from app.services.backtest_service import _fetch

    fetch = fetch or _fetch
    specs = {k: dc.spec(k) for k in keys if dc.get(k)}
    need: set[str] = set()
    for s in specs.values():
        need |= set(bt.tickers_needed(s))
    series, missing = fetch(sorted(need))
    rets = {k: div.daily_returns(series, s) for k, s in specs.items()}
    out = []
    ks = sorted(specs, key=lambda k: int(dc.get(k)["code"][1:]))
    for n, a in enumerate(ks):
        for b in ks[n + 1:]:
            rho = div.correlation(rets[a], rets[b]) if rets.get(a) and rets.get(b) else None
            if rho is not None and rho > NEAR_DUPLICATE_CORR:
                out.append({"a": dc.get(a)["code"], "b": dc.get(b)["code"], "correlation": round(rho, 3)})
    return {"near_duplicates": out, "missing_tickers": sorted(missing or [])}
