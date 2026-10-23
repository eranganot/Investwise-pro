"""PR 132 (D97): every sleeve must be a different bet. Owner, 2026-10-15: "I don't
want that all the sleeves will be similar and will be depending on the same 2-3
stocks. each sleeve MUST be different".

Two sleeves are THE SAME BET when either test trips:

1. **Shared market.** Every ticker maps to the market it is a bet on (TQQQ, QLD
   and QQQ are all the Nasdaq-100). A strategy's exposure profile is, per
   market, the largest share that market takes in any state the rule can be in
   -- its risk-on basket, its fall-back base, its risk-off leg, its selection
   universe -- because a sleeve that is the Nasdaq when on and T-bills when off
   is still a Nasdaq bet. The overlap of two profiles (sum of the per-market
   minimums) at or over `sleeve_overlap_alert_pct` (default 40%) is the same
   bet. Cash-like markets never count: two rules that both step back to T-bills
   are not the same bet.
2. **Moving together.** The correlation of the two strategies' daily returns,
   rebuilt from the engine's own targets over the measured window, at or over
   `sleeve_correlation_limit` (default 0.80). Different tickers that move as one
   -- semis beside the Nasdaq -- are caught here, not by the map.

3. **Holding the same thing (D123, owner 2026-10-23).** The correlation of what each
   sleeve holds when invested (its risk-on basket, rebalanced daily), at or over the
   same limit. A trend rule's time in cash dilutes test 2; it does not change what
   the sleeve is when it matters.

Correlations are measured by `refresh_correlations` (nightly, or on demand) and
stored in `kv_settings`; a request only ever READS them, so adding a sleeve never
waits on ten years of prices. A pair not yet measured is judged on test 1 alone,
and the answer says so.

Not financial advice.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger("investwise")

#: Owner decision, plan v3: shared market >= 40% of the smaller sleeve.
OVERLAP_ALERT_PCT = 40.0
#: Owner decision 2026-10-15 ("what is your recommendation?") -- 0.80, confirmed
#: against the measured table (STATUS, PR 132).
CORRELATION_LIMIT = 0.80
KV_KEY = "strategy_correlation:v1"

CASH = "T-bills / cash"
#: Ticker -> the market it is a bet on. Anything unlisted is its own market, so
#: an unknown ticker only ever overlaps with itself.
MARKET: dict[str, str] = {
    **{t: "Nasdaq-100" for t in ("QQQ", "QQQM", "QLD", "TQQQ")},
    "PSQ": "Nasdaq-100 (inverse)", "SQQQ": "Nasdaq-100 (inverse)", "QID": "Nasdaq-100 (inverse)",
    **{t: "US semiconductors" for t in ("SMH", "SOXX", "SOXL", "USD")},
    **{t: "S&P 500" for t in ("SPY", "VOO", "IVV", "SPLG", "SSO", "UPRO")},
    "SH": "S&P 500 (inverse)", "SDS": "S&P 500 (inverse)",
    **{t: "US total market" for t in ("VTI", "ITOT", "SCHB")},
    **{t: "US small-cap value" for t in ("AVUV", "VBR", "IJS")},
    "MTUM": "US momentum factor", "QUAL": "US quality factor", "USMV": "US low-volatility factor",
    **{t: "US dividend stocks" for t in ("SCHD", "VIG", "DVY")},
    **{t: CASH for t in ("BIL", "SHV", "SGOV", "CASH")},
    **{t: "US Treasuries" for t in ("IEF", "TLT", "TMF", "SHY", "GOVT", "EDV", "ZROZ")},
    **{t: "US bonds" for t in ("BND", "AGG")},
    **{t: "Gold" for t in ("GLD", "IAU", "GLDM", "SGOL")},
    **{t: "Broad commodities" for t in ("DBC", "PDBC", "GSG", "COMT")},
    "USO": "Oil", "DBA": "Agriculture", "SLV": "Silver",
    **{t: "Managed futures" for t in ("KMLM", "DBMF", "CTA", "WTMF")},
    **{t: "Non-US developed stocks" for t in ("EFA", "VEA", "IEFA")},
    **{t: "Non-US stocks" for t in ("VXUS", "ACWX", "IXUS")},
    **{t: "Emerging markets" for t in ("EEM", "VWO", "IEMG")},
    "SCZ": "Non-US small caps",
    **{t: "US real estate" for t in ("VNQ", "XLRE")},
    "XLK": "US tech sector", "TECL": "US tech sector", "XLV": "US healthcare sector",
    "XLF": "US financials sector", "FAS": "US financials sector", "XLE": "US energy sector",
    "ERX": "US energy sector", "XLI": "US industrials sector", "XLY": "US consumer discretionary sector",
    "XLP": "US consumer staples sector", "XLU": "US utilities sector", "XLB": "US materials sector",
    "XBI": "US biotech", "LABU": "US biotech", "CURE": "US healthcare sector", "DRN": "US real estate",
}
#: Single US stocks a selection rule may pick (US44 and kin) are one market.
US_STOCKS = "US large-cap stocks"


def market_of(ticker: str) -> str:
    t = (ticker or "").upper()
    if t in MARKET:
        return MARKET[t]
    if t.isalpha() and len(t) <= 5:
        # An individual US listing a selection rule ranks (AAPL, NVDA, ...).
        # Funds are all named above; anything else that looks like a stock is one.
        return US_STOCKS
    return t


def _dist(weights: dict[str, float] | None) -> dict[str, float]:
    """A basket as shares per market, summing to 1 (cash included)."""
    w = {k: float(v or 0.0) for k, v in (weights or {}).items() if float(v or 0.0) > 0}
    tot = sum(w.values())
    if tot <= 0:
        return {}
    out: dict[str, float] = {}
    for tk, v in w.items():
        m = market_of(tk)
        out[m] = out.get(m, 0.0) + v / tot
    return out


def profile(spec: dict) -> dict[str, float]:
    """Per market, the largest share it takes in any state the rule can be in.

    States: the risk-on basket (plus, for a selection rule, an equal share of its
    universe), the base it falls back to, the risk-off leg, and the legs a
    volatility rule mixes. Cash-like markets are dropped: they are not a bet.
    """
    o = spec.get("overlay") or {}
    states: list[dict[str, float]] = []
    on = dict(spec.get("weights") or {})
    uni = [str(t) for t in (o.get("universe") or [])]
    if uni:
        for t in uni:
            on[t] = on.get(t, 0.0) + 1.0 / len(uni)
    states.append(_dist(on))
    states.append(_dist(spec.get("base")))
    ro = spec.get("risk_off") or o.get("risk_off")
    if ro:
        states.append(_dist({ro: 1.0}))
    for t in o.get("fallback") or []:
        states.append(_dist({t: 1.0}))
    for leg in (o.get("legs") or {}).values() if isinstance(o.get("legs"), dict) else []:
        states.append(_dist({leg: 1.0}))
    out: dict[str, float] = {}
    for st in states:
        for m, v in st.items():
            if m == CASH:
                continue
            out[m] = max(out.get(m, 0.0), v)
    return out


def overlap_pct(a: dict, b: dict) -> tuple[float, list[str]]:
    """Shared market between two strategy specs, in % of the smaller exposure,
    and the markets they share."""
    pa, pb = profile(a), profile(b)
    shared = sorted(m for m in pa if m in pb)
    if not pa or not pb:
        return 0.0, []
    common = sum(min(pa[m], pb[m]) for m in shared)
    smaller = min(sum(pa.values()), sum(pb.values()))
    return (round(100.0 * min(1.0, common / smaller), 1) if smaller > 0 else 0.0), shared


def _limits() -> tuple[float, float]:
    from app.services import thresholds
    return (float(thresholds.get("sleeve_overlap_alert_pct", OVERLAP_ALERT_PCT)),
            float(thresholds.get("sleeve_correlation_limit", CORRELATION_LIMIT)))


def _spec_of(strategy_id: str) -> dict | None:
    from app.services import strategy_catalog
    e = strategy_catalog.get(strategy_id)
    return strategy_catalog._spec(e) if e else None


def _name(strategy_id: str) -> str:
    from app.services import strategy_catalog
    return (strategy_catalog.get(strategy_id) or {}).get("name") or strategy_id


def same_bet(a_id: str, b_id: str, corr: dict | None = None) -> dict | None:
    """Why `a_id` and `b_id` are the same bet, or None when they are different.

    `corr` is the stored matrix (`load_correlations`); a pair missing from it is
    judged on shared market alone, and `correlation` comes back None.
    """
    if a_id == b_id:
        return None
    a, b = _spec_of(a_id), _spec_of(b_id)
    if a is None or b is None:
        return None
    lim_overlap, lim_corr = _limits()
    ov, markets = overlap_pct(a, b)
    rho = ((corr or {}).get("pairs") or {}).get(pair_key(a_id, b_id))
    held_rho = ((corr or {}).get("invested_pairs") or {}).get(pair_key(a_id, b_id))
    why = []
    if ov >= lim_overlap - 1e-9:
        why.append(f"both are bets on {', '.join(markets)} ({ov:g}% of the smaller one, "
                   f"your limit is {lim_overlap:g}%)")
    if rho is not None and float(rho) >= lim_corr - 1e-9:
        why.append(f"they move together (correlation {float(rho):.2f} over ten years, "
                   f"your limit is {lim_corr:.2f})")
    if held_rho is not None and float(held_rho) >= lim_corr - 1e-9 and not (
            rho is not None and float(rho) >= lim_corr - 1e-9):
        # D123: test 3 -- what they hold when invested moves as one.
        why.append(f"what they hold when invested moves together (correlation {float(held_rho):.2f}, "
                   f"your limit is {lim_corr:.2f})")
    if not why:
        return None
    return {"strategy_id": b_id, "name": _name(b_id), "overlap_pct": ov, "markets": markets,
            "correlation": (round(float(rho), 3) if rho is not None else None),
            "invested_correlation": (round(float(held_rho), 3) if held_rho is not None else None),
            "why": "; ".join(why)}


def same_bets_for(strategy_id: str, held_ids: list[str], corr: dict | None) -> list[dict]:
    """Every held sleeve `strategy_id` would be the same bet as."""
    return [x for x in (same_bet(strategy_id, h, corr) for h in held_ids if h != strategy_id) if x]


def refusal(strategy_id: str, clashes: list[dict]) -> dict:
    """The answer to adding a sleeve that is the same bet as one already held."""
    first = clashes[0]
    return {"ok": False, "error": "same bet as a sleeve you hold",
            "strategy_id": strategy_id, "same_bet_as": clashes,
            "reason": (f"{_name(strategy_id)} is the same bet as your {first['name']} sleeve: "
                       f"{first['why']}. Every sleeve must be a different bet - replace "
                       f"{first['name']} with it instead, or pick a strategy that is different."),
            "replace": first["strategy_id"]}


# --------------------------------------------------------------------------- #
# correlation: measured offline, read online
# --------------------------------------------------------------------------- #
def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def daily_returns(series: dict, spec: dict) -> tuple[list[str], np.ndarray] | None:
    """The strategy's own daily return, from the engine's targets: yesterday's
    weights times today's price moves. No costs -- this is for correlation, not
    for a headline."""
    from app.engines.strategy_backtest import align, late_tickers, targets_for, tickers_needed

    need = set(tickers_needed(spec))
    feed = {t: series[t] for t in need if t in series}
    if len(feed) < len(need):
        return None
    dates, px = align(feed, late=late_tickers(spec))
    if not dates or len(dates) < 260:
        return None
    t = targets_for(px, spec)
    if isinstance(t, dict) or len(t) != len(dates):
        return None
    r = np.zeros(len(dates))
    for i in range(1, len(dates)):
        w = t[i - 1] or {}
        acc = 0.0
        for tk, wt in w.items():
            a = px.get(tk)
            if a is None:
                continue
            p0, p1 = float(a[i - 1]), float(a[i])
            if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                acc += float(wt) * (p1 / p0 - 1.0)
        r[i] = acc
    return list(dates), r


def correlation(ra: tuple[list[str], np.ndarray], rb: tuple[list[str], np.ndarray]) -> float | None:
    """Pearson correlation of two daily return series on their shared dates."""
    ia = {d: i for i, d in enumerate(ra[0])}
    common = [(ia[d], j) for j, d in enumerate(rb[0]) if d in ia]
    if len(common) < 250:
        return None
    x = np.array([ra[1][i] for i, _ in common[1:]])
    y = np.array([rb[1][j] for _, j in common[1:]])
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def invested_returns(series: dict, spec: dict) -> tuple[list[str], np.ndarray] | None:
    """D123 (owner, 2026-10-23): what the sleeve HOLDS when it is invested -- its
    risk-on basket (`weights`), rebalanced daily -- as a daily return series.

    The strategy's own return (`daily_returns`) is diluted by its time in cash: the
    SOXL trend sleeve and the TQQQ vol-target sleeve measured 0.78 there, while
    SOXL and TQQQ themselves moved 0.95 over the month to 10-22 and fell together
    on 8 of 8 sessions SOXL dropped more than 3%. On the day a stop fires, that is
    the number that matters."""
    w = {str(k).upper(): float(v or 0.0) for k, v in (spec.get("weights") or {}).items()
         if float(v or 0.0) > 0 and market_of(str(k)) != CASH}
    tot = sum(w.values())
    if tot <= 0 or any(t not in series for t in w):
        return None
    from app.engines.strategy_backtest import align
    dates, px = align({t: series[t] for t in w})
    if not dates or len(dates) < 260:
        return None
    r = np.zeros(len(dates))
    for i in range(1, len(dates)):
        acc = 0.0
        for tk, wt in w.items():
            p0, p1 = float(px[tk][i - 1]), float(px[tk][i])
            if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                acc += (wt / tot) * (p1 / p0 - 1.0)
        r[i] = acc
    return list(dates), r


def measure_blocking(ids: list[str], fetch=None) -> dict:
    """Every pair's correlation for `ids`. Blocking: fetches price history."""
    from app.engines.strategy_backtest import tickers_needed
    from app.services.backtest_service import _fetch

    fetch = fetch or _fetch
    specs = {i: s for i in ids if (s := _spec_of(i)) is not None}
    need: set[str] = set()
    for s in specs.values():
        need |= set(tickers_needed(s))
    series, missing = fetch(sorted(need))
    rets = {i: daily_returns(series, s) for i, s in specs.items()}
    held = {i: invested_returns(series, s) for i, s in specs.items()}
    pairs: dict[str, float] = {}
    invested: dict[str, float] = {}
    unmeasured: list[str] = []
    keys = sorted(specs)
    for n, a in enumerate(keys):
        for b in keys[n + 1:]:
            rho = (correlation(rets[a], rets[b]) if rets.get(a) and rets.get(b) else None)
            if rho is None:
                unmeasured.append(pair_key(a, b))
            else:
                pairs[pair_key(a, b)] = round(rho, 4)
            hr = (correlation(held[a], held[b]) if held.get(a) and held.get(b) else None)
            if hr is not None:
                invested[pair_key(a, b)] = round(hr, 4)
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pairs": pairs, "invested_pairs": invested, "unmeasured": unmeasured,
            "missing_tickers": sorted(missing or [])}


