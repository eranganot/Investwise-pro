"""PR 70 -- one priority score per card: money at stake, time, your own rules.

Eran, 2026-09-22: "I want to re-calculate and re-think on the priority of the
cards -- they are not representing and not giving the urgency when it matters."
And on "Consider USO", fixed at Minor: "it depends on the performance of the
holding and the potential, also the alignment to the plan".

Before this, every generator stamped its own severity, mostly a constant: every
buy idea LOW, every take-profit-approaching card MEDIUM, the goal card CRITICAL
at any distance. Nothing compared a 30 shekel card with a 1,000 shekel one.

THE SCORE (0-100), agreed in the proposal doc:

  money   0-40  the card's shekels, full marks at 5% of the book. Buy ideas count
                half: that money is being put to work, not at risk.
  time    0-35  rule/stop already hit 35 . within 2% of a trigger 20 . within 5%
                10 . within 10% 5 . stale price / tax deadline 20 . goal 10
                (waiting raises the monthly amount needed).
  rules   0-25  your rule fired 25 . at its level, not yet fired 15 . approaching 5.
  quality 0-45  buy ideas only: recent performance (0-10, full at +20% over ~6
                months) + the screen's score (0-15) + fit with your plan, the share
                of an asset-class gap the buy closes (0-20; 0 when the class is
                already at or over target).

LABELS: 70+ Important . 50-69 Worth doing . 30-49 Nice to have . below 30 Minor.
A stop that has fired, or a stop/trailing stop within 2%, is always Important.

ORDER: by score; within 5 points a card from your own rule, or one that
protects the book, goes first (Eran: "if the score is 52 vs 51 maybe it should
be the same priority").

The label is written back into `severity`, so every existing reader -- the pill
on the card, the push filter, the hidden-cards list, the mute marks -- follows
the new scale without a second vocabulary. Pure: dicts in, dicts out.
Not financial advice.
"""
from __future__ import annotations

from datetime import date

MONEY_FULL_FRAC = 0.05
TIE_POINTS = 5.0
_SEV_FOR = (("CRITICAL", 70.0), ("HIGH", 50.0), ("MEDIUM", 30.0), ("LOW", 0.0))
LABEL = {"CRITICAL": "Important", "HIGH": "Worth doing", "MEDIUM": "Nice to have", "LOW": "Minor"}
_PROTECTIVE = ("stop_loss", "trailing_stop")


def severity_for(score: float) -> str:
    for sev, floor in _SEV_FOR:
        if score >= floor:
            return sev
    return "LOW"


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money_points(amount: float | None, nav: float) -> float:
    if not amount or amount <= 0 or nav <= 0:
        return 0.0
    return min(40.0, 40.0 * float(amount) / (MONEY_FULL_FRAC * nav))


def distance_points(distance_pct: float | None) -> float:
    """How close a price is to one of your levels, as a share of price."""
    if distance_pct is None:
        return 0.0
    d = abs(float(distance_pct))
    return 35.0 if d <= 0 else 20.0 if d <= 2 else 10.0 if d <= 5 else 5.0 if d <= 10 else 0.0


def quality_points(idea: dict) -> float:
    """Recent performance + potential + fit with the plan, for a buy idea."""
    ret = _num(idea.get("return_6m_pct"))
    perf = min(10.0, max(0.0, 10.0 * ret / 20.0)) if ret is not None else 0.0
    sc = _num(idea.get("screen_score"))
    pot = min(15.0, max(0.0, 15.0 * sc / 100.0)) if sc is not None else 0.0
    frm, to, tgt = (_num(idea.get(k)) for k in ("class_from_pct", "class_to_pct", "class_target_pct"))
    fit = 0.0
    if None not in (frm, to, tgt) and tgt > frm:
        fit = 20.0 * max(0.0, min(1.0, (to - frm) / (tgt - frm)))
    return perf + pot + fit


