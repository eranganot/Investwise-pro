"""Phase H - weekly wealth digest. LLM-written when a key is set, with a
deterministic plain-text fallback so it's always useful."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.context_service import gather
from app.services.llm import gemini_enabled, gemini_generate


def _fallback(ctx: dict) -> str:
    nav = ctx.get("nav_ils", 0) or 0
    perf = ctx.get("performance") or {}
    risk = ctx.get("risk") or {}
    recs = ctx.get("recommendations") or []
    lines = [f"Net worth tracked: ₪{nav:,.0f}."]
    if perf.get("total_return_pct") is not None:
        lines.append(f"Performance: {perf['total_return_pct']}% total"
                     + (f", {perf.get('excess_return_pct')}% vs {perf.get('benchmark')}."
                        if perf.get('excess_return_pct') is not None else "."))
    if risk.get("annualized_volatility_pct") is not None:
        lines.append(f"Risk: volatility {risk['annualized_volatility_pct']}%, "
                     f"1-day VaR(95%) {risk.get('var_95_1d_pct')}%, beta {risk.get('beta')}.")
    if recs:
        lines.append("Top actions: " + "; ".join(r["title"] for r in recs[:3]) + ".")
    lines.append("Not financial advice.")
    return " ".join(lines)


async def build(session: AsyncSession, user: User) -> dict:
    ctx = await gather(session, user)
    fallback = _fallback(ctx)
    now = datetime.now(timezone.utc).isoformat()
    if not gemini_enabled():
        return {"llm": False, "digest": fallback, "generated_at": now}
    prompt = (
        "Write a short, friendly weekly wealth digest (4-6 sentences) for the account owner, using "
        "ONLY this JSON. Lead with how they're doing, then the top 1-3 actions, then one risk note. "
        "Plain language, ₪ where relevant. Never invent numbers. End with 'Not financial advice.'\n\n"
        + json.dumps(ctx, default=str)[:12000])
    out = gemini_generate(prompt)
    return {"llm": bool(out), "digest": out or fallback, "generated_at": now}
