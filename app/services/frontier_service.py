"""PR 90 - the frontier acts: resize the sleeves toward the plan's Kelly fraction.

Eran, 2026-10-05, after PR 89's smoke showed a 40% sleeve at 88% volatility
with negative expected compound growth: *"I want the recommendation cards to be
actionable and not only with insights ... once I approve them will execute it."*

**WHAT THE LIVE BOOK SAID** (frontier_inputs on production, 2026-10-05). The
book as targeted sits at **1.16x full Kelly**: past the point where holding
more of the same mix adds growth. Re-optimising only the core beside fixed
sleeves gets it to 1.08x and an all-bond corner -- the sleeves carry the risk,
so a core-only card would be a card that cannot do what it is for. Letting the
sleeves shrink reaches the Grow plan's 1/2 Kelly at half the volatility.

So the actionable card is a RESIZE: shrink each sleeve the optimiser says is
oversized, sell the excess with CGT, buy the core's own basket with the
proceeds, and re-level the sleeve caps -- one Accept.

**THE PROBLEM IT IS SOLVING, stated once:**

    maximise   mu'x - (lambda/2) x'Sx         lambda = 1 / (objective's Kelly fraction)
    over       x = sleeves + core + cash
    where      each sleeve may only SHRINK (0 <= s_i <= today's size)
               the core is held to its basket's own shape (one composite asset)
               cash stays where it is

Shrink-only on purpose: growing a leveraged sleeve because an estimate says so
is the trade this pipeline least trusts itself to make.

**WHY IT DOES NOT ACT ON ONE ESTIMATE.** Mean-variance amplifies whatever error
is in its inputs, and these inputs rest on one decade and one bear market.
The solve is repeated on `DRAWS` block-bootstrapped histories (21-day blocks
keep volatility clustering intact); the proposal is the AVERAGE of those
answers (Michaud resampling) and the 10th-90th percentile is the stability
band. A sleeve is only resized when today's size sits ABOVE its band -- when
the sampling noise cannot explain the gap. Inside the band the card does not
exist: a recommendation that flips with the bootstrap seed is not one.

**WHAT THE CARD MAY CLAIM.** Every before/after figure is computed from the
SIMULATED post-trade weights in the same model (CLAUDE.md: never from trade
size), and the accept test re-measures the book the handler actually produced.

Computed nightly and on demand, stored as a `SolverVerdict` row (version tag
`FRONTIER_TAG`), and read by the card -- never computed on the Today path.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np

from app.engines import frontier as fr
from app.services import frontier_inputs as fi
from app.services.solver_store import FRONTIER_VERSION

logger = logging.getLogger(__name__)

FRONTIER_TAG = FRONTIER_VERSION     # SolverVerdict.solver_version, owned by solver_store
DRAWS = 100
BLOCK_DAYS = 21
SEED = 7
BAND_PCT = (10.0, 90.0)
# A resize smaller than this (percentage points of NAV) is not a card: it is
# inside lot granularity and the estimate's own noise.
MIN_MOVE_PCT = 2.0
# Proposals land on a half-point grid. Finer would print precision the
# resampling does not have.
GRID_PCT = 0.5
# Past this a verdict is not acted on: the nightly job has missed a run.
MAX_AGE_H = 36.0

RESIZE = "RESIZE"
WITHIN_BAND = "WITHIN_BAND"
NOT_MEASURABLE = "NOT_MEASURABLE"

CARD_KIND = "frontier_resize"


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

def proxy_of(ticker: str) -> str:
    """Which frontier asset a core basket ticker is modelled by."""
    from app.services.allocation_mix import classify
    from app.services.strategy_profile import _INTL
    tk = (ticker or "").upper()
    if tk == "CASH":
        return fi.CASH_ID
    cls = classify(tk, "")
    if cls == "Fixed Income":
        return "fixed_income"
    if cls == "Commodities":
        return "commodities"
    if cls == "Cash":
        return fi.CASH_ID
    return "intl_equity" if tk in _INTL else "us_equity"


def core_vector(basket: dict[str, float], ids: list[str]) -> np.ndarray | None:
    """The core basket as weights over the frontier's asset ids (sums to 1).

    None when a basket ticker maps to an asset that could not be priced: a
    composite missing a component would be a different core than the one
    being bought.
    """
    total = sum(w for w in basket.values() if w > 0)
    if total <= 0:
        return None
    v = np.zeros(len(ids))
    for tk, w in basket.items():
        if w <= 0:
            continue
        pid = proxy_of(tk)
        if pid not in ids:
            return None
        v[ids.index(pid)] += w / total
    return v


def block_bootstrap(t: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Row indices for one stationary-block redraw of a length-t history."""
    starts = rng.integers(0, max(1, t - block + 1), size=math.ceil(t / block))
    idx = np.concatenate([np.arange(s, min(s + block, t)) for s in starts])
    return idx[:t]


