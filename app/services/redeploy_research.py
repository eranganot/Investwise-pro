"""PR 75 (plan item 3, "PR D") -- cash above the trigger gets RESEARCHED, not just placed.

Eran's item 3: above the cash trigger, research where the money should go,
without breaking the ₪150 hard minimum. `_redeploy_cash_recs` already sizes
every leg (gaps, sleeve targets, caps, floor -- unchanged here). What it never
did was ask whether the NAME it tops up is the best use of the money in that
class: it asked the screener only when the book held nothing in a class.

This module takes the sized legs and, for every CORE leg (never a sleeve --
a sleeve's instrument is its strategy's, so it is never swapped):

  * scores the holding it would top up and up to three screener names in the
    same class on ONE scale (the screener ranks the whole universe once, so a
    held ETF and a candidate stock are percentiles of the same set);
  * swaps the leg to a new name only when that name beats the holding by
    `NEW_NAME_MARGIN` points -- a versioned assumption, owner_reviewed=False;
  * never buys into a CONFIRMED downtrend (price < 50-day < 200-day average):
    such a candidate is listed and skipped, and a held name in one is replaced
    by the best candidate that is not, or held back in cash if there is none;
  * records every name it looked at and why it lost ("also considered").

Pure given its two lookups (`scores`, `trend`), so the rules are testable
without a network. Not financial advice: it ranks what the screener and the
price history say, with the screener's own disclosure that its weights are
not owner-reviewed.
"""
from __future__ import annotations

# Versioned assumptions (investing-discipline §4): the thresholds live here, with
# an id and a version the card pins, and stay owner_reviewed=False until Eran
# signs them off. Changing a value means bumping the version.
NEW_NAME_MARGIN = {"id": "redeploy.new_name_margin_pts", "version": 1, "value": 10.0,
                   "unit": "screener points (0-100)", "owner_reviewed": False,
                   "why": "a new name must beat topping up what you hold by this much, "
                          "so a near-tie never adds a position"}
TREND_RULE = {"id": "trend.confirmed_downtrend", "version": 1, "fast": 50, "slow": 200,
              "owner_reviewed": False,
              "why": "price below its 50-day average, which is below its 200-day average"}
MAX_CANDIDATES = 3
# At most this many candidates per class get a price-history fetch for their
# trend. With a class mostly in downtrend the scan would otherwise walk it all.
MAX_TREND_CHECKS = 8


def trend_from_closes(closes: list[float]) -> dict:
    """{"state": uptrend|downtrend|mixed|unmeasured, ...} from daily closes."""
    fast, slow = TREND_RULE["fast"], TREND_RULE["slow"]
    closes = [float(c) for c in (closes or []) if c is not None and float(c) > 0]
    if len(closes) < slow:
        return {"state": "unmeasured", "why": f"{len(closes)} closes, {slow} needed"}
    px = closes[-1]
    f = sum(closes[-fast:]) / fast
    s = sum(closes[-slow:]) / slow
    if px < f < s:
        state = "downtrend"
    elif px > f > s:
        state = "uptrend"
    else:
        state = "mixed"
    return {"state": state, "price": round(px, 4), "sma_fast": round(f, 4),
            "sma_slow": round(s, 4)}


def live_trend(ticker: str) -> dict:
    """Trend from the provider's last ~260 closes. "unavailable" (a provider
    problem) is kept apart from "no_data" (the ticker has none) -- the screener
    learned that distinction the hard way."""
    try:
        from app.agents.screener_agent import OpportunityAgent
        closes, status = OpportunityAgent()._history(ticker, 260)
    except Exception:  # noqa: BLE001
        return {"state": "unmeasured", "why": "price history unavailable"}
    if status != "ok":
        return {"state": "unmeasured", "why": f"price history {status}"}
    return trend_from_closes(closes)


