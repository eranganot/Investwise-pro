"""Portfolio analytics powering the Section X workflows.

`compute_snapshot` and the scoring helpers are pure (list[dict] in, dict out) so
they unit-test without a database; `load_positions` adapts persisted rows.
"""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engines.scoring import clamp_score
from app.engines.whs_engine import WhsEngine
from app.models.tables import User
from app.services.intake_service import list_positions

SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
# crude geography proxy from listing venue
GEO = {"NYSE": "US", "TASE": "IL", "SPOT": "GLOBAL"}
CUR = {"NYSE": "USD", "TASE": "ILS", "SPOT": "USD"}


def _opp_id(*parts) -> str:
    return "opp_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


def compute_snapshot(positions: list[dict]) -> dict:
    rows = []
    for p in positions:
        qty = float(p.get("quantity") or 0.0)
        price = float(p.get("current_price") or 0.0)
        cost = float(p.get("cost_basis") or 0.0)
        value = qty * price
        rows.append({
            "ticker": p["ticker"], "market": p.get("market", "NYSE"),
            "value": value, "unrealized": (price - cost) * qty,
            "volatility_pct": p.get("volatility_pct"),
            "liquidity_score": p.get("liquidity_score"),
        })
    nav = sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = (r["value"] / nav) if nav else 0.0

    exposure_geo: dict[str, float] = {}
    exposure_ticker: dict[str, float] = {}
    exposure_cur: dict[str, float] = {}
    for r in rows:
        exposure_geo[GEO.get(r["market"], "OTHER")] = exposure_geo.get(GEO.get(r["market"], "OTHER"), 0.0) + r["weight"]
        exposure_cur[CUR.get(r["market"], "OTHER")] = exposure_cur.get(CUR.get(r["market"], "OTHER"), 0.0) + r["weight"]
        exposure_ticker[r["ticker"]] = exposure_ticker.get(r["ticker"], 0.0) + r["weight"]

    vol_weighted = sum(r["weight"] * (r["volatility_pct"] or 15.0) for r in rows) if nav else 0.0
    liq_weighted = sum(r["weight"] * (r["liquidity_score"] if r["liquidity_score"] is not None else 70.0)
                       for r in rows) if nav else 70.0
    return {
        "nav": nav, "n_positions": len(rows), "rows": rows,
        "exposure_geo": exposure_geo, "exposure_cur": exposure_cur, "exposure_ticker": exposure_ticker,
        "max_weight": max(exposure_ticker.values(), default=0.0),
        "avg_volatility_pct": vol_weighted,
        "liquidity_avg": liq_weighted if nav else 70.0,
        "unrealized_gains": sum(r["unrealized"] for r in rows if r["unrealized"] > 0),
        "unrealized_losses": sum(-r["unrealized"] for r in rows if r["unrealized"] < 0),
    }


def health_scores(snap: dict, cap: float | None = None) -> dict:
    st = get_settings()
    cap = st.concentration_cap if cap is None else cap
    risk_score = clamp_score(100.0 - snap["avg_volatility_pct"] * st.analytics_vol_risk_factor)
    div = clamp_score(100.0 - max(0.0, snap["max_weight"] - cap) * 250.0)
    liquidity = clamp_score(snap["liquidity_avg"])
    loss_ratio = (snap["unrealized_losses"] / snap["nav"]) if snap["nav"] else 0.0
    tax_eff = clamp_score(st.analytics_tax_efficiency_base - loss_ratio * 200.0)  # unharvested losses dent efficiency
    whs = WhsEngine().compute(risk=risk_score, tax=tax_eff, alloc=div, liq=liquidity, thematic=60.0)
    return {
        "wealth_health_score": round(whs["score"]),
        "risk_score": round(risk_score),
        "tax_efficiency_score": round(tax_eff),
        "liquidity_score": round(liquidity),
        "diversification_score": round(div),
        "_cap": cap,
    }


def health_opportunities(snap: dict, scores: dict) -> list[dict]:
    opps = []
    if snap["unrealized_losses"] > 0:
        opps.append({"id": _opp_id("tax", round(snap["unrealized_losses"])), "dimension": "tax",
                     "severity": "CRITICAL" if snap["unrealized_losses"] > 0.03 * (snap["nav"] or 1) else "HIGH",
                     "description": f"Unrealized capital losses of ~₪{round(snap['unrealized_losses']):,} detected. "
                                    "Tax-loss harvesting recommended to offset realized gains."})
    if snap["max_weight"] > scores["_cap"]:
        top = max(snap["exposure_ticker"], key=snap["exposure_ticker"].get)
        opps.append({"id": _opp_id("div", top), "dimension": "diversification", "severity": "HIGH",
                     "description": f"{top} is {snap['max_weight']:.0%} of the portfolio, above the "
                                    f"{scores['_cap']:.0%} concentration cap. Trim to reduce single-name risk."})
    if scores["liquidity_score"] < 50:
        opps.append({"id": _opp_id("liq"), "dimension": "liquidity", "severity": "MEDIUM",
                     "description": "Weighted liquidity is low; consider holding more readily tradable assets."})
    if scores["risk_score"] < 50:
        opps.append({"id": _opp_id("risk"), "dimension": "risk", "severity": "HIGH",
                     "description": "Portfolio volatility is elevated; risk-reduction rebalancing is advised."})
    if len(snap["exposure_geo"]) <= 1 and snap["nav"]:
        opps.append({"id": _opp_id("geo"), "dimension": "diversification", "severity": "MEDIUM",
                     "description": "All exposure sits in a single geography; diversify across regions."})
    opps.sort(key=lambda o: SEVERITY_RANK.get(o["severity"], 9))
    return opps[:5]  # spec: cap at 5


