"""C13, C17, C28 (BACKLOG, 2026-10-24).

* C13 -- the funded-sleeve executor credits an unspent leg back to cash; nothing tested it.
* C17 -- a refused Accept answered HTTP 400 with only the short error ("not enough fundable
  money for this sleeve") while the refusal carried the reason (the sleeve that WOULD work)
  and the legs; the Today alert showed only the short error.
* C28 -- a rebalance band's note was the suggestion's sentence frozen at arm time. Live
  2026-10-24: QQQ "Your plan targets 7.2%" against a live target of 12.3%; VTI 26.6% vs
  19.7%; VXUS 8.9% vs 9.8%; SMH 3.9% vs 7.4% -- every band stale.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


# --------------------------------------------------------------------------- #
# C13
# --------------------------------------------------------------------------- #
class _Q:
    def __init__(self, price):
        self.price, self.currency, self.market = price, "ILS", "TASE"


@pytest.mark.asyncio
async def test_an_unpriceable_leg_is_credited_back_to_cash(own_session, monkeypatch):
    from app.services import strategy_service as ss
    from app.services.intake_service import get_cash, set_cash
    from app.services.feed_service import ensure_user
    monkeypatch.setattr(ss, "guarded_quote", lambda tk: _Q(10.0) if tk == "AAA" else None)
    async with own_session() as s:
        u = await ensure_user(s, "c13@example.com")
        await set_cash(s, u, 5000.0)
        await s.commit()
        out = await ss._execute_funded_sleeve(
            s, u, [{"ticker": "AAA", "buy_ils": 3000.0}, {"ticker": "NOPX", "buy_ils": 2000.0}],
            {"sells": [], "from_cash_ils": 5000.0})
        cash = await get_cash(s, u)
    assert [b["ticker"] for b in out["bought"]] == ["AAA"] and out["bought"][0]["amount_ils"] == pytest.approx(3000.0)
    assert out["skipped"] == [{"ticker": "NOPX", "reason": "no live price"}]
    assert out["unspent_ils"] == pytest.approx(2000.0), "the leg that could not be bought"
    assert cash == pytest.approx(2000.0), "5000 - 5000 taken + 2000 credited back: nothing vanishes"


# --------------------------------------------------------------------------- #
# C17
# --------------------------------------------------------------------------- #
def test_a_refusal_carries_its_reason_and_legs():
    from app.api.routes.recommendations import refusal_text
    txt = refusal_text({"ok": False, "error": "not enough fundable money for this sleeve",
                        "reason": "You asked for a 30% sleeve. The most this book can fund right now is 18%.",
                        "funding_summary": "Funded from ₪400 of cash.",
                        "buys": [{"ticker": "SOXL", "buy_ils": 1200.4}, {"ticker": "QQQ", "buy_ils": 300}]})
    assert txt == ("not enough fundable money for this sleeve. You asked for a 30% sleeve. The most this book can "
                   "fund right now is 18%. Funded from ₪400 of cash. It would have bought: SOXL ₪1,200, QQQ ₪300.")
    assert refusal_text({"ok": False}) == "Not applied."
    assert refusal_text({"ok": False, "error": "a rule moved - nothing was changed."}) == \
        "a rule moved - nothing was changed."


def test_the_accept_route_answers_400_with_the_whole_refusal(monkeypatch):
    import app.api.routes.recommendations as rr

    async def refuse(session, user, rec_id, **kw):
        return {"ok": False, "error": "funding this sleeve would move the book away from your plan",
                "reason": "Equities would go from 80% to 91% against a 75% target."}

    monkeypatch.setattr(rr, "apply_recommendation", refuse)
    with TestClient(m.app) as c:
        r = c.post("/api/v1/recommendations/rec_x/accept")
    assert r.status_code == 400
    assert r.json()["detail"] == ("funding this sleeve would move the book away from your plan. Equities would "
                                  "go from 80% to 91% against a 75% target.")


# --------------------------------------------------------------------------- #
# C28
# --------------------------------------------------------------------------- #
def test_a_band_note_reads_the_live_target_and_a_hand_written_note_is_kept():
    from app.services.rules_service import band_note_now, band_sentence
    stale = ("Your plan targets 7.2% of the book in QQQ. Trims above 10.2% and tops up below 4.2%, "
             "back to the target.")
    assert band_note_now(stale, "QQQ", 12.3, 3.0) == band_sentence("QQQ", 12.3, 3.0) == (
        "Your plan targets 12.3% of the book in QQQ. Trims above 15.3% and tops up below 9.3%, back to the target.")
    assert band_note_now(stale, "QQQ", None, 3.0).startswith("Your plan has no target for QQQ")
    assert band_note_now("my own reminder", "QQQ", 12.3, 3.0) == "my own reminder"
    assert band_sentence("SMH", 2.0, 3.0).endswith("tops up below 0%, back to the target.")


def test_the_rules_list_shows_the_live_band_sentence():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "BNDQ", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 80}]})
        stale = ("Your plan targets 1.5% of the book in BNDQ. Trims above 4.5% and tops up below 0%, "
                 "back to the target.")
        made = c.post("/api/v1/rules", json={"ticker": "BNDQ", "rule_type": "rebalance_band", "mode": "pct",
                                             "level": 3.0, "note": stale}).json()
        rid = str((made.get("rule") or made).get("id"))
        row = next(x for x in c.get("/api/v1/rules").json()["rules"] if str(x["id"]) == rid)
    from app.services.rules_service import band_sentence
    assert row["note"] != stale, "the sentence frozen at arm time is not shown"
    assert row["note"] in ("Your plan has no target for BNDQ right now, so this band does nothing until it does.",
                           band_sentence("BNDQ", round(float(row.get("target") or 0), 1), 3.0))