def live_scores(objective: str | None, currency: str = "ILS") -> dict[str, dict]:
    """{TICKER: {"score", "reasons", "data_gaps", "asset_class"}} for the whole
    universe, ranked once per class so held names and candidates share a scale."""
    from app.agents.screener_agent import OpportunityAgent
    from app.services import objective_weights as ow
    from app.services.allocation_mix import classify

    ag = OpportunityAgent()
    out: dict[str, dict] = {}

    def _add(picks, cls_default, *, classify_it=False):
        # A commodity or bond pick keeps its SCREEN's class. Measured in review
        # of this PR: `classify("GLTR")` falls back to Equities, so a precious-
        # metals fund scored on trend-and-cost replaced a QQQ top-up scored on
        # fundamentals -- two scales, one comparison.
        for p in picks or []:
            cls = (classify(p.ticker, None) or cls_default) if classify_it else cls_default
            out[p.ticker.upper()] = {
                "score": float(p.score) if p.score is not None else None,
                "reasons": list(p.reasons or []), "data_gaps": list(getattr(p, "data_gaps", []) or []),
                "asset_class": cls, "name": p.name, "screen": cls_default}
    try:
        _add(ag.screen_equities(weights=ow.weights_for(objective), top_n=1000), "Equities",
             classify_it=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        _add(ag.screen_commodities(top_n=1000), "Commodities")
    except Exception:  # noqa: BLE001
        pass
    try:
        _add(ag.screen_bonds(top_n=1000, currency=currency), "Fixed Income")
    except Exception:  # noqa: BLE001
        pass
    return out


def _gaps(v: dict) -> set[str]:
    return {str(g).split(":")[0] for g in (v.get("data_gaps") or [])}


def _comparable(a: dict, b: dict) -> bool:
    """Scored by the same screen on the same factors. A score built from the
    factors that happened to be present is not comparable to one built from
    all of them (the screener's own rule since PR 27)."""
    return (a.get("screen", b.get("screen")) == b.get("screen", a.get("screen"))
            and _gaps(a) == _gaps(b))


def _gap_words(a: dict, b: dict) -> str:
    ga, gb = sorted(_gaps(a)), sorted(_gaps(b))
    return f"missing {', '.join(ga) or 'nothing'} vs {', '.join(gb) or 'nothing'}"


def research(legs: list[dict], unplaced: list[dict], *, sleeve_tickers: set[str],
             held_tickers: set[str], scores: dict[str, dict], trend,
             room_for, min_trade: float) -> dict:
    """Re-decide each core leg. Mutates `legs`/`unplaced` in place and returns
    the research record the card shows.

    `trend(ticker) -> dict`, `room_for(ticker) -> ₪ of room under its cap`.
    """
    margin = float(NEW_NAME_MARGIN["value"])
    sleeves = {t.upper() for t in sleeve_tickers}
    held = {t.upper() for t in held_tickers}
    taken = {x["ticker"].upper() for x in legs}
    records: list[dict] = []
    trends: dict[str, dict] = {}

    def _trend(tk):
        if tk not in trends:
            trends[tk] = trend(tk) or {"state": "unmeasured"}
        return trends[tk]

    keep: list[dict] = []
    for leg in legs:
        tk = leg["ticker"].upper()
        if tk in sleeves:
            keep.append(leg)                      # a sleeve's instrument is its strategy's
            continue
        cls = leg.get("asset_class")
        incumbent = None if leg.get("new_position") else tk
        pool = sorted(((t, v) for t, v in scores.items()
                       if v.get("asset_class") == cls and v.get("score") is not None
                       and t not in held and t not in sleeves
                       and (t not in taken or (incumbent is None and t == tk))
                       and not (incumbent is not None and t == tk)),
                      key=lambda kv: -kv[1]["score"])
        considered, skipped = [], []
        for n_checked, (t, v) in enumerate(pool):
            if n_checked >= MAX_TREND_CHECKS:
                break            # a bounded number of history fetches per class
            tr = _trend(t)
            if tr.get("state") == "downtrend":
                skipped.append({"ticker": t, "score": v["score"], "trend": tr,
                                "lost_because": "confirmed downtrend -- not bought into"})
                continue
            considered.append({"ticker": t, "score": v["score"], "trend": tr,
                               "data_gaps": v.get("data_gaps") or [], "screen": v.get("screen"),
                               "reasons": v.get("reasons") or [], "name": v.get("name")})
            if len(considered) >= MAX_CANDIDATES:
                break
        best = considered[0] if considered else None
        rec = {"asset_class": cls, "leg_ticker_before": tk, "incumbent": incumbent,
               "incumbent_score": (scores.get(incumbent) or {}).get("score") if incumbent else None,
               "incumbent_trend": _trend(incumbent) if incumbent else None,
               "margin_pts": margin, "decision": None, "chosen": tk,
               "also_considered": []}
        amt = float(leg["amount_ils"])

        def _swap(to: dict, why: str) -> bool:
            room = float(room_for(to["ticker"]))
            new_amt = round(min(amt, room), 2)
            if new_amt < min_trade:
                return False
            leg.update({"ticker": to["ticker"], "amount_ils": new_amt, "new_position": True,
                        "reason": why})
            rec.update(decision="swapped", chosen=to["ticker"])
            taken.add(to["ticker"])
            if new_amt < amt:
                unplaced.append({"asset_class": cls, "amount_ils": round(amt - new_amt, 2),
                                 "reason": f"{to['ticker']} has room for only ₪{new_amt:,.0f} under its cap"})
            return True

        if incumbent is None:
            # A class the book holds none of: the best-scored name not in a
            # confirmed downtrend, instead of the first idea on the list.
            swapped = bool(best and best["ticker"] != tk and _swap(
                best, f"you hold no {cls}; {best['ticker']} ranks best in {cls} "
                      f"({best['score']:.0f}/100) and is not in a confirmed downtrend"))
            if not swapped and _trend(tk).get("state") == "downtrend":
                # Review, CONFIRMED: when the better name had no room the old
                # branch fell through to "kept" and bought the downtrend anyway.
                unplaced.append({"asset_class": cls, "amount_ils": amt,
                                 "reason": f"{tk} is in a confirmed downtrend and no other "
                                           f"{cls} candidate could take it -- kept in cash"})
                rec["decision"] = "held_back"
                leg["_drop"] = True
            elif not swapped:
                rec["decision"] = "kept"
        else:
            inc_score = rec["incumbent_score"]
            inc_down = (rec["incumbent_trend"] or {}).get("state") == "downtrend"
            if inc_down:
                if best and _swap(best, f"{tk} is in a confirmed downtrend (price below its "
                                        f"50-day, 50-day below its 200-day); {best['ticker']} "
                                        f"ranks {best['score']:.0f}/100 in {cls} and is not"):
                    pass
                else:
                    unplaced.append({"asset_class": cls, "amount_ils": amt,
                                     "reason": f"{tk} is in a confirmed downtrend and no other "
                                               f"{cls} candidate qualified -- kept in cash"})
                    rec["decision"] = "held_back"
                    leg["_drop"] = True
            elif (best and inc_score is not None and best["score"] >= inc_score + margin
                  and _comparable(scores.get(incumbent) or {}, best)
                  and _swap(best, f"{best['ticker']} ranks {best['score']:.0f}/100 in {cls}, "
                                  f"{best['score'] - inc_score:.0f} points above {tk} "
                                  f"({inc_score:.0f}) -- past the {margin:g}-point margin "
                                  f"for adding a new name")):
                pass
            else:
                rec["decision"] = "kept"
        # Why each runner-up lost, in words.
        for c in considered:
            if c["ticker"] == rec["chosen"]:
                continue
            if rec["decision"] == "kept" and incumbent:
                if rec["incumbent_score"] is None:
                    why = f"{incumbent} has no screener score, so they cannot be compared -- kept the top-up"
                elif not _comparable(scores.get(incumbent) or {}, c):
                    why = (f"not comparable: {incumbent} and {c['ticker']} were scored on different "
                           f"factors ({_gap_words(scores.get(incumbent) or {}, c)}) -- kept the top-up")
                elif c["score"] < rec["incumbent_score"] + margin:
                    why = (f"{c['score']:.0f} vs {incumbent}'s {rec['incumbent_score']:.0f}: "
                           f"within the {margin:g}-point margin")
                else:
                    why = "had no room under its cap"
            else:
                why = f"ranked below {rec['chosen']}"
            rec["also_considered"].append({**{k: c[k] for k in ("ticker", "score")},
                                           "trend": c["trend"].get("state"), "lost_because": why,
                                           "data_gaps": c["data_gaps"]})
        rec["also_considered"] += [{"ticker": x["ticker"], "score": x["score"],
                                    "trend": "downtrend", "lost_because": x["lost_because"]}
                                   for x in skipped[:MAX_CANDIDATES]]
        records.append(rec)
        keep.append(leg)
    legs[:] = [x for x in keep if not x.pop("_drop", False)]
    return {"records": records,
            "assumptions": [f"{NEW_NAME_MARGIN['id']}@{NEW_NAME_MARGIN['version']}",
                            f"{TREND_RULE['id']}@{TREND_RULE['version']}"],
            "owner_reviewed": False}