def _reduce(mu: np.ndarray, cov: np.ndarray, sleeve_idx: list[int], core_vec: np.ndarray,
            cash_idx: int) -> tuple[np.ndarray, np.ndarray]:
    """Full asset space -> [sleeves..., core, cash]."""
    n = mu.size
    rows = []
    for i in sleeve_idx:
        r = np.zeros(n)
        r[i] = 1.0
        rows.append(r)
    rows.append(core_vec)
    c = np.zeros(n)
    c[cash_idx] = 1.0
    rows.append(c)
    a = np.array(rows)
    return a @ mu, a @ cov @ a.T


def _book_metrics(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, rf: float) -> dict:
    st = fr.portfolio_stats(w, mu, cov)
    kp = fr.kelly_position(w, mu, cov, rf)
    f = kp["fraction_of_kelly"]
    return {"fraction_of_kelly": round(f, 3) if f is not None else None,
            "over_kelly": kp["over_kelly"],
            "volatility_pct": round(st["volatility"] * 100, 2),
            "return_pct": round(st["return"] * 100, 2),
            "growth_cagr_pct": round(kp["growth_now"]["cagr"] * 100, 2)}


def _grid(x_pct: float) -> float:
    return round(round(x_pct / GRID_PCT) * GRID_PCT, 2)


def resize_solve(prep: dict, *, sleeves_pct: dict[str, float], core_basket: dict[str, float],
                 cash_pct: float, objective: str, est_kw: dict,
                 draws: int = DRAWS, seed: int = SEED) -> dict:
    """The resampled resize. Pure: a `frontier_inputs.prepare` result in, a verdict out.

    ``sleeves_pct`` is each sleeve's PLAN size in percent of NAV; ``cash_pct``
    today's cash share in percent; ``est_kw`` the keyword arguments for
    `frontier_inputs.estimate`.
    """
    assets = prep["assets"]
    ids = [a["id"] for a in assets] + [fi.CASH_ID]
    sleeve_ids = [a["id"] for a in assets if a["kind"] == "sleeve" and a["id"] in sleeves_pct]
    if not sleeve_ids:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "NO_SLEEVES",
                "detail": "no sleeve on this book could be measured, so there is nothing to resize"}
    core_vec = core_vector(core_basket, ids)
    if core_vec is None:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "CORE_NOT_MODELLED",
                "detail": "the core basket names an asset that could not be priced"}
    sleeve_idx = [ids.index(s) for s in sleeve_ids]
    cash_idx = ids.index(fi.CASH_ID)
    k = len(sleeve_ids)
    now_s = np.array([sleeves_pct[s] / 100.0 for s in sleeve_ids])
    cash = max(0.0, min(1.0, cash_pct / 100.0))
    free_total = 1.0 - cash
    if now_s.sum() > free_total + 1e-9:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "SLEEVES_EXCEED_BOOK",
                "detail": "sleeves plus cash exceed the book"}
    lam = fr.risk_aversion_for(objective)
    free = np.array([True] * (k + 1) + [False])
    fixed = np.zeros(k + 2)
    fixed[-1] = cash
    upper = np.append(now_s, [1.0, 0.0])

    def solve(mu_r, cov_r):
        return fr.optimize(mu_r, cov_r, risk_aversion=lam, free_total=free_total,
                           fixed=fixed, free=free, upper=upper)

    ils = prep["ils"]
    est0 = fi.estimate(ils, assets, **est_kw)
    mu0, cov0 = _reduce(est0["mu"], est0["cov"], sleeve_idx, core_vec, cash_idx)
    point = solve(mu0, cov0)

    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(draws):
        idx = block_bootstrap(ils.shape[0], BLOCK_DAYS, rng)
        est = fi.estimate(ils[idx], assets, **est_kw)
        mu_r, cov_r = _reduce(est["mu"], est["cov"], sleeve_idx, core_vec, cash_idx)
        samples.append(solve(mu_r, cov_r)[:k])
    samples = np.array(samples)
    mean = samples.mean(axis=0)
    lo, hi = np.percentile(samples, BAND_PCT, axis=0)

    rows, proposed = [], now_s.copy()
    for j, sid in enumerate(sleeve_ids):
        cur = now_s[j] * 100
        prop = _grid(mean[j] * 100)
        act = (cur - hi[j] * 100) >= MIN_MOVE_PCT and (cur - prop) >= MIN_MOVE_PCT
        if act:
            proposed[j] = prop / 100.0
        rows.append({"id": sid, "current_pct": round(cur, 2), "proposed_pct": prop,
                     "point_pct": round(point[j] * 100, 2),
                     "band_pct": [round(lo[j] * 100, 2), round(hi[j] * 100, 2)],
                     "act": bool(act)})

    def book(s):
        return np.concatenate([s, [free_total - s.sum(), cash]])

    rf = est0["risk_free"]
    before = _book_metrics(book(now_s), mu0, cov0, rf)
    after = _book_metrics(book(proposed), mu0, cov0, rf)
    outcome = RESIZE if any(r["act"] for r in rows) else WITHIN_BAND
    return {"ok": True, "outcome": outcome, "objective": objective,
            "kelly_target": round(1.0 / lam, 4), "risk_aversion": lam,
            "sleeves": rows, "cash_pct": round(cash * 100, 2),
            "core_pct_before": round((free_total - now_s.sum()) * 100, 2),
            "core_pct_after": round((free_total - proposed.sum()) * 100, 2),
            "core_basket": core_basket,
            "before": before, "after": after,
            "draws": draws, "block_days": BLOCK_DAYS, "band": list(BAND_PCT),
            "views": est0["views"], "shrinkage": round(est0["shrinkage"], 4),
            "window": {"start": prep["dates"][0], "end": prep["dates"][-1],
                       "sessions": len(prep["dates"])},
            "fx_basis": "historical" if prep["fx"] else "spot",
            "excluded": prep["excluded"], "degraded": prep["degraded"] + est0["degraded"]}


