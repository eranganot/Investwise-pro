"""2026-10-22 -- "when I click Ignore on the other card nothing happens".

Read live from production. The "Safer, lower chance: Move 30% of Volatility-
Targeted Leverage into Managed Futures" card (rec_0164ed), a sleeve-move card:

    POST /api/v1/recommendations/rec_0164ed/dismiss -> 200
    GET  /api/v1/recommendations                    -> the card is still there,
                                                       not in hidden_cards

The card declares `persistent: True`. That flag exists so that "Done" cannot bury
a standing condition (PR 86's cold-start sleeve card). The same flag was also
subtracted from the IGNORED set, so every Ignore on such a card wrote its mark and
was then thrown away on the next read. Ignore is a statement about the user's
attention; no property of the card may overrule it.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def _settle(dismissed, completed, **kw):
    # Lazy import, so the file collects against the previous commit and the tests
    # fail there for the right reason instead of never running.
    from app.services.recommendations import settle_mutes
    base = dict(refired=set(), persistent=set(), stale_high=set(), moved=set())
    base.update(kw)
    return settle_mutes(dismissed, completed, **base)


def test_a_persistent_card_stays_ignored():
    dis, done = _settle({"rec_0164ed"}, set(), persistent={"rec_0164ed"})
    assert "rec_0164ed" in dis, "Ignore on a persistent card was thrown away"


def test_a_persistent_card_still_cannot_be_buried_by_done():
    """The protection `persistent` was written for is unchanged."""
    dis, done = _settle(set(), {"rec_cold"}, persistent={"rec_cold"})
    assert "rec_cold" not in done


def test_a_card_that_moved_on_still_comes_back_after_an_ignore():
    dis, _ = _settle({"x"}, set(), moved={"x"})
    assert "x" not in dis


def test_a_refired_rule_still_comes_back_after_an_ignore():
    dis, _ = _settle({"x"}, set(), refired={"x"})
    assert "x" not in dis


def test_unrelated_marks_are_untouched():
    dis, done = _settle({"a", "b"}, {"c"}, persistent={"b"})
    assert dis == {"a", "b"} and done == {"c"}


def test_the_inputs_are_not_mutated():
    d, c = {"a"}, {"b"}
    _settle(d, c, persistent={"a", "b"})
    assert d == {"a"} and c == {"b"}


# --------------------------------------------------------------------------- #
# through the API: the cold-start sleeve card is persistent and actionable-free
# --------------------------------------------------------------------------- #
def _pos(ticker, price=100.0, qty=100.0, basis=90.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def test_ignoring_a_persistent_card_hides_it_through_the_api():
    """Route -> store -> filter -> payload, on a real persistent card."""
    from fastapi.testclient import TestClient

    import app.main as m
    from tests._cards import all_cards

    with TestClient(m.app) as c:
        try:
            c.post("/api/v1/recommendations/restore")
            c.post("/api/v1/recommendations/restore-completed")
            c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
                _pos("V"), _pos("SCHD"), _pos("MSFT")]})
            c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=10")

            cards = all_cards(c.get("/api/v1/recommendations").json())
            persistent = [r for r in cards if r.get("persistent")]
            assert persistent, "this book produced no persistent card, so the test would prove nothing"
            victim = persistent[0]

            r = c.post(f"/api/v1/recommendations/{victim['id']}/dismiss", json={})
            assert r.status_code == 200, r.text

            after = c.get("/api/v1/recommendations").json()
            still = [x for x in all_cards(after) if x["id"] == victim["id"]]
            assert not still, f"'{victim['title']}' was ignored and is still on Today"
            hidden = {h["id"]: h["how"] for h in after.get("hidden_cards", [])}
            assert hidden.get(victim["id"]) == "ignored", "the ignored card is not listed as hidden"
        finally:
            c.post("/api/v1/recommendations/restore")
            c.post("/api/v1/recommendations/restore-completed")
