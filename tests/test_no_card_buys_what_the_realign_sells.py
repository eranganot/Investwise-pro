"""C30 (re-measured 2026-11-09) -- no card may buy a name the core realign sells.

MEASURED ON PRODUCTION, 2026-11-06 (the trade ledger, read-only):

    06:32:46  Buy  1.65 CVX  1,114 ILS   buy_funded       card rec_f23044
    06:32:53  Sell 1.65 CVX  1,114 ILS   core_to_basket   card rec_f6f017

`rec_f23044` is `_rid("buy_idea", "CVX", 1000)`: a screener buy idea. Seven
seconds later the core realign sold the same shares, because the core runs
Diversified Global Growth (VTI/QQQ/VXUS/SMH) and `plan_realign` sells EVERY
core-region holding its basket does not name. The same shape, from the cash
card, on 2026-10-31: BND bought 2,830 and 6,821 ILS, then sold by the realign
within 40 seconds both times (before PR 155, but this path was not touched by
it: the cash card's "new position" pick reads the screener too).

Two producers, two answers, one book: the buy-idea card claimed a position the
app's own next card removes, which the card-claim invariant forbids. The rule
pinned here: while the core holds a basket, a NEW name may only be one the
basket or a sleeve names. Not financial advice.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services.recommendations import _buy_idea_recs
from app.services.strategies import core_weights

CORE = "grow_diversified"


class _Row:
    def __init__(self, ticker, value, cls="Equities"):
        self.ticker = ticker
        self.quantity = 1.0
        self.current_price = value
        self.market = "NYSE"
        self.meta = {"asset_class": cls}
        self.cost_basis = value


def _book():
    rows = [_Row("VTI", 40_000.0), _Row("QQQ", 30_000.0)]
    snap = {"nav": 100_000.0, "avg_volatility_pct": 15.0,
            "holdings": [{"ticker": "VTI", "value_ils": 40_000.0, "asset_class": "Equities"},
                         {"ticker": "QQQ", "value_ils": 30_000.0, "asset_class": "Equities"}]}
    return rows, snap


def _idea(tk):
    return {"ticker": tk, "name": tk, "score": 80.0, "kind": "stock", "sector": "Energy",
            "reasons": ["ranks well"], "flags": [], "metrics": {}, "data_gaps": [],
            "ranked_for": {"objective": "Grow", "coverage": {"requested": 58, "ranked": 58}}}


_FAKE = type("B", (), {"amount_ils": 1000.0, "impact": "x", "summary": "y",
                       "apply_spec": {"kind": "buy_funded"},
                       "fund": {"sells": [], "from_cash_ils": 1000.0}})()

_TARGET = {"core_strategy_id": CORE, "core_pct": 0.5, "target": {"Equities": 1.0}}


def test_the_basket_is_what_the_realign_holds_the_core_to():
    """The premise: CVX is not in the basket, VXUS is."""
    w = core_weights(CORE)
    assert "CVX" not in w and "VXUS" in w


def test_a_buy_idea_off_the_core_basket_gets_no_card():
    """The 11-06 round trip: CVX bought, then sold by the realign 7 s later."""
    rows, snap = _book()
    with patch("app.services.funding_service.propose_funded_buy", return_value=_FAKE):
        out = _buy_idea_recs([_idea("CVX")], rows, snap, None, "Grow", book_target=_TARGET)
    assert out == [], "a buy idea named a ticker the core realign will sell straight back"


def test_a_buy_idea_in_the_basket_still_gets_its_card():
    """The filter must not silence ideas the realign would keep."""
    rows, snap = _book()
    with patch("app.services.funding_service.propose_funded_buy", return_value=_FAKE):
        out = _buy_idea_recs([_idea("VXUS")], rows, snap, None, "Grow", book_target=_TARGET)
    assert [c["meta"]["ticker"] for c in out] == ["VXUS"]


def test_without_a_basketed_core_ideas_are_unchanged():
    """No core basket -> nothing sells off-basket names -> no reason to filter."""
    rows, snap = _book()
    with patch("app.services.funding_service.propose_funded_buy", return_value=_FAKE):
        out = _buy_idea_recs([_idea("CVX")], rows, snap, None, "Grow", book_target={})
    assert [c["meta"]["ticker"] for c in out] == ["CVX"]


# --- the cash card's pick for a class the book holds none of (10-31: BND) ----

class _TaseRow:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


def _cash_legs(core_id):
    from app.services.recommendations import _redeploy_cash_recs
    rows = [_TaseRow("CASH", 5000.0, "Cash"), _TaseRow("VTI", 15000.0)]
    snap = {"nav": 20000.0, "exposure_ticker": {"VTI": 0.75}}
    mix = {"Equities": 0.6, "Fixed Income": 0.37, "Cash": 0.03}
    ideas = [{"ticker": "BND", "score": 80.0}, {"ticker": "VTI", "score": 70.0}]
    with patch("app.services.recommendations._buy_ideas", return_value=ideas):
        out = _redeploy_cash_recs(rows, snap, None, "Grow", 0.5, 5000.0,
                                  book_mix=mix, core_id=core_id)
    return [lg for c in out for lg in (c.get("apply") or {}).get("legs", [])]


def test_the_cash_card_opens_no_position_the_realign_sells():
    """With a basketed core, BND (not in the basket, no sleeve) is not bought."""
    assert all(lg["ticker"] != "BND" for lg in _cash_legs(CORE)), _cash_legs(CORE)


def test_without_a_core_the_cash_card_still_fills_an_empty_class():
    """The filter is for a basketed core only; without one the gap is filled."""
    assert any(lg["ticker"] == "BND" for lg in _cash_legs(None))


# --- C33: the plan-weight pass never buys a class that is over target ---------

def _c33_legs(cap):
    from app.services.recommendations import _redeploy_cash_recs
    nav = 20000.0
    rows = [_TaseRow("QQQ", 10000.0), _TaseRow("VTI", 3500.0),
            _TaseRow("BIL", 4000.0, "Fixed Income"), _TaseRow("CASH", 2500.0, "Cash")]
    weights = {"QQQ": 0.5, "VTI": 0.175, "BIL": 0.2}
    target = {"Equities": 0.62, "Fixed Income": 0.35, "Cash": 0.03}
    out = _redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": weights}, None, "Grow",
                              cap, 2500.0, book_mix=target,
                              sleeve_targets={"QQQ": 0.35, "BIL": 0.35})
    return [lg for c in out for lg in (c.get("apply") or {}).get("legs", [])]


def test_a_capped_under_class_does_not_send_the_cash_to_an_over_class():
    """Equities 67.5% vs 62%. BIL capped at 22% can take only 400 of the cash;
    before C33 the leftover bought 900 of VTI (under its NAME target) and
    Equities went to 72%. Now the leftover stays in cash."""
    legs = _c33_legs(0.22)
    assert legs and all(lg["ticker"] == "BIL" for lg in legs), legs


def test_with_room_the_under_class_still_gets_the_cash():
    legs = _c33_legs(0.6)
    assert [lg["ticker"] for lg in legs] == ["BIL"]
    assert legs[0]["amount_ils"] >= 1800.0