def plan_trades(verdict: dict, *, specs: dict[str, dict], holdings: list[dict]) -> dict:
    """The trades a RESIZE verdict implies for today's holdings. Pure.

    ``specs`` maps sleeve id -> its catalog spec; ``holdings`` is
    ``[{"ticker", "shares", "value_ils", "tax_ils"}]`` per held row (value and
    tax for selling the WHOLE row). Each resized sleeve sells the same fraction
    of every ticker it can hold -- the share of the sleeve that is going away.

    A ticker two sleeves can both hold is not split by guesswork: that sleeve
    is skipped and the reason travels on the plan.
    """
    from app.engines import blend
    from app.services.funding_service import MIN_TRADE_ILS

    claim = {sid: {t.upper() for t in blend.claimable_tickers(spec)} for sid, spec in specs.items()}
    resizes, sells, skipped = [], [], []
    for s in verdict.get("sleeves") or []:
        if not s.get("act"):
            continue
        sid = s["id"]
        mine = claim.get(sid) or set()
        shared = sorted(t for t in mine for o, ts in claim.items() if o != sid and t in ts)
        if shared:
            skipped.append({"id": sid, "reason": "SHARED_TICKER", "tickers": shared})
            continue
        frac = 1.0 - s["proposed_pct"] / s["current_pct"]
        resizes.append({"strategy_id": sid, "from_pct": s["current_pct"],
                        "to_pct": s["proposed_pct"], "fraction_sold": round(frac, 6),
                        "tickers": sorted(mine)})
        for h in holdings:
            if h["ticker"].upper() in mine and h["shares"] > 0:
                sells.append({"ticker": h["ticker"].upper(), "strategy_id": sid,
                              "shares": round(h["shares"] * frac, 6),
                              "value_ils": round(h["value_ils"] * frac, 2),
                              "tax_ils": round(h["tax_ils"] * frac, 2)})
    gross = sum(x["value_ils"] for x in sells)
    tax = sum(x["tax_ils"] for x in sells)
    net = gross - tax
    basket = {t.upper(): w for t, w in (verdict.get("core_basket") or {}).items()
              if w > 0 and t.upper() != "CASH"}
    total_w = sum(basket.values())
    buys = []
    for tk, w in sorted(basket.items()):
        amt = round(net * w / total_w, 2) if total_w else 0.0
        if amt >= MIN_TRADE_ILS:
            buys.append({"ticker": tk, "weight": round(w / total_w, 6), "amount_ils": amt})
    return {"resizes": resizes, "sells": sells, "buys": buys, "skipped": skipped,
            "gross_ils": round(gross, 2), "tax_ils": round(tax, 2), "net_ils": round(net, 2),
            "kept_in_cash_ils": round(net - sum(b["amount_ils"] for b in buys), 2)}


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------

