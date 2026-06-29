"""Gemini-powered summaries & research (Phase: Google Finance / Gemini).

Portfolio summary, per-holding summary, macro/futures summary, and deep
research-per-holding (grounded on live web search). Everything degrades
gracefully: with no GOOGLE_API_KEY the endpoints return a deterministic
fallback rather than failing.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.llm import gemini_enabled, gemini_generate, gemini_generate_grounded
from app.services.markets_service import futures_snapshot
from app.services.plan_service import effective_caps, get_plan
from app.services.portfolio_analytics import compute_snapshot, health_scores, load_positions
from app.services.recommendations import build_recommendations

_DISCLAIMER = "This is general information, not financial advice."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _portfolio_context(session: AsyncSession, user: User) -> dict:
    positions = await load_positions(session, user)
    snap = compute_snapshot(positions) if positions else {}
    cap = effective_caps(await get_plan(session, user)).get("concentration_cap")
    health = health_scores(snap, cap) if snap else {}
    recs = await build_recommendations(session, user)
    holdings = []
    nav = snap.get("nav") or 0
    for p in positions:
        val = (p.get("current_price") or 0) * (p.get("quantity") or 0)
        holdings.append({"ticker": p["ticker"], "value_pct": round(val / nav * 100, 1) if nav else None})
    holdings.sort(key=lambda h: (h["value_pct"] or 0), reverse=True)
    snap = await asyncio.to_thread(futures_snapshot)
    mkt = snap.get("market", {})
    return {
        "nav_ils": round(nav) if nav else None,
        "health": {k: health.get(k) for k in (
            "wealth_health_score", "risk_score", "tax_efficiency_score",
            "diversification_score", "liquidity_score") if k in health},
        "top_holdings": holdings[:8],
        "open_actions": [r.get("title") for r in recs.get("recommendations", [])][:6],
        "market_regime": mkt.get("regime"),
        "market_rationale": mkt.get("rationale"),
    }


async def portfolio_summary(session: AsyncSession, user: User) -> dict:
    ctx = await _portfolio_context(session, user)
    if not ctx.get("top_holdings"):
        return {"llm": False, "summary": "Add a few holdings and I'll summarize how your wealth is doing.",
                "generated_at": _now()}
    if not gemini_enabled():
        h = ctx.get("health", {}).get("wealth_health_score")
        fallback = (f"Your wealth health is {h}/100. "
                    f"Market backdrop looks {ctx.get('market_regime','neutral')} "
                    f"({ctx.get('market_rationale','')}). "
                    + (f"{len(ctx['open_actions'])} suggested action(s) await in 'What to do now'."
                       if ctx.get("open_actions") else "Nothing urgent to act on.") + f" {_DISCLAIMER}")
        return {"llm": False, "summary": fallback, "generated_at": _now()}
    prompt = (
        "You are InvestWise, a calm, plain-spoken wealth assistant for a retail investor. "
        "Using ONLY the JSON below, write a 3-5 sentence summary: lead with how their wealth is doing, "
        "note the market backdrop, then the 1-2 most useful actions. Use ₪ for amounts. "
        f"Never invent numbers. End with '{_DISCLAIMER}'\n\n" + json.dumps(ctx, default=str)[:6000])
    out = gemini_generate(prompt)
    return {"llm": bool(out), "summary": out or "Summary unavailable right now.",
            "generated_at": _now(), "context": ctx}


async def holding_summary(session: AsyncSession, user: User, ticker: str) -> dict:
    ticker = ticker.strip().upper()
    positions = await load_positions(session, user)
    pos = next((p for p in positions if p["ticker"].upper() == ticker), None)
    if not pos:
        return {"llm": False, "ticker": ticker, "summary": f"{ticker} isn't in your portfolio.",
                "generated_at": _now()}
    cur = pos.get("current_price") or 0
    cost = pos.get("cost_basis") or 0
    pnl_pct = round((cur - cost) / cost * 100, 1) if cost else None
    ctx = {"ticker": ticker, "market": pos.get("market"), "shares": pos.get("quantity"),
           "current_price": cur, "cost_basis": cost, "gain_loss_pct": pnl_pct,
           "asset_class": pos.get("asset_class")}
    if not gemini_enabled():
        return {"llm": False, "ticker": ticker, "context": ctx,
                "summary": (f"{ticker}: {('up' if (pnl_pct or 0) >= 0 else 'down')} "
                            f"{abs(pnl_pct) if pnl_pct is not None else '?'}% vs your cost. {_DISCLAIMER}"),
                "generated_at": _now()}
    prompt = (
        f"In 2-3 plain sentences, explain what the investor should understand about their {ticker} "
        "position right now, using ONLY this JSON (don't invent prices or news). Mention how it's doing "
        f"vs their cost and what role it plays. End with '{_DISCLAIMER}'\n\n" + json.dumps(ctx, default=str))
    out = gemini_generate(prompt)
    return {"llm": bool(out), "ticker": ticker, "context": ctx,
            "summary": out or "Summary unavailable.", "generated_at": _now()}


async def macro_summary() -> dict:
    snap = await asyncio.to_thread(futures_snapshot)
    if not gemini_enabled():
        m = snap.get("market", {})
        return {"llm": False, "summary": f"Markets look {m.get('regime','neutral')} — {m.get('rationale','')}. {_DISCLAIMER}",
                "futures": snap.get("futures", []), "market": snap.get("market", {}), "generated_at": _now()}
    prompt = (
        "You are a markets desk writing a 3-4 sentence 'what's moving' note for a retail investor. "
        "Using ONLY this JSON of futures and the derived regime, summarize the market backdrop in plain "
        f"language (stocks, oil, gold, rates, dollar, volatility) and what regime it implies. End with '{_DISCLAIMER}'\n\n"
        + json.dumps(snap, default=str)[:4000])
    out = gemini_generate(prompt)
    return {"llm": bool(out), "summary": out or "Summary unavailable.",
            "futures": snap.get("futures", []), "market": snap.get("market", {}), "generated_at": _now()}


async def deep_research(session: AsyncSession, user: User, ticker: str) -> dict:
    ticker = ticker.strip().upper()
    positions = await load_positions(session, user)
    pos = next((p for p in positions if p["ticker"].upper() == ticker), None)
    held = bool(pos)
    pos_ctx = ""
    if pos:
        cur, cost = pos.get("current_price") or 0, pos.get("cost_basis") or 0
        pnl = round((cur - cost) / cost * 100, 1) if cost else None
        pos_ctx = (f" The investor HOLDS it: {pos.get('quantity')} shares, cost ₪{cost}, "
                   f"now ₪{cur} ({pnl}% vs cost).")
    if not gemini_enabled():
        return {"llm": False, "ticker": ticker, "held": held,
                "research": "Live research needs a Gemini API key (GOOGLE_API_KEY).",
                "sources": [], "generated_at": _now()}
    prompt = (
        f"Research the stock/asset {ticker} using up-to-date web sources. Then give a retail investor a "
        "concise briefing with these sections:\n"
        "1) What it is (one line).\n"
        "2) Recent news & price drivers (last few weeks).\n"
        "3) Bull case / bear case (2-3 bullets each).\n"
        "4) Verdict for someone considering or holding it." + pos_ctx + "\n"
        f"Be balanced, cite what you found, and end with '{_DISCLAIMER}'")
    res = gemini_generate_grounded(prompt)
    if not res:
        return {"llm": False, "ticker": ticker, "held": held,
                "research": "Research is temporarily unavailable. Please try again.",
                "sources": [], "generated_at": _now()}
    return {"llm": True, "ticker": ticker, "held": held,
            "research": res.get("text", ""), "sources": res.get("sources", []),
            "generated_at": _now()}
