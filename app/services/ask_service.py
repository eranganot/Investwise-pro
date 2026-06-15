"""Phase H - natural-language Q&A grounded in the deterministic snapshot."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.context_service import gather
from app.services.llm import gemini_enabled, gemini_generate

_PROMPT = (
    "You are InvestWise, a careful personal-wealth assistant. Answer the user's question "
    "using ONLY the JSON data below. Never invent or estimate numbers that aren't present; "
    "if the data doesn't contain the answer, say so plainly. Be concise (2-5 sentences), use "
    "plain language and ₪ where relevant. End with: 'Not financial advice.'\n\n"
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