async def record(session, user, verdict: dict, *, source: str = "on_demand"):
    """Persist one frontier verdict. Best-effort, like `solver_store.record`."""
    from app.models.tables import SolverVerdict
    if not isinstance(verdict, dict) or not verdict.get("outcome"):
        return None
    try:
        row = SolverVerdict(subject=user.email, solver_version=FRONTIER_TAG,
                            outcome=str(verdict["outcome"])[:32], target_excess_pct=None,
                            max_drawdown_pct=None, solved_total_sleeve_pct=None,
                            floor_breaches_ceiling=False, binding_constraint="frontier",
                            source=source[:16], detail=verdict)
        session.add(row)
        await session.commit()
        return row
    except Exception:  # noqa: BLE001 -- a solve that answered must not fail on its receipt
        logger.warning("could not record frontier verdict", exc_info=False)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


async def latest(session, user):
    from sqlalchemy import desc, select

    from app.models.tables import SolverVerdict
    return (await session.scalars(
        select(SolverVerdict).where(SolverVerdict.subject == user.email)
        .where(SolverVerdict.solver_version == FRONTIER_TAG)
        .order_by(desc(SolverVerdict.created_at)).limit(1))).first()


def _age_hours(row) -> float | None:
    ts = getattr(row, "created_at", None)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


# --------------------------------------------------------------------------
# the loader
# --------------------------------------------------------------------------

async def _core_basket(session, user, objective: str) -> dict[str, float]:
    """The basket the core BUYS: the chosen core strategy's, else the objective's."""
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat
    from app.services.allocation_mix import OBJ_TARGET
    sid = await sv.core_strategy_id(session, user)
    basket = static_cat.core_weights(sid) if sid else None
    if basket:
        return basket
    default = {"Equities": "VTI", "Fixed Income": "BND", "Commodities": "IAU", "Cash": "CASH"}
    return {default[c]: w for c, w in (OBJ_TARGET.get(objective) or {}).items() if c in default}


async def compute_for(session, user, *, source: str = "on_demand",
                      draws: int = DRAWS) -> dict:
    """Solve for the acting user's book and record the verdict. Read-only on the book."""
    from app.core.offload import offload
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions

    ctx = await fi.load_context(session, user)
    if not ctx.get("ok"):
        return ctx
    rows = await list_positions(session, user)
    mix, _nav = current_mix(rows)
    cash_pct = float(mix.get("Cash") or 0.0) * 100.0
    basket = await _core_basket(session, user, ctx["objective"])

    def work():
        prep = fi.prepare(ctx["series"], sleeves=ctx["sleeves"])
        if not prep["ok"]:
            return {"ok": True, "outcome": NOT_MEASURABLE, "reason": prep.get("reason"),
                    "detail": prep.get("detail")}
        return resize_solve(prep, sleeves_pct={s["id"]: float(s["current_pct"]) for s in ctx["sleeves"]},
                            core_basket=basket, cash_pct=cash_pct, objective=ctx["objective"],
                            est_kw=ctx["est_kw"], draws=draws)

    out = await offload(work)
    out["computed_at"] = datetime.now(timezone.utc).isoformat()
    out["version"] = FRONTIER_TAG
    await record(session, user, out, source=source)
    return out


async def refresh_all(session) -> dict:
    """Nightly: one verdict per book that runs sleeves. One bad book never stops the rest."""
    from sqlalchemy import distinct, select

    from app.models.tables import PlanSleeve, User
    subjects = list((await session.scalars(select(distinct(PlanSleeve.subject)))).all())
    done = failed = 0
    for subject in subjects:
        user = (await session.scalars(select(User).where(User.email == subject))).first()
        if user is None:
            continue
        try:
            await compute_for(session, user, source="scheduled")
            done += 1
        except Exception:  # noqa: BLE001
            logger.warning("scheduled frontier solve failed for a subject", exc_info=False)
            failed += 1
    return {"frontier_solved": done, "frontier_failed": failed}


# --------------------------------------------------------------------------
# the card
# --------------------------------------------------------------------------

