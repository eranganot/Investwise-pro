"""Surplus cash must get a sized, executable home.

Reported after accepting a stop-loss: "now I have more cash -- so where should I
put it? hold it or reinvest?". The app noticed the idle cash ("Put idle cash to
work") but the card was `apply: none` and named nothing, sized nothing, and
could execute nothing.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import funding_service as fund  # noqa: E402
from app.services.recommendations import (  # noqa: E402
    _ACTIONABLE_KINDS, _redeploy_cash_recs,
)


class _Row:
    """Faithful stand-in for an ORM Position.

    current_mix() reads quantity/current_price/market/meta, so a stub carrying
    only a ticker blows up inside the allocation maths rather than testing it.
    Everything is priced 1.0 on TASE (ILS) so value == quantity and no FX rate
    has to be mocked -- the arithmetic stays readable.
    """

    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


# Eran's book after the META stop-loss executed: ~30% cash against a 3% floor.
def _book():
    return [
        _Row("CASH", 6473.0, "Cash"),
        _Row("AMZN", 6265.0),
        _Row("V", 5208.0),
        _Row("SCHD", 703.0),
        _Row("MSFT", 760.0),
        _Row("COW", 2147.0, "Commodities"),
    ]


def _snap(nav, weights):
    return {"nav": nav, "exposure_ticker": weights}


NAV = 21556.0
WEIGHTS = {"AMZN": 0.29, "V": 0.24, "SCHD": 0.03, "MSFT": 0.035, "COW": 0.10}


def test_redeploy_is_registered_as_executable():
    assert "redeploy_cash" in _ACTIONABLE_KINDS


def test_no_card_when_cash_is_at_or_below_the_floor():
    """Nothing meaningful to redeploy is not a recommendation."""
    rows = [r for r in _book() if r.ticker != "CASH"] + [_Row("CASH", 400.0, "Cash")]
    out = _redeploy_cash_recs(rows, _snap(NAV, WEIGHTS), None, "Grow", 0.25, 400.0)
    assert out == []


def test_surplus_cash_produces_one_sized_executable_card():
    out = _redeploy_cash_recs(_book(), _snap(NAV, WEIGHTS), None, "Grow", 0.25, 6473.0)
    assert len(out) == 1, "one card about the cash, not several"
    card = out[0]
    assert card["apply"]["kind"] == "redeploy_cash"
    legs = card["apply"]["legs"]
    assert legs, "must name concrete tickers, not just 'your target mix'"
    for leg in legs:
        assert leg["amount_ils"] >= fund.MIN_TRADE_ILS, "no sub-minimum dust trades"
        assert leg["reason"], "every leg explains itself"
        assert leg["ticker"] != "CASH", "never 'buys' cash with cash"
    # Never proposes spending the objective's floor.
    floor = fund.cash_floor_ils(NAV, "Grow", None)
    assert card["meta"]["cash_after_ils"] >= floor - 1.0
    assert sum(x["amount_ils"] for x in legs) <= 6473.0


def test_never_proposes_breaching_the_single_name_cap():
    """A holding already at the cap gets no top-up."""
    rows = [_Row("CASH", 5000.0, "Cash"), _Row("AMZN", 5000.0)]
    out = _redeploy_cash_recs(rows, _snap(10000.0, {"AMZN": 0.25}), None, "Grow", 0.25, 5000.0)
    for card in out:
        for leg in card["apply"]["legs"]:
            assert leg["ticker"] != "AMZN", "AMZN is already at the 25% cap"


def test_card_states_the_buffer_it_keeps():
    out = _redeploy_cash_recs(_book(), _snap(NAV, WEIGHTS), None, "Grow", 0.25, 6473.0)
    assert out, "expected a card for 30% cash against a 3% floor"
    assert "buffer" in out[0]["action"].lower()
    assert any(h.startswith("Keep ") for h in out[0]["how"])
