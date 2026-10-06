"""PR 61. Eran, 2026-09-06: *"the swing should be a card that replace the TQQQ
with QQQ automatically."*

After the B2 swap his book held ~₪7,476 of TQQQ that no sleeve claims any more,
and a 33% QQQ sleeve holding nothing. PR 60 stopped the core card offering to
liquidate that position into VTI and cash. This is the other half: the card that
offers to finish the move, in one tap.

**The objection it has to answer**, still in the file and still right:

    "one tap taking a book from nothing to a fifth in a 3x fund is a different
     decision from topping up. That one routes to 'Fund this sleeve'."

**The first answer was wrong and the suite caught it.** It asked only whether the
funding money was UNCLAIMED by another sleeve. On the fixture in
`test_sleeves_mean_what_they_say.py::test_k...`, that rule would have taken a
book from nothing to 10% SOXL by selling an ordinary V holding — the exact
decision the guidance split exists to keep out of one tap. Being *unclaimed* is
not the same as being *earmarked*.

**The rule that survives is the RELATIONSHIP, not the size and not the owner.**
Actionable only when every position sold is a leveraged instrument whose base is
this sleeve's ticker — `strategy_catalog.steps_back_to`, read off the specs.
TQQQ steps back to QQQ, so selling it to hold QQQ is a downgrade of an exposure
the book already has, under a rule that is no longer running. V does not step
back to SOXL. Neither does QQQ step back to TQQQ, which is why the helper is
asymmetric.

Priced through the same `load_basket` the button runs, in dry-run, so the card
cannot show one plan while Accept executes another — the `_core_plan` discipline,
which exists because a card once said "sell 4,324" and Accept sold 12,139.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import recommendations, strategy_catalog as cat  # noqa: E402
from tests._cards import all_cards  # noqa: E402

QQQ_HOLD = "btm_qqq_hold"       # weights {QQQ}, no base -- the post-swap sleeve
SOXL_TREND = "btm_trend_soxl"   # weights {SOXL}, base {SMH}


def _pos(ticker, price=100.0, qty=100.0, basis=90.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _cold_card(c):
    recs = all_cards(c.get("/api/v1/recommendations").json())
    cold = [r for r in recs if (r.get("meta") or {}).get("cold_start")]
    return cold[0] if cold else None


# --------------------------------------------------------------------------- #
# 1. the relationship the rule is built on
# --------------------------------------------------------------------------- #
def test_the_catalogue_knows_what_steps_back_to_what():
    """Read off the specs, not off the user's history.

    Three rules hold TQQQ over a QQQ core, so "TQQQ steps back to QQQ" is a fact
    about the catalogue, available whether or not this particular user ever ran
    one of them.
    """
    assert cat.steps_back_to("TQQQ", "QQQ")
    assert cat.steps_back_to("tqqq", "qqq"), "the check is case-sensitive"
    assert cat.steps_back_to("SOXL", "SMH")


def test_it_is_asymmetric_and_that_is_the_whole_point():
    """A downgrade is not a swap you can run in either direction.

    Funding a TQQQ sleeve by selling QQQ is precisely the "nothing to a fifth in
    a 3x fund" move the guidance split exists to keep out of one tap.
    """
    assert not cat.steps_back_to("QQQ", "TQQQ")
    assert not cat.steps_back_to("V", "SOXL"), "an ordinary holding is not an overlay"
    assert not cat.steps_back_to("TQQQ", "SMH"), "wrong rule's base"
    assert not cat.steps_back_to("QQQ", "QQQ")
    assert not cat.steps_back_to("", "QQQ")


# --------------------------------------------------------------------------- #
# 2. the card, on Eran's book
# --------------------------------------------------------------------------- #
def test_the_post_swap_card_offers_to_finish_the_move():
    """TQQQ in the book, a QQQ sleeve holding nothing, nothing else claiming it."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", qty=75.0), _pos("VTI", qty=30.0), _pos("BND", qty=23.0)]})
        c.post(f"/api/v1/strategies/{QQQ_HOLD}/apply?sleeve_pct=33")

        card = _cold_card(c)
        assert card, "no cold-start card for a sleeve that holds nothing"
        assert (card.get("apply") or {}).get("kind") == "fund_sleeve", (
            f"the card will not finish the move: {card.get('apply')}")
        assert (card["apply"].get("strategy_id")) == QQQ_HOLD
        assert card["meta"].get("steps_back") is True
        assert "TQQQ" in card["meta"].get("from", []), card["meta"]
        # It says what it is, in the user's terms.
        assert "TQQQ" in card["title"] and "QQQ" in card["title"]


