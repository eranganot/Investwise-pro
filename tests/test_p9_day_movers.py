"""P9 -- which holdings moved the book, and on which session.

2026-11-05: the chart's "Today so far" read -1.12% before the US market had
opened, because it compared live prices with an older snapshot. The movers
list must name the session it measured: "today so far" only when the quote is
newer than the last daily close, otherwise "last session" and its date.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import day_attribution as da

CLOSES = [("2026-11-04", 100.0), ("2026-11-05", 102.0), ("2026-11-06", 99.0)]


def test_an_open_market_measures_today_against_the_last_close():
    m = da.move_for(CLOSES, current_price=101.0, price_as_of="2026-11-09T15:30:00+00:00", ccy="USD")
    assert m["basis"] == "today so far" and m["session"] == "2026-11-09"
    assert m["move"] == pytest.approx(2.0)
    assert m["move_pct"] == pytest.approx(2.0 / 99.0 * 100)


def test_a_closed_market_reports_its_last_session_by_date():
    """The 11-05 case: no quote newer than the close -> the last session, named."""
    m = da.move_for(CLOSES, current_price=99.0, price_as_of="2026-11-06T21:00:00+00:00", ccy="USD")
    assert m["basis"] == "last session" and m["session"] == "2026-11-06"
    assert m["move"] == pytest.approx(-3.0)


def test_tel_aviv_closes_in_agorot_are_scaled_to_the_books_shekels():
    pts = [("2026-11-05", 1000.0), ("2026-11-06", 1100.0)]
    m = da.move_for(pts, current_price=11.0, price_as_of="2026-11-06", ccy="ILS")
    assert m["move"] == pytest.approx(1.0)


def test_one_close_and_no_newer_quote_is_not_a_move():
    assert da.move_for([("2026-11-06", 99.0)], current_price=99.0,
                       price_as_of="2026-11-06", ccy="USD") is None


@pytest.mark.asyncio
async def test_the_parts_add_up_and_cash_and_stale_are_listed_not_guessed():
    view = {"positions": [
        {"ticker": "QQQ", "asset_class": "Equities", "quantity": 2.0, "current_price": 101.0,
         "current_price_ils": 303.0, "value_ils": 606.0, "currency": "USD",
         "price_as_of": "2026-11-09T15:30:00+00:00"},
        {"ticker": "BIL", "asset_class": "Fixed Income", "quantity": 10.0, "current_price": 99.0,
         "current_price_ils": 297.0, "value_ils": 2970.0, "currency": "USD",
         "price_as_of": "2026-11-06T21:00:00+00:00"},
        {"ticker": "OLD", "asset_class": "Equities", "quantity": 1.0, "current_price": 5.0,
         "current_price_ils": 15.0, "value_ils": 15.0, "currency": "USD", "price_stale": True},
        {"ticker": "CASH", "asset_class": "Cash", "quantity": 150.0, "value_ils": 150.0},
    ]}

    async def _holdings(session, user, entity=None):
        return view

    async def _traded(session, user, today):
        return {"QQQ"}

    with patch("app.services.portfolio_view.holdings", _holdings), \
            patch("app.providers.registry.guarded_history", lambda tk, days=10: CLOSES), \
            patch.object(da, "_traded_today", _traded):
        out = await da.day_movers(None, type("U", (), {"email": "x"})())

    by = {r["ticker"]: r for r in out["rows"]}
    # QQQ: 2 sh x (101-99) x 3 ILS/USD = 12; BIL: 10 sh x (99-102) x 3 = -90
    assert by["QQQ"]["move_ils"] == pytest.approx(12.0) and by["QQQ"]["traded_today"] is True
    assert by["BIL"]["move_ils"] == pytest.approx(-90.0) and by["BIL"]["basis"] == "last session"
    assert out["total_ils"] == pytest.approx(-78.0)
    assert [r["ticker"] for r in out["rows"]] == ["BIL", "QQQ"], "largest move first"
    assert {s["ticker"] for s in out["skipped"]} == {"OLD"}
    assert out["sessions"] == ["2026-11-06", "2026-11-09"]
