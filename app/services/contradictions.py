"""No two cards on Today may argue with each other, or with a sleeve.

Eran, 2026-08-25: *"make sure that the recommendation cards do not contradict
each other."* What his book was actually serving that morning:

  1. HIGH   "TQQQ is 39% of your portfolio - Trim 24.231 TQQQ"
            ...while `btm_swing_dip` TARGETS 38.4% of NAV in TQQQ and held
            38.59%. The app's loudest card wanted to destroy 61% of a sleeve
            that was on target, because the rule behind it was armed at 15%
            when the sleeve was 15% and nobody re-levelled it.
  2. MEDIUM "Sell Equities (~12,139)"  beside
     MEDIUM "Buy 2,120 of VXUS (global equity)"        -- sell equity, buy equity.
  3. MEDIUM "Buy Commodities (~5,293)" (25% target) beside
     LOW    "Add a commodities sleeve - 2,120 of USO ... against your 10%
            target"                                    -- two targets, one class.
  4. Three cards funding themselves from the same equity pool with no shared
     budget: 5,003 + 12,139 + 2,180 = 19,322 of selling out of 20,567 held.

`_reconcile` already existed and already merged duplicate rebalance legs. It is
about cards that say the SAME thing twice. This module is about cards that say
OPPOSITE things once each, which is the harder failure to see and the one that
actually loses money: every card individually defensible, the set incoherent.

DESIGN: suppress the loser, and SAY SO. A dropped card that vanishes silently is
how the eleven buried cards of PR 4 happened. Every suppression appends to
`notes` on the surviving card and is reported in `contradictions[]` on the
payload, so "why did the trim card disappear" has an answer on the screen.
"""
from __future__ import annotations

# Only these kinds move money. An advisory card cannot contradict anything,
# because accepting it does nothing -- and suppressing advisories would hide
# exactly the explanations a user needs when two real cards disagree.
_MOVES_MONEY = {"trim", "sell", "sell_losers", "buy", "buy_funded", "rebalance_to_objective",
                "redeploy_cash", "fund_sleeve", "swap"}

_SELLS = {"trim", "sell", "sell_losers", "rebalance_to_objective"}
_BUYS = {"buy", "buy_funded", "redeploy_cash", "fund_sleeve"}


def _kind(rec: dict) -> str:
    return str(((rec.get("apply") or {}).get("kind") or "none")).lower()


def _tickers(rec: dict) -> set[str]:
    from app.services.card_scope import _tickers_of
    return set(_tickers_of(rec))


def _note(rec: dict, text: str) -> None:
    rec.setdefault("notes", [])
    if text not in rec["notes"]:
        rec["notes"].append(text)