def test_accept_actually_performs_the_swap():
    """The claim on the card is the only thing worth testing about the card.

    `fund_sleeve` routes through `load_basket(mode="fund")` -- the same function
    the dry-run preview priced -- so there is one sizing and no second opinion.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", qty=75.0), _pos("VTI", qty=30.0), _pos("BND", qty=23.0)]})
        c.post(f"/api/v1/strategies/{QQQ_HOLD}/apply?sleeve_pct=33")
        card = _cold_card(c)
        assert (card.get("apply") or {}).get("kind") == "fund_sleeve"

        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("applied") == "fund_sleeve", body
        assert [x["ticker"] for x in body["funding"]["sells"]] == ["TQQQ"], body["funding"]

        s = c.get("/api/v1/plan/sleeves").json()
        assert s["allocated_held_pct"] == pytest.approx(0.33, abs=0.02), s
        # And the card is gone, because the sleeve is no longer cold.
        assert _cold_card(c) is None, "the sleeve is funded; the card must stop firing"


def test_the_headline_is_the_money_that_actually_moves():
    """`est_amount` feeds mute, re-fire and merge, so it is not cosmetic.

    Sized from the preview's own legs rather than from the drift gap, which is
    the discipline `_core_plan` exists to enforce.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("TQQQ", qty=75.0), _pos("VTI", qty=30.0), _pos("BND", qty=23.0)]})
        c.post(f"/api/v1/strategies/{QQQ_HOLD}/apply?sleeve_pct=33")
        card = _cold_card(c)
        assert (card.get("apply") or {}).get("kind") == "fund_sleeve"
        est = float(card["est_amount"])

        c.post(f"/api/v1/recommendations/{card['id']}/accept")
        bought = float(c.get("/api/v1/plan/sleeves").json()["allocated_held_ils"])

        assert bought == pytest.approx(est, rel=0.01), (
            f"the card said {est:,.0f} and Accept moved {bought:,.0f}")
        # Not the drift gap, which is what the card used to quote: the gap is
        # measured off a NAV that still counts the position about to be sold.
        assert est > 0.0


