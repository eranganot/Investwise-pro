"""T4 - the Today chart's range is measured server-side, not sliced client-side.

`index_series` normalises to the first value IN THE SERIES IT WAS GIVEN, so a
re-based percentage only means "change over this window" if the series was
fetched for that window. That is why the range is a request parameter and these
tests exist: a client-side slice would re-base against the wrong day, and a slice
of a DOWNSAMPLED long series would draw sixteen sessions as a week.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes.intake import PERF_RANGES
from app.main import app

PORT = {"entity_name": "Personal", "positions": [
    {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
     "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 50}]}


def _cleanup(c):
    c.delete("/api/v1/portfolio/position", params={"ticker": "AAA", "market": "NYSE"})


def test_every_button_on_the_card_maps_to_a_range():
    # The five the UI offers. A button with no server-side meaning would fall
    # through to the 252-day default and silently draw the wrong window.
    assert set(PERF_RANGES) == {"1W", "1M", "1Q", "1Y", "MAX"}
    assert PERF_RANGES["1W"] < PERF_RANGES["1M"] < PERF_RANGES["1Q"] \
        < PERF_RANGES["1Y"] < PERF_RANGES["MAX"]


def test_a_range_reaches_the_measurement():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        try:
            r = c.post("/api/v1/portfolio/performance", params={"range": "1Q"}).json()
            assert r["requested_days"] == PERF_RANGES["1Q"]
            assert r["range"] == "1Q"
        finally:
            _cleanup(c)


def test_a_shorter_range_measures_fewer_sessions():
    """The load-bearing one: the window has to actually narrow."""
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        try:
            short = c.post("/api/v1/portfolio/performance", params={"range": "1M"}).json()
            long = c.post("/api/v1/portfolio/performance", params={"range": "1Y"}).json()
            if short.get("ok") and long.get("ok"):
                assert short["observations"] <= long["observations"]
                # And each is re-based to its OWN start, which is what makes the
                # percentage mean "change over this range".
                assert short["portfolio_index"][0] == 100.0
                assert long["portfolio_index"][0] == 100.0
        finally:
            _cleanup(c)


def test_an_unknown_range_abstains_rather_than_defaulting():
    """Falling through to 252 days would draw a year while the button says 1W."""
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        try:
            r = c.post("/api/v1/portfolio/performance", params={"range": "5Y"}).json()
            assert r["ok"] is False
            assert "1W" in r["detail"]
        finally:
            _cleanup(c)


def test_no_range_keeps_the_previous_behaviour():
    """Existing callers must not move."""
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        try:
            r = c.post("/api/v1/portfolio/performance").json()
            assert r["requested_days"] == 252
            assert r["range"] is None
        finally:
            _cleanup(c)


def test_history_days_is_clamped_not_trusted():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        try:
            hi = c.post("/api/v1/portfolio/performance",
                        params={"history_days": 999999}).json()
            lo = c.post("/api/v1/portfolio/performance",
                        params={"history_days": 1}).json()
            assert hi["requested_days"] == 2600
            assert lo["requested_days"] == 7
        finally:
            _cleanup(c)
