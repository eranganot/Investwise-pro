"""PR 29 - the screener finally gets a surface, and it is a card.

`buy_ideas` had been in the /recommendations payload for months and NOTHING in
the app read it:

    grep buy_ideas app/static_app/ frontend/   -> 0 matches
    grep screener  app/static_app/index.html   -> 0 matches

Its only consumer was one `next()` inside `_redeploy_cash_recs`, lifting the
single top-ranked name in a class the book holds none of. So PR 24, 25 and 27
were fixing a ranking that reached the user through exactly one ticker, on one
card, in one situation.

Eran's call was to put the ideas into Today's "What to do" as ordinary cards
rather than build a separate list, and it is the right one: a second surface
proposing trades is precisely what the card-claim invariant and
`contradictions.py` exist to police, and it would be the only such surface with
no `apply` spec to check its claim against.

These tests pin the invariants that choice buys.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services.recommendations import _MAX_BUY_IDEA_CARDS, _buy_idea_recs


class _Row:
    def __init__(self, ticker, value, cls="Equities"):
        self.ticker = ticker
        self.quantity = 1.0
        self.current_price = value
        self.market = "NYSE"
        self.meta = {"asset_class": cls}
        self.cost_basis = value


def _book():
    """A book with room in Fixed Income, so a bond idea is fundable."""
    rows = [_Row("SPY", 80_000.0), _Row("QQQ", 20_000.0)]
    snap = {"nav": 100_000.0, "avg_volatility_pct": 15.0,
            "holdings": [{"ticker": "SPY", "value_ils": 80_000.0, "asset_class": "Equities"},
                         {"ticker": "QQQ", "value_ils": 20_000.0, "asset_class": "Equities"}]}
    return rows, snap


def _idea(tk, **kw):
    base = {"ticker": tk, "name": f"{tk} Fund", "score": 74.9, "kind": "etf",
            "sector": "Fixed Income", "reasons": ["Cheap on earnings (P/E 15)"],
            "flags": [], "metrics": {}, "data_gaps": [],
            "ranked_for": {"objective": "Grow", "coverage": {"requested": 58, "ranked": 58}}}
    base.update(kw)
    return base


def test_no_funded_buy_means_no_card():
    """`propose_funded_buy` returning None is the gate, and it must be the ONLY
    gate that matters. A ranked name is a candidate, never an instruction: if the
    plan does not want the class, or it cannot be paid for, there is no card."""
    rows, snap = _book()
    with patch("app.services.funding_service.propose_funded_buy", return_value=None):
        out = _buy_idea_recs([_idea("BND"), _idea("AGG")], rows, snap, None, "Grow")
    assert out == []


def test_a_card_is_never_emitted_for_a_ticker_another_card_already_names():
    """Two cards naming one ticker is the shape STATUS records for the three
    trailing stops. The ledger stops them spending the same shares; this stops
    them proposing the same name."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "Moves Fixed Income 0% -> 5%.",
                          "summary": "Funded from cash.", "apply_spec": {"kind": "buy"},
                          "fund": {"sells": [], "from_cash_ils": 5000.0}})()
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([_idea("BND")], rows, snap, None, "Grow",
                             already_named={"BND"})
    assert out == [], "a card was emitted for a ticker another card already proposes"


def test_a_name_the_book_already_holds_is_left_to_the_rebalance_cards():
    """Topping up an existing position is a sizing decision the drift and
    rebalance cards are built to make. This card exists to surface a name the
    book does NOT hold."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "x", "summary": "y",
                          "apply_spec": {}, "fund": {}})()
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([_idea("SPY")], rows, snap, None, "Grow")
    assert out == []


def test_today_is_not_flooded():
    """Eleven ranked ideas would bury the risk and drift cards under a wall of
    optional purchases. Today is a list of things to do, not a leaderboard."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "x", "summary": "y",
                          "apply_spec": {}, "fund": {}})()
    ideas = [_idea(f"T{i}") for i in range(11)]
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs(ideas, rows, snap, None, "Grow")
    assert len(out) == _MAX_BUY_IDEA_CARDS == 2


