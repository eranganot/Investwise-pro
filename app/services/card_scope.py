"""Which strategy does this card serve? Every card has to answer.

Eran, 2026-08-25: *"I want to make sure that all the cards on Today are aligned
with my strategies (sleeves+core). Also, I want to add indication to the cards
on Today that marked to which strategy it is applied to."*

The label is the smaller half of that ask. The reason it is worth building is
the larger half: **a card that cannot name its scope is usually a card computed
at the wrong one.** Both defects found that day were exactly this shape --

  * the rebalance card measured the CORE's 36% target against 100% of the book,
    because nothing forced it to say which part of the book it was talking
    about;
  * the top card on Today read "TQQQ is 39% of your portfolio - trim" on a book
    whose `btm_swing_dip` sleeve TARGETS 38.4% in TQQQ, because the rule behind
    it carried `strategy_id: null` and so belonged to nothing.

So scope is derived from what the card already carries, stamped once at the
chokepoint every card passes through, and then USED -- by `contradictions.py`,
which cannot check "does this card fight a sleeve?" without knowing which
sleeve, if any, the card is standing on.

THREE KINDS, and the distinction is the point:

  sleeve  the card acts on a ticker one of your sleeves targets. It is arguing
          with a strategy you chose, and it had better have a good reason.
  core    the card acts on the part of the book no sleeve claims. Free to move.
  book    the card is about the whole portfolio -- your goal, your fees, your
          cash floor. It belongs to no strategy and says so, rather than
          pretending to be scoped.
"""
from __future__ import annotations

_BOOK = {"kind": "book", "strategy_id": None, "name": None, "label": "Whole book"}


def _sleeve(strategy_id: str, name: str | None, pct: float | None) -> dict:
    return {"kind": "sleeve", "strategy_id": strategy_id,
            "name": name or strategy_id,
            "label": f"{name or strategy_id}" + (f" · {pct:g}% sleeve" if pct else " sleeve")}


def _core(strategy_id: str | None, name: str | None, pct: float | None) -> dict:
    return {"kind": "core", "strategy_id": strategy_id,
            "name": name or strategy_id,
            "label": (f"Core · {name}" if name else "Core")
                     + (f" · {pct:g}% of book" if pct else "")}