def score_card(card: dict, *, nav: float, weights: dict[str, float] | None = None,
               max_weight_rules: dict[str, float] | None = None,
               today: date | None = None) -> dict:
    """The score, its parts and the reason for each. Does not mutate `card`."""
    weights = weights or {}
    max_weight_rules = max_weight_rules or {}
    today = today or date.today()
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    ap = card.get("apply") if isinstance(card.get("apply"), dict) else {}
    cid = str(card.get("id") or "")
    dim = card.get("dimension")
    amount = _num(card.get("est_amount"))
    money, time_, rules, quality = money_points(amount, nav), 0.0, 0.0, None
    why, protective, own_rule = [], False, False

    if cid.startswith("rule_") or dim == "rule":
        time_, rules, own_rule = 35.0, 25.0, True
        protective = meta.get("rule_type") in _PROTECTIVE
        why.append("your rule fired")
    elif meta.get("rule_type") and meta.get("distance_pct") is not None:
        # One of your levels is close (take-profit, trailing stop).
        time_ = min(20.0, distance_points(meta.get("distance_pct")))
        rules, own_rule = 5.0, True
        protective = meta.get("rule_type") in _PROTECTIVE
        why.append(f"{float(meta['distance_pct']):.1f}% from your {meta['rule_type'].replace('_', ' ')}")
    elif ap.get("kind") == "set_standing_order" or dim == "goal":
        if ap.get("kind") == "set_standing_order" and amount:
            money = money_points(amount * 12.0, nav)
        time_ = 10.0
        why.append("waiting raises the monthly amount needed")
    elif dim == "data":
        time_ = 20.0
        why.append("a stale price")
    elif dim == "tax" and today.month == 12:
        time_ = 20.0
        why.append("the tax year ends this month")
    elif dim == "diversification" and meta.get("ticker"):
        tk = str(meta["ticker"]).upper()
        w = weights.get(tk, 0.0) * 100.0
        lvl = max_weight_rules.get(tk)
        if lvl is not None and w >= lvl - 1.0:
            time_, rules, own_rule = 20.0, 15.0, True
            why.append(f"{tk} is at your {lvl:g}% max-weight rule")
        else:
            time_ = 10.0
            why.append(f"{tk} is over your plan's limit")

    if isinstance(meta.get("idea"), dict):
        money = money / 2.0
        quality = quality_points(meta["idea"])
        why.append("idea quality")

    score = money + time_ + rules + (quality or 0.0)
    sev = severity_for(score)
    if protective and (time_ >= 35.0 or (meta.get("distance_pct") is not None
                                          and abs(float(meta["distance_pct"])) <= 2.0)):
        sev = "CRITICAL"
        why.append("a stop is at or near its level")
    return {"score": round(score, 1), "money": round(money, 1), "time": round(time_, 1),
            "rules": round(rules, 1), "quality": (None if quality is None else round(quality, 1)),
            "severity": sev, "label": LABEL[sev], "own_rule": own_rule, "protective": protective,
            "why": why}


def apply_priority(recs: list[dict], **ctx) -> list[dict]:
    """Stamp `priority` and the matching `severity` on every card, in place."""
    for r in recs:
        p = score_card(r, **ctx)
        r["priority"] = p
        r["severity"] = p["severity"]
    return recs


def fold_alternatives(recs: list[dict]) -> list[dict]:
    """Two buy ideas filling the same asset-class gap are one decision.

    Live, 2026-09-22: "Consider USO" and "Consider UGA" both moved Commodities
    from 0% to 5% against a 10% target, and would have sat side by side at 52
    and 50. The better-scoring idea keeps the card; the others ride on it as
    `alternatives`, so nothing is hidden and nothing is offered twice.
    """
    best: dict[str, dict] = {}
    drop: set[int] = set()
    for r in recs:
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        cls = meta.get("asset_class")
        if not isinstance(meta.get("idea"), dict) or not cls:
            continue
        cur = best.get(cls)
        score = float((r.get("priority") or {}).get("score") or 0.0)
        if cur is None:
            best[cls] = r
            continue
        keep, lose = ((r, cur) if score > float((cur.get("priority") or {}).get("score") or 0.0)
                      else (cur, r))
        keep.setdefault("alternatives", []).extend(lose.pop("alternatives", []))
        keep["alternatives"].append({
            "ticker": (lose.get("meta") or {}).get("ticker"), "title": lose.get("title"),
            "score": (lose.get("priority") or {}).get("score"),
            "est_amount": lose.get("est_amount"), "id": lose.get("id")})
        best[cls] = keep
        drop.add(id(lose))
    return [r for r in recs if id(r) not in drop]


def sort_key(card: dict) -> tuple:
    p = card.get("priority") or {}
    lift = TIE_POINTS + 0.01 if (p.get("own_rule") or p.get("protective")) else 0.0
    forced = 1 if card.get("severity") == "CRITICAL" else 0
    return (-forced, -(float(p.get("score") or 0.0) + lift))
