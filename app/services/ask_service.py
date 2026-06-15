"""Phase H - natural-language Q&A grounded in the deterministic snapshot."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.context_service import gather
from app.services.llm import gemini_enabled, gemini_generate

_PROMPT = (
    "You are InvestWise, a careful personal-wealth assistant. Treat the JSON below as the ground "
    "truth about the user's portfolio, risk, performance, available options and strategies. You MAY "
    "apply widely-accepted investing principles (diversification, correlation, inflation hedging, "
    "fees, risk vs return, time horizon) to INTERPRET that data and answer 'should I...' questions "
    "with a balanced view - reasons for and against. NEVER invent specific numbers, prices or returns "
    "that are not in the data. When useful, point to concrete options or strategies that appear in the "
    "data (e.g. a commodity instrument or a named strategy). Be concise (3-6 sentences), plain language, "
    "₪ where relevant. End with: 'Not financial advice.'\n\n"
    "DATA:\n{data}\n\nQUESTION: {q}"
)


async def answer(session: AsyncSession, user: User, question: str) -> dict:
    ctx = await gather(session, user)
    grounded = sorted(ctx.keys())
    if not (question or "").strip():
        return {"llm": False, "answer": "Ask me something about your portfolio.", "grounded_on": grounded}
    if not gemini_enabled():
        return {"llm": False, "grounded_on": grounded, "context": ctx,
                "answer": "The AI assistant is off. Set GOOGLE_API_KEY to enable it - "
                          "your data context is included below."}
    out = gemini_generate(_PROMPT.format(data=json.dumps(ctx, default=str)[:12000], q=question.strip()))
    if not out:
        return {"llm": False, "grounded_on": grounded,
                "answer": "The assistant couldn't reach the model just now - try again shortly."}
    return {"llm": True, "answer": out, "grounded_on": grounded}