async def load_positions(session: AsyncSession, user: User, entity: str | None = None) -> list[dict]:
    out = []
    for p in await list_positions(session, user, entity):
        m = p.meta or {}
        out.append({
            "ticker": p.ticker, "market": p.market,
            "quantity": float(p.quantity), "cost_basis": float(p.cost_basis),
            "current_price": float(p.current_price) if p.current_price is not None else 0.0,
            "volatility_pct": m.get("volatility_pct"), "liquidity_score": m.get("liquidity_score"),
        })
    return out


def tax_opportunities(positions: list[dict]) -> dict:
    """Section X.3 - harvesting / deferral / surtax / entity-routing scan."""
    st = get_settings()
    snap = compute_snapshot(positions)
    gains, losses = snap["unrealized_gains"], snap["unrealized_losses"]
    opps = []

    if losses > 0:
        offsetable = min(losses, gains) if gains > 0 else losses
        opps.append({
            "id": _opp_id("harvest", round(losses)), "trigger": "CAPITAL_LOSS_HARVESTING",
            "description": f"Harvest ~₪{round(losses):,} of unrealized losses to offset "
                           f"{'realized gains' if gains > 0 else 'future gains (carry-forward)'}.",
            "estimated_annual_tax_savings_currency": round(st.cgt_rate * offsetable, 2),
        })
    if gains > 0:
        opps.append({
            "id": _opp_id("defer", round(gains)), "trigger": "GAIN_DEFERRAL",
            "description": f"Defer realization on ~₪{round(gains):,} of unrealized gains to a later "
                           "tax year to delay CGT and stay under the surtax threshold.",
            "estimated_annual_tax_savings_currency": round(st.cgt_rate * gains * 0.10, 2),
        })
        if gains > st.surtax_threshold_ils:
            opps.append({
                "id": _opp_id("surtax"), "trigger": "SURTAX_THRESHOLD",
                "description": f"Projected gains exceed the ₪{round(st.surtax_threshold_ils):,} surtax "
                               "threshold; stagger realizations to avoid the surtax band.",
                "estimated_annual_tax_savings_currency": round(st.surtax_rate * (gains - st.surtax_threshold_ils), 2),
            })
    markets = {r["market"] for r in snap["rows"]}
    if len(markets) > 1:
        opps.append({
            "id": _opp_id("route"), "trigger": "ENTITY_ASSET_LOCATION",
            "description": "Holdings span multiple venues; route higher-tax assets into the most "
                           "efficient wrapper (Personal vs. Spouse vs. Corporate).",
            "estimated_annual_tax_savings_currency": round(st.cgt_rate * snap["nav"] * 0.01, 2),
        })
    total = round(sum(o["estimated_annual_tax_savings_currency"] for o in opps), 2)
    return {"opportunity_count": len(opps), "total_estimated_annual_savings_currency": total,
            "opportunities": opps}


def risk_alerts(snap: dict, cap: float | None = None) -> dict:
    """Section X.4 - concentration vectors monitor."""
    st = get_settings()
    cap = st.concentration_cap if cap is None else cap
    vectors = ["single_position", "sector", "geographic", "currency", "liquidity"]
    alerts = []

    if snap["max_weight"] > cap:
        top = max(snap["exposure_ticker"], key=snap["exposure_ticker"].get)
        alerts.append({"vector": "single_position", "severity": "HIGH",
                       "detail": f"{top} is {snap['max_weight']:.0%} (> {cap:.0%} single-position cap)."})
    for geo, w in snap["exposure_geo"].items():
        if w > st.analytics_geo_cap and snap["nav"]:
            alerts.append({"vector": "geographic", "severity": "MEDIUM",
                           "detail": f"{geo} geography is {w:.0%} of the book (> 80%)."})
    for cur, w in snap["exposure_cur"].items():
        if w > st.analytics_geo_cap and snap["nav"]:
            alerts.append({"vector": "currency", "severity": "MEDIUM",
                           "detail": f"{cur} currency exposure is {w:.0%} (> 80%); FX imbalance."})
    if snap["liquidity_avg"] < 50:
        alerts.append({"vector": "liquidity", "severity": "HIGH",
                       "detail": f"Weighted liquidity {snap['liquidity_avg']:.0f}/100 risks a lockout horizon."})
    alerts.sort(key=lambda a: SEVERITY_RANK.get(a["severity"], 9))
    return {"vectors_monitored": vectors, "alert_count": len(alerts), "alerts": alerts}
