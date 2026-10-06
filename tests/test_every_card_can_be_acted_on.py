"""PR 102: a card asks you to do something, and Accept does it.

Knowledge without an ask is a heads-up: one line, no "Mark as done", gone for
good once dismissed. This file is the permanent guard: it walks the REAL card
set for every adversarial book and fails on any card whose Accept has no
handler, so a new card type cannot come back as advice-with-a-button.
"""
import inspect
import re

from fastapi.testclient import TestClient

from app.main import app
from app.services import headsup
from app.services import recommendations as rec
from tests.test_card_claims import BOOKS, _seed


def _handled_kinds() -> set[str]:
    """Every kind `apply_recommendation` dispatches, read from its source."""
    src = inspect.getsource(rec.apply_recommendation)
    kinds = set(re.findall(r'kind == "([a-z_]+)"', src))
    for group in re.findall(r"kind in \(([^)]*)\)", src):
        kinds |= set(re.findall(r'"([a-z_]+)"', group))
    return kinds


def test_every_card_on_today_has_an_accept_that_does_something():
    handled = _handled_kinds()
    for name, book in sorted(BOOKS.items()):
        with TestClient(app) as c:
            _seed(c, book)
            r = c.get("/api/v1/recommendations").json()
        cards = (r.get("recommendations") or []) + (r.get("more_recommendations") or [])
        for card in cards:
            kind = (card.get("apply") or {}).get("kind") or "none"
            assert rec._is_actionable(card), f"[{name}] advice with no Accept: {card['title']}"
            assert kind in handled, f"[{name}] '{card['title']}' has kind {kind!r} that Accept does not handle"
        for h in r.get("headsups") or []:
            assert set(h) >= {"id", "title", "line"}
            if h.get("apply"):
                assert h["apply"]["kind"] in handled and h.get("act", {}).get("label")


def test_the_split():
    nav = 24_000.0
    trade = {"id": "a", "title": "Trim X", "apply": {"kind": "trim", "ticker": "X", "shares": 1}, "est_amount": 500}
    tiny = {**trade, "id": "b", "est_amount": 60}
    rule_tiny = {**tiny, "id": "rule_abc"}
    advice = {"id": "c", "title": "Hold more liquid assets", "action": "First sentence. Second one.",
              "apply": {"kind": "none"}}
    tax_small = {"id": "d", "dimension": "tax", "apply": {"kind": "sell_losers"}, "est_amount": 5}
    tax_ok = {**tax_small, "id": "e", "est_amount": 80}
    idle = {"id": "f", "apply": {"kind": "none"}, "meta": {"headsup_kind": "idle_cash"}}
    cards, heads, dropped = headsup.split([trade, tiny, rule_tiny, advice, tax_small, tax_ok, idle],
                                          nav=nav, actionable=rec._is_actionable)
    assert [c["id"] for c in cards] == ["a", "rule_abc", "e"]
    assert [h["id"] for h in heads] == ["b", "c"]
    assert heads[1]["line"] == "First sentence."
    assert {d["id"] for d in dropped} == {"d", "f"}
    assert headsup.min_trade_ils(24_000) == 120.0 and headsup.min_trade_ils(5_000) == 100.0


def test_a_near_cap_heads_up_offers_to_raise_the_cap_only_near_the_target():
    from tests.test_the_app_says_when_to_take_profits import _P
    qqq = _P("QQQ", 1.0, 100.0, 90.0)
    snap = {"nav": 520.0, "exposure_ticker": {"QQQ": 0.19}}
    cap = {"id": "r1", "ticker": "QQQ", "rule_type": "max_weight", "level": 20.0, "active": True}
    card = next(c for c in rec._profit_recs([qqq], snap, [cap], {"QQQ": 0.185}) if "nearly at" in c["title"])
    h = headsup.to_headsup(card)
    assert h["apply"] == {"kind": "edit_rule", "target_rule_id": "r1", "level": 21.5}
    assert h["act"]["label"].startswith("Raise the cap to 21.5%")
    # a cap far from the target is a choice, not a mismatch: no act
    far = next(c for c in rec._profit_recs([qqq], snap, [cap], {"QQQ": 0.10}) if "nearly at" in c["title"])
    assert "apply" not in headsup.to_headsup(far)


def test_over_target_the_gain_card_sells_the_excess():
    from tests.test_the_app_says_when_to_take_profits import _P
    soxl = _P("SOXL", 10.0, 200.0, 100.0)
    card = next(c for c in rec._profit_recs([soxl], {"nav": 4000.0, "exposure_ticker": {"SOXL": 0.5}}, [],
                                            {"SOXL": 0.40}) if c["title"].startswith("Trim SOXL"))
    assert card["apply"] == {"kind": "trim", "ticker": "SOXL", "shares": 2.0}
    assert card["est_amount"] == 400.0


def test_a_dismissed_heads_up_never_comes_back():
    with TestClient(app) as c:
        _seed(c, BOOKS[sorted(BOOKS)[0]])
        hs = c.get("/api/v1/recommendations").json().get("headsups") or []
        if not hs:
            return
        hid = hs[0]["id"]
        assert c.post(f"/api/v1/recommendations/headsups/{hid}/dismiss").json()["ok"]
        again = c.get("/api/v1/recommendations").json()
        assert hid not in {h["id"] for h in again.get("headsups") or []}
        assert hid not in {x["id"] for x in (again.get("recommendations") or [])}