async def load_correlations(session) -> dict:
    from app.models.tables import KVSetting
    row = await session.get(KVSetting, KV_KEY)
    if row is None:
        return {}
    try:
        return json.loads(row.value or "{}")
    except ValueError:
        return {}


async def refresh_correlations(session, fetch=None) -> dict:
    """Measure every active catalogue pair and store it. Nightly, or on demand."""
    from app.core.offload import offload
    from app.models.tables import KVSetting
    from app.services import strategy_catalog

    out = await offload(measure_blocking, strategy_catalog.active_ids(), fetch)
    if not out.get("pairs"):
        # Nothing measured (prices unavailable): keep what is stored rather than
        # replace a good matrix with an empty one.
        logger.warning("strategy correlations: nothing measured (missing %s)",
                       ", ".join(out.get("missing_tickers") or []) or "none")
        return {**out, "stored": False}
    row = await session.get(KVSetting, KV_KEY)
    if row is None:
        session.add(KVSetting(key=KV_KEY, value=json.dumps(out)))
    else:
        row.value = json.dumps(out)
    await session.commit()
    return {**out, "stored": True}


async def held_clashes(session, user) -> list[dict]:
    """Pairs of sleeves the book already holds that are the same bet (wave 4 cards)."""
    from app.services import sleeve_service as sv
    ids = [s.strategy_id for s in await sv.list_sleeves(session, user)]
    corr = await load_correlations(session)
    out = []
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            x = same_bet(a, b, corr)
            if x:
                out.append({"a": a, "a_name": _name(a), **{("b" if k == "strategy_id" else k): v
                                                            for k, v in x.items()}})
    return out


async def _refresh_job(fetch=None) -> dict:
    # Its own NullPool engine in this loop: APScheduler runs this through
    # `asyncio.run`, and borrowing the app's pooled engine across loops is what
    # asyncpg rejects (CLAUDE.md, "Cross-loop sessions").
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await refresh_correlations(session, fetch)
    finally:
        await engine.dispose()


def run_correlation_refresh_blocking(fetch=None) -> dict:
    """Sync entrypoint for APScheduler. RAISES when nothing could be measured, so
    `_guarded` records a failure instead of a healthy empty run (PR 107)."""
    import asyncio
    res = asyncio.run(_refresh_job(fetch))
    if not res.get("stored"):
        raise RuntimeError(f"strategy correlations not measured: missing {res.get('missing_tickers')}")
    logger.info("strategy correlations: %d pairs measured", len(res.get("pairs") or {}))
    return {"pairs": len(res.get("pairs") or {}), "unmeasured": len(res.get("unmeasured") or [])}
