"""What your money did, against what you paid in.

The headline was gain-vs-cost-basis, and that was correct while the book carried
7,388 of positions an FX bug had minted: NAV was wrong, so NAV-minus-deposits
read +42.48% on a book that had gained 4%. The metric was never the problem --
the NAV was.

On a repaired book the owner's question is "I put in 20,000, what is it worth
now", and the difference between the two numbers is not a discrepancy:

    NAV 21,339 - deposits 20,000 = 1,339   total gain      (+6.69%)
    NAV 21,339 - basis    20,350 =   989   unrealised
    basis 20,350 - deposits 20,000 =  350  REALISED, reinvested

Sell a winner, put the proceeds into something else, and the new lot is stamped
at a price that already contained the gain. Basis rises with no new money. Every
brokerage account does this, gain-vs-contributions counts it correctly, and the
350 needs no warning, no baseline and no explanation.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _client():
    logging.disable(logging.CRITICAL)
    return TestClient(m.app)


def _seed(c, contributed=20000):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 400.00, "listing_price": 400.00, "quantity": 10.0,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 36.00, "listing_price": 36.00, "quantity": 60.0,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 621.79})
    if contributed is not None:
        c.post("/api/v1/portfolio/contributions",
               json={"amount_ils": contributed, "kind": "deposit", "note": "opening"})


def test_a_the_payload_carries_gain_against_deposits():
    try:
        with _client() as c:
            _seed(c)
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    for f in ("gain_vs_contributions_ils", "gain_vs_contributions_pct",
              "gain_vs_basis_ils", "cost_basis_ils", "contributed_ils"):
        assert f in p, f"missing {f}"

    # The arithmetic has to be exactly what it says on the card.
    assert p["gain_vs_contributions_ils"] == pytest.approx(
        p["nav_ils"] - p["contributed_ils"], abs=0.02)
    assert p["gain_vs_contributions_pct"] == pytest.approx(
        p["gain_vs_contributions_ils"] / p["contributed_ils"] * 100, abs=0.02)


def test_b_the_two_gains_differ_by_exactly_the_realised_part():
    """The identity that makes the 350 explicable rather than unexplained.

        (NAV - deposits) - (NAV - basis) == basis - deposits

    Trivial algebra, and it is the whole argument: the gap between the two
    headline numbers IS realised gain. Nothing is missing, so nothing needs a
    baseline to absorb it.
    """
    try:
        with _client() as c:
            _seed(c)
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    realised = p["cost_basis_ils"] - p["contributed_ils"]
    assert (p["gain_vs_contributions_ils"] - p["gain_vs_basis_ils"]) == pytest.approx(
        realised, abs=0.02)


def test_c_gain_against_deposits_is_the_larger_number_on_a_rebalanced_book():
    """Sanity, and the reason this is the number he asked for.

    A book whose basis has been re-stamped upward by reinvested gains shows a
    SMALLER gain against basis than against deposits -- the realised part has
    already been folded into basis, so measuring against basis quietly excludes
    money you genuinely made.
    """
    try:
        with _client() as c:
            _seed(c)
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert p["cost_basis_ils"] > p["contributed_ils"], "this test needs a re-stamped book"
    assert p["gain_vs_contributions_ils"] > p["gain_vs_basis_ils"]


def test_d_no_deposits_recorded_falls_back_to_basis():
    """Never show a confident percentage against a number you do not have.

    With no contributions ledger there is no honest answer to "what did my money
    do", so the card says so and reverts to cost basis rather than dividing by
    zero or by a guess.
    """
    try:
        with _client() as c:
            _seed(c, contributed=None)
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert p["contributed_ils"] is None
    assert p["gain_vs_contributions_ils"] is None
    assert p["gain_vs_contributions_pct"] is None
    assert p["gain_vs_basis_ils"] is not None, "the fallback still has to work"


def test_e_the_front_page_leads_with_deposits_and_demotes_basis():
    """The card itself, not just the payload.

    Asserted against the source because there is no DOM here -- but the strings
    are the deliverable: an amber "no recorded deposit behind it" warning on 350
    shekels of ordinary bookkeeping is the thing being removed.
    """
    from pathlib import Path

    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    nav = html.split("function navSummary(p){", 1)[1].split("\nasync function", 1)[0]

    assert "gain_vs_contributions_ils" in nav, "the card is not reading the new field"
    assert "you put in" in nav, "the headline should name what it is measured against"
    assert "realised and reinvested" in nav, "the 350 has to be NAMED, not hidden"
    assert "has no recorded deposit behind it" not in nav, (
        "the amber unexplained-basis warning is still on the card")
    assert "Basis reconciled" not in nav, (
        "the basis baseline line is vestigial once the warning is gone")
    # And the fallback is still wired for a book with no deposits.
    assert "gain_vs_basis_ils" in nav


def test_f_a_deposit_only_book_reports_zero_gain_not_a_gap():
    """Money in, nothing bought, nothing gained. The simplest possible case.

    Worth pinning because it is the one where the old and new headline agree,
    and any sign error in the new arithmetic shows up here first.
    """
    try:
        with _client() as c:
            c.post("/api/v1/portfolio/cash", json={"amount_ils": 5000})
            c.post("/api/v1/portfolio/contributions",
                   json={"amount_ils": 5000, "kind": "deposit"})
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert p["gain_vs_contributions_ils"] == pytest.approx(0.0, abs=0.02)
    assert p["gain_vs_contributions_pct"] == pytest.approx(0.0, abs=0.02)
