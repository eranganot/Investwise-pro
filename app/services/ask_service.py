"""Phase H - natural-language Q&A grounded in the deterministic snapshot."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.context_service import for_prompt, gather
from app.services.digest_service import unsupported_numbers
from app.services.llm import gemini_enabled, gemini_generate_ex

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


DISCLAIMER = "Not financial advice."


def with_disclaimer(text: str) -> str:
    """The text, ending with the disclaimer (PR 110: it was prompt-only)."""
    t = (text or "").rstrip()
    return t if "not financial advice" in t.lower() else f"{t}\n\n{DISCLAIMER}"


async def answer(session: AsyncSession, user: User, question: str) -> dict:
    ctx = await gather(session, user)
    grounded = sorted(ctx.keys())
    if not (question or "").strip():
        return {"llm": False, "answer": "Ask me something about your portfolio.", "grounded_on": grounded}
    if not gemini_enabled():
        return {"llm": False, "grounded_on": grounded, "context": ctx,
                "answer": "The AI assistant is off. Set GOOGLE_API_KEY to enable it - "
                          "your data context is included below."}
    # LABELLED for the model, PLAIN for the caller. The assistant hands the
    # same figures to the same kind of reader as the digest, so it gets the same
    # protection -- but `context` in the response keeps its original shape.
    out, err = gemini_generate_ex(
        _PROMPT.format(data=json.dumps(for_prompt(ctx), default=str)[:12000],
                       q=question.strip()))
    if not out:
        # Say WHY. "Couldn't reach the model" made an exhausted billing balance
        # (a two-minute fix) look identical to a permanent outage.
        return {"llm": False, "grounded_on": grounded, "error": err,
                "answer": err or "The assistant couldn't reach the model just now - try again shortly."}
    # PR 110 (C8): "never invent numbers" was prompt-only. A stubbed reply
    # ("returned 37.4% ... TEVA will hit 412") reached the screen verbatim
    # while the digest's own checker flags both figures. Numbers the user
    # typed are allowed ("should I buy 50 shares?").
    bad = unsupported_numbers(out, {"ctx": ctx, "question": question})
    if bad:
        return {"llm": False, "verified": False, "unsupported_numbers": bad, "grounded_on": grounded,
                "answer": ("I couldn't answer that from your data without quoting figures your data "
                           "doesn't contain, so I'm not showing that answer. Try asking about a "
                           "specific holding, your mix or your goal. " + DISCLAIMER)}
    return {"llm": True, "verified": True, "answer": with_disclaimer(out), "grounded_on": grounded}