# --------------------------------------------------------------------------- #
# 3. what it still refuses -- the half the first attempt got wrong
# --------------------------------------------------------------------------- #
def test_an_ordinary_holding_does_not_buy_a_leveraged_sleeve_in_one_tap():
    """The SOXL fixture, restated here because it is the discriminating case.

    Selling V to start a 10% SOXL sleeve is not finishing anything. Nothing in
    the catalogue says V steps back to SOXL, so the card stays guidance -- and
    now carries the funding engine's own reason rather than a pointer at a tab.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V"), _pos("SCHD"), _pos("MSFT")]})
        c.post(f"/api/v1/strategies/{SOXL_TREND}/apply?sleeve_pct=10")

        card = _cold_card(c)
        assert card, "no cold-start card for a sleeve that holds nothing"
        assert (card.get("apply") or {}).get("kind") in (None, "none"), (
            f"an ordinary holding was allowed to fund a 3x sleeve in one tap: "
            f"{card.get('apply')}")
        assert card.get("persistent") is True


def test_the_guidance_branch_says_why_rather_than_where():
    """It used to point at the Plan tab. Now it carries the engine's reason.

    `_fund_sleeve`'s refusal is written to be printable -- "You asked for a 33%
    sleeve (₪7,238). The most this book can fund right now is 12% ..." -- and a
    card that says which number to change beats one that says which tab to open.
    """
    with TestClient(m.app) as c:
        # Every holding belongs to the OTHER sleeve, so `_fund_sleeve`'s
        # piggy-bank exclusion leaves nothing to sell. It abstains and explains
        # rather than half-installing a position nobody chose.
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("MTUM"), _pos("QUAL"), _pos("AVUV")]})
        c.post("/api/v1/strategies/btm_factor_stack/apply?sleeve_pct=50")
        c.post(f"/api/v1/strategies/{SOXL_TREND}/apply?sleeve_pct=40")

        card = _cold_card(c)
        assert card and (card.get("apply") or {}).get("kind") in (None, "none")
        assert "Lower the sleeve to about" in card["why"], card["why"]
        assert "short, after spending" in card["why"], (
            "the card is not carrying the funding engine's refusal reason")


def test_fund_sleeve_is_an_actionable_kind():
    """A card carrying an apply kind outside this set renders the green "the app
    can do this for you" tag and then falls through every branch -- the failure
    the actionable/guidance split was introduced to kill.
    """
    assert "fund_sleeve" in recommendations._ACTIONABLE_KINDS


def test_the_preview_never_writes():
    """It runs on every Today load, inside a savepoint, in dry-run.

    `_agent_tx` exists because a defensive `except` that carries on with the same
    session passes on SQLite and raises InFailedSQLTransactionError on Postgres,
    from a query long after the agent that actually broke.
    """
    import inspect

    src = inspect.getsource(recommendations)
    i = src.index("_cold = not _held and _gap > 0")
    body = src[i:i + 6000]
    assert "async with _agent_tx(session):" in body
    assert 'mode="fund", dry_run=True' in body

    from app.services import strategy_service
    fs = inspect.getsource(strategy_service._fund_sleeve)
    a = fs.index("if dry_run:")
    b = fs.index("_execute_funded_sleeve", a)
    assert "return" in fs[a:b], (
        "dry_run no longer returns before the executor; the preview would trade")


# --------------------------------------------------------------------------- #
# PR 61.1 -- the card was right and the FUNDING PLAN was wrong
#
# Eran, looking at Today after PR 61 deployed: "the second card is still guidance
# only and not actionable!!"
#
# `/health` served 446ceaa, so PR 61 was live. Asking production for the plan
# behind that card showed why it refused:
#
#     SELL VTI   2,925.31  | Equities is the most overweight class (81% vs 80%)
#                            and VTI is among its least-taxed positions
#     SELL TQQQ  4,145.14  | ... and TQQQ is among its least-taxed positions
#     from_cash    179.88
#
# VTI does not step back to QQQ, so the card correctly declined -- to do something
# that would have been wrong anyway: selling core money doing its job to start a
# sleeve whose funding was sitting in the leveraged position the sleeve replaces.
#
# MY REPRODUCTION WAS THE FLAW. In the synthetic book above TQQQ is 59% of the
# book and trips the 25% single-name cap, so the ranking picked it alone. On the
# real book it is ~34% and the ranking falls through to class-overweight plus
# tax, where VTI ranks cheaper. The card's rule was never tested against a book
# where the funder had a choice.
# --------------------------------------------------------------------------- #
LIVE = [   # the live book's shape: Equities marginally over, VTI the least-taxed
    ("SOXL", 7422.0, 0.20, "Equities"),
    ("TQQQ", 7476.0, 0.30, "Equities"),
    ("VTI",  3420.0, 0.05, "Equities"),
    ("BND",  2337.0, 0.02, "Fixed Income"),
]


def _live_book(c):
    positions = []
    for tk, ils, gain, cls in LIVE:
        px = ils / 100.0
        positions.append({"ticker": tk, "market": "TASE", "asset_class": cls, "depth": 3,
                          "spot_price": px, "listing_price": px, "quantity": 100.0,
                          "cost_basis": px / (1.0 + gain),
                          "expected_return_pct": 8, "volatility_pct": 20})
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": positions})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 619.0})
    # THE RISK TOLERANCE IS PART OF THE REPRODUCTION, not decoration. Live: High,
    # whose `concentration_cap` is 0.40 (`plan_service.RISK_CAPS`). TQQQ at ~35%
    # of the book is UNDER that, so `name_over` is zero and the ranking falls
    # through to class-overweight plus tax -- which is where VTI, carrying the
    # smaller gain, outranks it. Under the Medium default (0.25) TQQQ trips its
    # own cap and is picked first regardless, which is exactly why the first
    # version of this file could not see the bug.
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "cash_target_pct": 7.5})
    c.post(f"/api/v1/strategies/{SOXL_TREND}/apply?sleeve_pct=33.91")
    c.post(f"/api/v1/strategies/{QQQ_HOLD}/apply?sleeve_pct=33.09")


def test_the_funder_sells_the_position_the_move_converts():
    """TQQQ first, and VTI not at all.

    The sleeve needs about 7,000 and TQQQ alone holds 7,476 of it. A plan that
    reaches past it for core VTI is not cheaper, it is a different trade.
    """
    with TestClient(m.app) as c:
        _live_book(c)
        prev = c.post(f"/api/v1/strategies/{QQQ_HOLD}/load-basket",
                      json={"mode": "fund", "dry_run": True}).json()
        assert prev.get("ok"), prev
        sells = {s["ticker"]: s["value_ils"] for s in prev["funding"]["sells"]}

        assert "TQQQ" in sells, sells
        assert "VTI" not in sells, (
            f"the funder reached past TQQQ for core VTI: {sells}")
        assert "BND" not in sells, sells
        assert "SOXL" not in sells, "the other sleeve is not a piggy bank"
        # And it says why it picked that one.
        leg = [s for s in prev["funding"]["sells"] if s["ticker"] == "TQQQ"][0]
        assert "converts" in leg["reason"], leg["reason"]


def test_and_so_the_card_can_act_on_the_live_book():
    """The two halves are one rule: `steps_back_to` decides both.

    The card may act only on a plan whose every leg steps back to the sleeve, and
    the funder now builds exactly that plan when the book allows it. Before this,
    the card was offered a plan it had to refuse.
    """
    with TestClient(m.app) as c:
        _live_book(c)
        card = _cold_card(c)
        assert card, "no cold-start card for the unfunded QQQ sleeve"
        assert (card.get("apply") or {}).get("kind") == "fund_sleeve", (
            f"still guidance on the live book's shape: {card.get('apply')}")
        assert card["meta"]["from"] == ["TQQQ"], card["meta"]