def check(recs: list[dict], *, sleeve_targets: dict[str, float], nav: float,
          weights: dict[str, float], book_target: dict[str, float] | None = None) -> tuple[list[dict], list[dict]]:
    """Return (surviving cards, contradictions found).

    `sleeve_targets` is TICKER -> target share of NAV, summed across sleeves --
    the same primitive the funding engine and the tax harvester use, so a card
    cannot be judged against a different idea of what the sleeves want.
    `weights` is TICKER -> current share of NAV.
    """
    found: list[dict] = []
    drop: set[str] = set()
    by_id = {r.get("id"): r for r in recs}

    # ------------------------------------------------------------------ #
    # 1. A card may not sell a holding below the sleeve target that governs it.
    #
    # This is the one that was live. The sleeve target is a decision the user
    # made; a rule armed at a stale level is not a reason to overturn it
    # silently. The card is suppressed and the STALE RULE is named, because the
    # rule is the thing that needs fixing.
    # ------------------------------------------------------------------ #
    for r in recs:
        if _kind(r) not in _SELLS:
            continue
        for tk in _tickers(r):
            want = sleeve_targets.get(tk)
            if want is None:
                continue
            have = weights.get(tk, 0.0)
            # Only a contradiction if the position is AT OR BELOW its sleeve
            # target. Above it, trimming back toward the sleeve is exactly
            # right and the card should stand.
            if have <= want + 0.005:
                drop.add(r.get("id"))
                found.append({
                    "kind": "sells_a_sleeve_at_target",
                    "card": r.get("id"), "title": r.get("title"), "ticker": tk,
                    "detail": (f"{tk} is {have:.1%} of the book against a sleeve target of "
                               f"{want:.1%}. Selling it moves you away from a strategy you "
                               f"chose, not toward it."),
                    "suppressed": True,
                })
                break

    # ------------------------------------------------------------------ #
    # 2. No two surviving cards may buy and sell the same asset class.
    # ------------------------------------------------------------------ #
    live = [r for r in recs if r.get("id") not in drop]
    sell_cls: dict[str, dict] = {}
    buy_cls: dict[str, dict] = {}
    for r in live:
        k = _kind(r)
        cls = (r.get("apply") or {}).get("asset_class") or (r.get("meta") or {}).get("asset_class")
        if not cls:
            continue
        if k in _SELLS and str(r.get("title", "")).lower().startswith("sell"):
            sell_cls.setdefault(cls, r)
        elif k in _BUYS or str(r.get("title", "")).lower().startswith("buy"):
            buy_cls.setdefault(cls, r)
    for cls in set(sell_cls) & set(buy_cls):
        a, b = sell_cls[cls], buy_cls[cls]
        found.append({"kind": "buys_and_sells_one_class", "card": b.get("id"),
                      "other": a.get("id"), "asset_class": cls,
                      "detail": f"One card sells {cls} while another buys it.",
                      "suppressed": False})
        _note(a, f"Another card buys {cls}. Take them together or neither — "
                 f"accepting only one moves your mix the wrong way.")
        _note(b, f"Another card sells {cls}. Take them together or neither.")

    # ------------------------------------------------------------------ #
    # 3. Two cards may not quote different targets for the same asset class.
    #
    # Not suppressed -- reported. If this fires it is a BUG in the generators,
    # not a choice the user should be asked to arbitrate, and it should be
    # loud in the payload where a smoke test can see it.
    # ------------------------------------------------------------------ #
    if book_target:
        for r in live:
            raw = ((r.get("audit_trail") or {}).get("raw_data") or {})
            cls, tw = raw.get("asset_class"), raw.get("target_weight")
            if not cls or tw is None:
                continue
            want = book_target.get(cls)
            if want is None:
                continue
            if abs(float(tw) - float(want)) > 0.005:
                found.append({
                    "kind": "two_targets_for_one_class", "card": r.get("id"),
                    "asset_class": cls,
                    "detail": (f"This card measures {cls} against {float(tw):.1%} while the "
                               f"book's target is {want:.1%}."),
                    "suppressed": False})

    # ------------------------------------------------------------------ #
    # 4. The cards may not, together, propose selling more than the book holds.
    #
    # Each card funds itself independently, so three defensible cards can add
    # up to liquidating the portfolio. Reported with the total rather than
    # suppressed: which one to drop is the user's call, not this function's.
    # ------------------------------------------------------------------ #
    total_sell = 0.0
    sellers: list[str] = []
    for r in live:
        if _kind(r) in _MOVES_MONEY and (r.get("est_amount") or 0) > 0 and _kind(r) != "none":
            amt = float(r.get("est_amount") or 0)
            total_sell += amt
            sellers.append(r.get("id"))
    invested = max(0.0, nav * (1.0 - weights.get("CASH", 0.0)))
    if invested and total_sell > invested * 0.5 and len(sellers) > 1:
        found.append({
            "kind": "cards_exceed_the_book", "cards": sellers,
            "detail": (f"Accepting every card would move about ₪{total_sell:,.0f} of a "
                       f"₪{invested:,.0f} invested book. Each card funds itself, so they "
                       f"do not share a budget — take them one at a time and re-read."),
            "suppressed": False})
        for rid in sellers:
            _note(by_id.get(rid, {}), "These cards do not share a funding budget. "
                                      "Accept one, then re-read the rest.")

    return [r for r in recs if r.get("id") not in drop], found
