"""PR 102: a card asks you to do something, and Accept does it.

Knowledge without an ask is not a card. It is a **Heads-up**: a one-line note in
a collapsed list at the bottom of Today. It has no "Mark as done" (there is
nothing to do), a dismissed one never comes back, and it never notifies.

Eran, 2026-10-05 (Execution plan v2, PR 102): twelve card types gave advice
with no Accept -- "Mark as done" was the only button, and it cleared a card
without anything having happened. This module is the one place that decides
which is which, so a new card type cannot slip back in as advice-with-a-button:
`tests/test_every_card_can_be_acted_on.py` walks the real card set and fails on
any card whose Accept does nothing.
"""
from __future__ import annotations

import json
import re

# Eran (D4): an actionable card must be worth acting on. Tax harvests are
# measured in tax saved, everything else in shekels traded.
MIN_TAX_SAVING_ILS = 50.0
MIN_TRADE_ILS = 100.0
MIN_TRADE_PCT_OF_BOOK = 0.5

# Kinds whose `est_amount` is the size of the trade Accept makes. A card that
# only edits a plan or a rule is not sized in shekels and has no floor.
_TRADE_KINDS = {"trim", "sell_position", "buy_funded", "redeploy_cash", "fund_sleeve",
                "fund_plan", "core_to_basket"}

# Row 9: "Put idle cash to work" is said by the actionable cash-redeploy card.
_REMOVED_IDS = {"idle_cash"}

_KV_PREFIX = "headsups_dismissed:"


def min_trade_ils(nav: float) -> float:
    """The card's own minimums: 100 ILS or 0.5% of the book, whichever is larger
    (PR 104d: both are settings). The floor actually applied is
    `thresholds.card_floor_ils`, which also never goes under the funded minimum."""
    from app.services import thresholds
    ils = float(thresholds.get("min_card_trade_ils", MIN_TRADE_ILS))
    pct = float(thresholds.get("min_card_trade_pct", MIN_TRADE_PCT_OF_BOOK))
    return max(ils, pct / 100.0 * max(0.0, float(nav or 0.0)))


def _one_line(card: dict) -> str:
    """The card's own first sentence -- never a new one."""
    text = str(card.get("action") or "").strip()
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    return first if len(first) <= 220 else first[:217].rstrip() + "..."


def to_headsup(card: dict) -> dict:
    meta = card.get("meta") or {}
    out = {"id": card["id"], "title": card.get("title") or "", "line": _one_line(card),
           "dimension": card.get("dimension"), "ticker": meta.get("ticker"),
           # The card it came from, unrendered: the audit trail and the numbers
           # behind the one line stay inspectable.
           "card": {k: v for k, v in card.items() if k != "headsup_act"}}
    act = card.get("headsup_act")
    if act and (act.get("apply") or {}).get("kind"):
        # D3: a heads-up may carry ONE act option (e.g. raise a cap). Accept on
        # it runs through the same handler as any card.
        out["act"] = {"label": act["label"]}
        out["apply"] = act["apply"]
    return out


def split(recs: list[dict], *, nav: float, actionable) -> tuple[list[dict], list[dict], list[dict]]:
    """(cards, heads_ups, dropped). `actionable(card)` is the app's own test of
    whether Accept does something (`recommendations._is_actionable`)."""
    from app.services import thresholds
    cards, heads, dropped = [], [], []
    # PR 115 (D-c): ONE floor -- never under the smallest funded trade.
    floor = thresholds.card_floor_ils(nav)
    for c in recs:
        kind = (c.get("apply") or {}).get("kind") or "none"
        if (c.get("meta") or {}).get("headsup_kind") in _REMOVED_IDS:
            dropped.append({"id": c.get("id"), "reason": "said by the cash-redeploy card"})
            continue
        _tax_min = float(thresholds.get("min_card_tax_ils", MIN_TAX_SAVING_ILS))
        if c.get("dimension") == "tax" and kind == "sell_losers" \
                and float(c.get("est_amount") or 0.0) < _tax_min:
            dropped.append({"id": c.get("id"), "reason": f"saves under {_tax_min:g} ILS (your smallest tax card)"})
            continue
        if not actionable(c):
            heads.append(to_headsup(c))
            continue
        # A triggered rule's card is the rule's firing: it is never too small.
        is_rule = str(c.get("id") or "").startswith("rule_")
        amt = c.get("est_amount")
        if kind in _TRADE_KINDS and not is_rule and amt is not None and abs(float(amt)) < floor:
            heads.append(to_headsup(c))
            continue
        cards.append(c)
    return cards, heads, dropped


def _key(email: str) -> str:
    return (_KV_PREFIX + email)[:80]


async def dismissed(session, user) -> set[str]:
    from app.models.tables import KVSetting
    row = await session.get(KVSetting, _key(user.email))
    try:
        return set(json.loads(row.value)) if row else set()
    except (TypeError, ValueError):
        return set()


async def dismiss(session, user, hid: str) -> None:
    """Gone for good: a heads-up never re-fires."""
    from app.models.tables import KVSetting
    have = await dismissed(session, user)
    have.add(hid)
    row = await session.get(KVSetting, _key(user.email))
    if row is None:
        session.add(KVSetting(key=_key(user.email), value=json.dumps(sorted(have))))
    else:
        row.value = json.dumps(sorted(have))
    await session.commit()
