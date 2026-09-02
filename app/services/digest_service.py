"""Phase H - weekly wealth digest. LLM-written when a key is set, with a
deterministic plain-text fallback so it's always useful.

WHAT WENT WRONG, 2026-09-01, in a push notification on Eran's phone:

    "Hello! Your account is currently valued at 21,474.35 ILS, with a total
     return of 17.76%. You have an 89.2% probability of reaching your 102,973
     goal in 5 years. To diversify your holdings, consider buying 2,147 ILS of
     PDBC (Broad commodities) to reach your 10% target in this asset class."

Four claims. Measured against the app the same morning:

  * 21,474.35 was real -- the NAV snapshot for 2026-08-31.
  * 17.76% was real and was NOT a return on that NAV. It is
    `performance.total_return_pct`, a BACKFILL of the holdings held today
    priced through 252 sessions of real closes. The account's actual return on
    the 20,000 contributed was +3.89%. Two true numbers, incompatible bases,
    one false sentence -- and the prompt's "never invent numbers" did not
    forbid it, because nothing was invented.
  * 89.2% / 102,973 / 5 years: none of the three was in the context at all. The
    plan target is 100,288.77 over 6 years and `/plan/projection` says 22.7%.
    `risk.goal` was `null`. That one WAS invented.
  * 2,147 ILS is exactly 10% of NAV -- computed by the model, from a 10%
    commodities target that lives in `OBJ_TARGET["Grow"]` and is not in the
    context either. The book's own target has no commodities line.

So the fix is two things, and neither is a better instruction:

1.  **Every quotable figure carries its basis.** `context_service` sends
    `{value, means}` and the prompt requires the basis to be used. That is what
    stops a NAV and a backfill return being welded together.
2.  **Every number in the generated text must appear in the context.**
    `unsupported_numbers` finds the ones that do not, and `build` sends the
    deterministic fallback instead. A model that computes 10% of NAV, or
    produces an 89.2% from nowhere, is caught by arithmetic rather than by
    hoping the instruction held.

The fallback is not a degraded mode. It states every figure with its basis, and
it is what ships whenever the check fails -- which is the outcome this module
should prefer when it cannot prove the prose.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.context_service import for_prompt, gather
from app.services.llm import gemini_enabled, gemini_generate

logger = logging.getLogger(__name__)

#: Numbers written in the text are matched against the context after rounding to
#: the same number of decimals the text used, so "21,474" matches 21474.35 and
#: "17.8%" matches 17.76. Anything looser starts accepting 17.76 for 17.03.
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

#: Written numbers a reader cannot be misled by, and which no context can be
#: expected to contain. Deliberately tiny: "5 years" was one of the false
#: claims, and 5 is a small number, so smallness is not a licence.
_ALWAYS_OK = {0.0, 1.0, 2.0, 100.0}


def _value(v):
    """The number out of a `{value, means}` wrapper, or the bare value."""
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def numbers_in(text: str) -> list[float]:
    """Every number a reader would see in this text."""
    out = []
    for m in _NUM.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _context_numbers(ctx) -> set[float]:
    """Every number anywhere in the context, unwrapped and flattened."""
    found: set[float] = set()

    def walk(node):
        node = _value(node)
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(float(node))
            return
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
            return
        if isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
            return
        if isinstance(node, str):
            # A number that reached the context inside a STRING -- a date, a
            # ticker with digits, an amount already formatted into a card's
            # `action` -- is still a number the model was GIVEN, so quoting it
            # is honest. Excluding these would fail the digest for repeating a
            # recommendation back correctly.
            for n in numbers_in(node):
                found.add(n)

    walk(ctx)
    # Percentages are the most-quoted figures and the context stores some as
    # FRACTIONS (`probability_meets_target: 0.227`). A model writing "23%" is
    # quoting what it was given, so admit the percent form of every fraction.
    for n in list(found):
        if -1.0 <= n <= 1.0:
            found.add(round(n * 100, 4))
    return found


def unsupported_numbers(text: str, ctx) -> list[float]:
    """Numbers in `text` that are not in `ctx`. Empty means every figure is grounded.

    Matching is by ROUNDING, not by tolerance: a written number matches a
    context number when the context number, rounded to the number of decimals
    the text used, equals it. "21,474" matches 21474.35 and "23%" matches 0.227;
    17.76 does NOT match 17.03, which is the distinction the check exists for.
    """
    allowed = _context_numbers(ctx)
    bad: list[float] = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(0)
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if n in _ALWAYS_OK:
            continue
        dp = len(raw.split(".")[1]) if "." in raw else 0
        if any(round(a, dp) == n for a in allowed):
            continue
        bad.append(n)
    return bad


def _fallback(ctx: dict) -> str:
    nav = _value(ctx.get("nav_ils")) or 0
    perf = ctx.get("performance") or {}
    risk = ctx.get("risk") or {}
    recs = ctx.get("recommendations") or []
    rules = ctx.get("triggered_rules") or []
    lines = [f"Net worth tracked: ₪{nav:,.0f}."]
    if rules:
        lines.append(f"⚠️ {len(rules)} trading rule(s) triggered: "
                     + "; ".join(r["title"] for r in rules[:3]) + ".")
    _tr = _value(perf.get("total_return_pct"))
    if _tr is not None:
        # NAMED, every time. This is the sentence that went wrong: without the
        # word "backfill" a reader takes it as the return on the figure above it.
        _ex = _value(perf.get("excess_return_pct"))
        lines.append(f"Backfill of today's holdings: {_tr}% over the window"
                     + (f", {_ex}% vs {_value(perf.get('benchmark'))}."
                        if _ex is not None else ".")
                     + " That is what these holdings would have done, not what"
                       " your account did.")
    # ONLY THE PARTS THAT EXIST. This used to interpolate whatever was there,
    # and a book with no VaR row rendered "1-day VaR(95%) None%" -- caught by
    # the new check, which reads None as prose and 95 as an unsupported figure.
    _parts = []
    for _label, _val, _suffix in (
            ("volatility", _value(risk.get("annualized_volatility_pct")), "%"),
            ("1-day VaR (95% confidence)", _value(risk.get("var_95_1d_pct")), "%"),
            ("beta", _value(risk.get("beta")), "")):
        if _val is not None:
            _parts.append(f"{_label} {_val}{_suffix}")
    if _parts:
        lines.append("Risk: " + ", ".join(_parts) + ".")
    pj = ctx.get("projection") or {}
    _p, _t = _value(pj.get("probability_meets_target")), _value(pj.get("target_amount"))
    if _p is not None and _t is not None:
        lines.append(f"Chance of reaching ₪{float(_t):,.0f} in "
                     f"{_value(pj.get('years'))} years: {round(float(_p) * 100)}%.")
    if recs:
        lines.append("Top actions: " + "; ".join(r["title"] for r in recs[:3]) + ".")
    lines.append("Not financial advice.")
    return " ".join(lines)


async def build(session: AsyncSession, user: User) -> dict:
    ctx = await gather(session, user)
    # Surface any triggered trading rules so the digest leads with them.
    try:
        from app.services.rules_service import triggered_rule_recs
        ctx["triggered_rules"] = [{"title": r["title"], "action": r["action"]}
                                  for r in await triggered_rule_recs(session, user)]
    except Exception:  # noqa: BLE001
        ctx["triggered_rules"] = []
    fallback = _fallback(ctx)
    now = datetime.now(timezone.utc).isoformat()
    if not gemini_enabled():
        return {"llm": False, "digest": fallback, "generated_at": now, "verified": True}
    prompt = (
        "Write a short, friendly weekly wealth digest (4-6 sentences) for the account owner, using "
        "ONLY this JSON. If any 'triggered_rules' are present, lead with them (the user's own stop-loss/"
        "take-profit rules fired). Then how they're doing, the top 1-3 actions, and one risk note. "
        "Plain language, ₪ where relevant.\n\n"
        "RULES ABOUT NUMBERS -- these are checked automatically and the digest is discarded if "
        "broken:\n"
        "* Use ONLY numbers that appear in this JSON. Do NOT add, subtract, convert, annualise, or "
        "take a percentage OF anything. A number you worked out is not a number you were given.\n"
        "* Many figures arrive as {\"value\": ..., \"means\": ...}. Quote the value and describe it "
        "the way 'means' does. A figure described as a BACKFILL must never be called the return on "
        "the account or on its value.\n"
        "* If a number you want is not here, leave the claim out.\n"
        "End with 'Not financial advice.'\n\n"
        + json.dumps(for_prompt(ctx), default=str)[:12000])
    out = gemini_generate(prompt)
    if not out:
        return {"llm": False, "digest": fallback, "generated_at": now, "verified": True}

    bad = unsupported_numbers(out, ctx)
    if bad:
        # THE DIGEST IS DISCARDED, NOT PATCHED. A sentence whose number cannot
        # be traced is not repairable from here -- the claim around it was built
        # on that number. The deterministic text says the same things and every
        # figure in it came from the context by construction.
        logger.warning("digest discarded: %d number(s) not in the context: %s",
                       len(bad), ", ".join(str(b) for b in bad[:6]))
        return {"llm": False, "digest": fallback, "generated_at": now,
                "verified": False, "unsupported_numbers": bad,
                "reason": "the generated text quoted numbers that are not in the context"}
    return {"llm": True, "digest": out, "generated_at": now, "verified": True}