def _holdings_for_sale(rows) -> list[dict]:
    from app.services.recommendations import _sale_value_ils
    out = []
    for p in rows:
        q = float(p.quantity or 0)
        if q <= 0:
            continue
        gross, tax = _sale_value_ils(q, float(p.current_price or 0), float(p.cost_basis or 0),
                                     p.market, p.meta)
        out.append({"ticker": (p.ticker or "").upper(), "shares": q,
                    "value_ils": gross, "tax_ils": tax})
    return out


async def card(session, user, rows) -> list[dict]:
    """The Today card for the latest RESIZE verdict, or nothing.

    Nothing when: no verdict, a verdict past `MAX_AGE_H`, a verdict about a
    different book (a sleeve was resized since, or the objective changed), or
    an outcome other than RESIZE.
    """
    from app.services import sleeve_service as sv
    from app.services import strategy_catalog
    from app.services.plan_service import get_plan

    row = await latest(session, user)
    if row is None or row.outcome != RESIZE:
        return []
    age = _age_hours(row)
    if age is None or age > MAX_AGE_H:
        return []
    v = row.detail or {}
    plan = await get_plan(session, user)
    if (getattr(plan, "objective", None) or "Balanced") != v.get("objective"):
        return []
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    for s in v.get("sleeves") or []:
        if abs(live.get(s["id"], -1.0) - float(s["current_pct"])) > 0.01:
            return []
    specs = {sid: strategy_catalog.get(sid) or {} for sid in live}
    trades = plan_trades(v, specs=specs, holdings=_holdings_for_sale(rows))
    if not trades["resizes"] or not trades["sells"]:
        return []

    b, a = v["before"], v["after"]
    moves = ", ".join(f"{r['strategy_id']} {r['from_pct']:g}% -> {r['to_pct']:g}%"
                      for r in trades["resizes"])
    when = "just now" if age < 1 else f"{age:.0f}h ago"
    kelly_b = b.get("fraction_of_kelly")
    kelly_a = a.get("fraction_of_kelly")
    title = (f"Your book is {kelly_b:.2f}x full Kelly: shrink {trades['resizes'][0]['strategy_id']}"
             if kelly_b is not None else
             f"Shrink {trades['resizes'][0]['strategy_id']}: it adds risk the plan cannot use")
    sells_txt = ", ".join(f"{x['shares']:g} {x['ticker']} (~{x['value_ils']:,.0f})" for x in trades["sells"])
    buys_txt = ", ".join(f"{x['ticker']} ~{x['amount_ils']:,.0f}" for x in trades["buys"]) or "nothing (kept as cash)"
    band_txt = "; ".join(f"{s['id']}: today {s['current_pct']:g}%, band {s['band_pct'][0]:g}-{s['band_pct'][1]:g}%"
                         for s in v["sleeves"] if s.get("act"))
    return [{
        "id": f"rec_frontier_{row.id}",
        "dimension": "frontier", "severity": "HIGH",
        "title": title,
        "why": (f"Your {v['objective']} plan targets {v['kelly_target']:.2f}x Kelly. As planned today the "
                f"book models at {kelly_b}x, {b['volatility_pct']:.1f}% volatility and "
                f"{b['growth_cagr_pct']:.1f}%/yr expected compound growth. Resampled {v['draws']} times "
                f"over {v['window']['sessions']} sessions in ILS; the resize is only proposed where "
                f"today's size sits above the whole stability band ({band_txt}). Expected figures are "
                f"estimates from one decade with one bear market, not forecasts."),
        "action": (f"Accept resizes {moves}, sells {sells_txt}, pays ~{trades['tax_ils']:,.0f} in tax, "
                   f"and buys your core basket with the ~{trades['net_ils']:,.0f} left: {buys_txt}. "
                   f"Sleeve caps are re-levelled to the new sizes."),
        "impact": (f"Modelled on the post-trade book: {kelly_b}x -> {kelly_a}x Kelly, volatility "
                   f"{b['volatility_pct']:.1f}% -> {a['volatility_pct']:.1f}%, expected compound growth "
                   f"{b['growth_cagr_pct']:.1f}% -> {a['growth_cagr_pct']:.1f}%/yr. Core "
                   f"{v['core_pct_before']:g}% -> {v['core_pct_after']:g}% of the book."),
        "how": [f"Resize: {moves}",
                f"Sell: {sells_txt}",
                f"Tax (CGT) ~{trades['tax_ils']:,.0f}",
                f"Buy: {buys_txt}",
                f"Solved {when} ({v.get('version')})"],
        "apply": {"kind": CARD_KIND, "verdict_id": row.id,
                  "resizes": trades["resizes"],
                  "buy_weights": {x["ticker"]: x["weight"] for x in trades["buys"]},
                  "estimate": {"sells": trades["sells"], "buys": trades["buys"],
                               "tax_ils": trades["tax_ils"], "net_ils": trades["net_ils"]}},
        "meta": {"before": b, "after": a, "sleeves": v["sleeves"], "skipped": trades["skipped"],
                 "age_hours": round(age, 1), "kelly_target": v["kelly_target"]},
    }]