def test_the_card_says_when_the_score_rests_on_missing_factors():
    """THE PAYOFF FOR PR 27. `data_gaps` was recorded so a reader could tell a
    composite built from five factors from one built from eight. If the card
    does not say it, the data might as well have been dropped."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "x", "summary": "y",
                          "apply_spec": {}, "fund": {}})()
    gapped = _idea("BND", data_gaps=["pb: not reported", "debt_to_equity: not reported"])
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([gapped], rows, snap, None, "Grow")

    assert len(out) == 1
    why = out[0]["why"]
    assert "WITHOUT 2 of its eight factors" in why, why
    assert "pb" in why and "debt_to_equity" in why
    assert "rests on less" in why
    assert out[0]["meta"]["data_gaps"] == gapped["data_gaps"]


def test_a_complete_name_makes_no_excuse_for_itself():
    """The mirror. A name measured on all eight factors must not carry a caveat
    it has not earned -- a warning on every card is a warning on none."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "x", "summary": "y",
                          "apply_spec": {}, "fund": {}})()
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([_idea("BND")], rows, snap, None, "Grow")
    assert "WITHOUT" not in out[0]["why"]
    assert out[0]["meta"]["data_gaps"] == []


def test_the_reproducibility_pin_rides_with_the_card():
    """A recommendation read next month must trace to the weights AND the
    coverage that produced it, not to whatever the table says by then."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0, "impact": "x", "summary": "y",
                          "apply_spec": {}, "fund": {}})()
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([_idea("BND")], rows, snap, None, "Grow")
    pin = out[0]["meta"]["ranked_for"]
    assert pin["coverage"]["ranked"] == 58
    assert pin["objective"] == "Grow"


def test_the_card_carries_an_apply_spec_and_an_amount():
    """A card with no `apply` cannot be checked against the change it claims,
    which is the one thing the card-claim invariant needs from it."""
    rows, snap = _book()
    fake = type("B", (), {"amount_ils": 5000.0,
                          "impact": "Moves Fixed Income from 0% to 5%.",
                          "summary": "Funded from cash.",
                          "apply_spec": {"kind": "buy", "ticker": "BND"},
                          "fund": {"sells": [], "from_cash_ils": 5000.0}})()
    with patch("app.services.funding_service.propose_funded_buy", return_value=fake):
        out = _buy_idea_recs([_idea("BND")], rows, snap, None, "Grow")
    card = out[0]
    assert card["apply"] == {"kind": "buy", "ticker": "BND"}
    assert card["est_amount"] == 5000.0
    assert card["severity"] == "LOW", "an optional idea must not outrank a risk card"
    assert card["dimension"] == "opportunity"
    assert any("Use ₪5,000 of cash" in h for h in card["how"]), card["how"]


def test_a_zero_shortfall_is_not_narrated_as_a_shortfall():
    """Found BY these cards, in shared code every buy card uses.

        "Fund it with 70,078 by selling 188.8887 HUGE. ... That still
         leaves 0 short."

    `shortfall_ils` is stored to the agora, so any residue under half a shekel
    formats as "0" while staying truthy. The sentence contradicted the rest of
    the line and the card's own est_amount.
    `test_no_card_is_presented_short[single_name_breach]` had asserted this all
    along; no producer had hit the round-to-zero case until now.
    """
    from app.services.funding_service import MIN_TRADE_ILS, describe_funding

    base = {"sells": [], "from_cash_ils": 1000.0, "tax_ils": 0.0}
    assert "short" not in describe_funding({**base, "shortfall_ils": 0.0}, "Equities")
    assert "short" not in describe_funding({**base, "shortfall_ils": 0.4}, "Equities")
    assert "short" not in describe_funding({**base, "shortfall_ils": MIN_TRADE_ILS - 1},
                                           "Equities")
    # A real one still speaks up.
    assert "short" in describe_funding({**base, "shortfall_ils": 4_000.0}, "Equities")