def _tickers_of(rec: dict) -> list[str]:
    """Every ticker a card names, wherever it hides them.

    Cards grew their subject fields one generator at a time -- `apply.ticker`,
    `apply.tickers`, `apply.legs[].ticker`, `apply.rules[].ticker`,
    `meta.ticker`. A scope derived from only the first of those would silently
    call a multi-leg redeploy "whole book" and lose the very association this
    module exists to make.
    """
    ap = rec.get("apply") or {}
    meta = rec.get("meta") or {}
    out: list[str] = []
    for v in (ap.get("ticker"), meta.get("ticker"), rec.get("ticker")):
        if v:
            out.append(str(v).upper())
    for v in (ap.get("tickers") or meta.get("tickers") or []):
        if v:
            out.append(str(v).upper())
    for leg in (ap.get("legs") or []):
        if isinstance(leg, dict) and leg.get("ticker"):
            out.append(str(leg["ticker"]).upper())
    for r in (ap.get("rules") or []):
        if isinstance(r, dict) and r.get("ticker"):
            out.append(str(r["ticker"]).upper())
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def scope_for(rec: dict, *, ticker_owner: dict[str, str], sleeve_names: dict[str, str],
              sleeve_pcts: dict[str, float], core_id: str | None,
              core_name: str | None, core_pct: float | None) -> dict:
    """The scope of one card. Pure, so the mapping is testable without a book.

    `ticker_owner` maps TICKER -> strategy_id for every ticker a sleeve targets.
    A ticker no sleeve claims is core by definition, because the core IS the
    remainder -- there is no third place for it to live.
    """
    ap = rec.get("apply") or {}
    rid = str(rec.get("id") or "")

    # 1. The card already names a strategy. `fund_sleeve` cards do
    #    (`apply.strategy_id`), and the signal / discipline cards encode it in
    #    their id because they are generated per strategy.
    sid = ap.get("strategy_id") or rec.get("strategy_id")
    if not sid:
        for prefix in ("stratsig_", "stratrules_"):
            if rid.startswith(prefix):
                stub = rid[len(prefix):]
                # The id truncates the strategy id, so match by prefix rather
                # than equality -- `signal_card_id` slices to 12 characters.
                for cand in sleeve_names:
                    if cand.startswith(stub) or stub.startswith(cand[:12]):
                        sid = cand
                        break
                break
    if sid:
        if sid == core_id:
            return _core(core_id, core_name, core_pct)
        if sid in sleeve_names:
            return _sleeve(sid, sleeve_names.get(sid), sleeve_pcts.get(sid))
        # A strategy that is neither a live sleeve nor the core: name it anyway
        # rather than flattening it to "book". A card about a strategy you no
        # longer run is a real thing and should look like one.
        return _sleeve(sid, sid, None)

    # 2. Derive from the tickers it acts on.
    tks = _tickers_of(rec)
    owners = {ticker_owner[t] for t in tks if t in ticker_owner}
    if len(owners) == 1:
        s = next(iter(owners))
        # Mixed cards -- some sleeve tickers, some not -- are NOT a sleeve card.
        # A tax-harvest card naming four losers of which one is sleeved acts on
        # the book, and calling it a sleeve card would license it to argue with
        # that sleeve's target.
        if all(t in ticker_owner for t in tks):
            return _sleeve(s, sleeve_names.get(s), sleeve_pcts.get(s))
        return _BOOK
    if len(owners) > 1:
        return _BOOK
    if tks:
        # Named tickers, none of them sleeved -> the core's part of the book.
        return _core(core_id, core_name, core_pct)

    # 3. Names nothing. Asset-class and plan-level cards land here, correctly.
    return _BOOK


async def annotate(session, user, recs: list[dict]) -> list[dict]:
    """Stamp `scope` on every card. Returns the same list, mutated in place.

    Called once, after reconciliation, so a merged card is scoped from what it
    actually ended up containing rather than from whichever leg happened to be
    first.
    """
    from app.services import sleeve_service as sv
    from app.services import strategy_catalog as scat
    from app.services import strategies as static_cat

    try:
        sleeves = await sv.list_sleeves(session, user)
        core_id = await sv.core_strategy_id(session, user)
        core_pct = sv.remainder_pct(sleeves)
    except Exception:  # noqa: BLE001 -- a label is never worth breaking Today
        for r in recs:
            r.setdefault("scope", _BOOK)
        return recs

    from app.services.strategy_service import sleeve_targets

    ticker_owner: dict[str, str] = {}
    sleeve_names: dict[str, str] = {}
    sleeve_pcts: dict[str, float] = {}
    for s in sleeves:
        if s.is_core:
            continue
        sleeve_pcts[s.strategy_id] = float(s.sleeve_pct or 0.0)
        entry = scat.get(s.strategy_id)
        sleeve_names[s.strategy_id] = (entry or {}).get("name") or s.strategy_id
        for tk in (sleeve_targets(s.strategy_id, s.sleeve_pct) or {}):
            # First sleeve to claim a ticker owns it for labelling purposes.
            # Two sleeves sharing a ticker is legal and `all_sleeve_targets`
            # sums them; for a LABEL there is no honest way to name both, so
            # the card says the one it can defend.
            ticker_owner.setdefault(tk.upper(), s.strategy_id)

    core_name = None
    if core_id:
        for c in static_cat.CATALOG:
            if c["id"] == core_id:
                core_name = c.get("name")
                break

    for r in recs:
        r["scope"] = scope_for(r, ticker_owner=ticker_owner, sleeve_names=sleeve_names,
                               sleeve_pcts=sleeve_pcts, core_id=core_id,
                               core_name=core_name, core_pct=core_pct)
    return recs