# --------------------------------------------------------------------------
# accept
# --------------------------------------------------------------------------

async def apply_resize(session, user, spec: dict) -> dict:
    """Execute a `frontier_resize` card. Refuses UNTOUCHED when the book moved.

    Order matters and is the funded-buy order: price every buy first, then
    resize, then sell, then buy out of what the sale actually raised. Shares are
    recomputed from the holdings at the moment of Accept, so a price move since
    the card was built changes the amounts, never the proportions.
    """
    from app.providers.registry import guarded_quote
    from app.services import sleeve_service as sv
    from app.services.funding_service import MIN_TRADE_ILS
    from app.services.intake_service import credit_cash, list_positions
    from app.services.recommendations import _apply_redeploy_cash, _offload, _sell_shares
    from app.services.strategy_service import _arm_sleeve_caps

    resizes = spec.get("resizes") or []
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    for r in resizes:
        if abs(live.get(r["strategy_id"], -1.0) - float(r["from_pct"])) > 0.01:
            return {"ok": False, "refused": "BOOK_CHANGED",
                    "error": f"{r['strategy_id']} is no longer {r['from_pct']:g}% - nothing was changed. "
                             f"Refresh the frontier and look again."}
    weights = spec.get("buy_weights") or {}
    for tk in weights:
        try:
            q = await _offload(guarded_quote, tk)
        except Exception:  # noqa: BLE001
            q = None
        if q is None or not getattr(q, "price", None):
            return {"ok": False, "refused": "NO_PRICE",
                    "error": f"no live price for {tk} - nothing was sold."}

    resized = []
    for r in resizes:
        res = await sv.add_or_resize(session, user, r["strategy_id"], float(r["to_pct"]))
        if not res.get("ok"):
            await session.rollback()
            return {"ok": False, "refused": "RESIZE_REFUSED", "error": res.get("reason") or res.get("error")}
        resized.append({"strategy_id": r["strategy_id"], "from_pct": r["from_pct"], "to_pct": r["to_pct"]})
    await session.commit()

    rows = await list_positions(session, user)
    sold, tax_total, net_total = [], 0.0, 0.0
    for r in resizes:
        frac = float(r["fraction_sold"])
        for tk in r["tickers"]:
            held = [p for p in rows if (p.ticker or "").upper() == tk]
            qty = sum(float(p.quantity or 0) for p in held)
            if qty <= 0 or frac <= 0:
                continue
            res = await _sell_shares(session, user, held, qty * frac)
            net = res["gross"] - res["tax"]
            await credit_cash(session, user, net)
            tax_total += res["tax"]
            net_total += net
            sold.append({"ticker": tk, "shares": round(res["shares"], 6), "price": res.get("price"),
                         "value_ils": round(res["gross"], 2), "tax_ils": round(res["tax"], 2)})
        rows = await list_positions(session, user)

    total_w = sum(weights.values())
    legs = []
    for tk, w in weights.items():
        amt = round(net_total * w / total_w, 2) if total_w else 0.0
        if amt >= MIN_TRADE_ILS:
            legs.append({"ticker": tk, "amount_ils": amt})
    bought = await _apply_redeploy_cash(session, user, {"legs": legs}) if legs else {"bought": [], "skipped": []}
    caps = await _arm_sleeve_caps(session, user)
    await session.commit()
    got = bought.get("bought", [])
    note = ("Resized " + ", ".join(f"{r['strategy_id']} {r['from_pct']:g}% -> {r['to_pct']:g}%" for r in resized)
            + ". Bought " + (", ".join(f"{b['ticker']} {b['amount_ils']:,.0f}" for b in got) or "nothing")
            + f" with the {net_total:,.0f} raised. Sleeve caps re-levelled.")
    return {"note": note, "resized": resized, "sold": sold, "bought": got,
            "skipped_buys": bought.get("skipped", []), "tax_ils": round(tax_total, 2),
            "net_raised_ils": round(net_total, 2), "caps": caps,
            "broker_note": "Tracked book updated - no brokerage order was placed."}
