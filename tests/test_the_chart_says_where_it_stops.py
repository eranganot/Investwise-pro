"""2026-10-22 -- "the trend looks positive but the portfolio holdings are now lower
than a few hours ago".

Read live from production. The account-history line ended on 2026-10-21 at
21,122.55 ILS (-9.90%), rising for three days. The live total on the same screen
read 19,675 ILS after SOXL fell 20% that session. Both were correct and the screen
said nothing about the gap: the recorded series stops at the last snapshot.

`provisional_point` adds ONE labelled point -- live prices against the last
snapshot -- but only when that comparison is honest. Otherwise it returns a reason
and the chart draws nothing extra: a gap is honest, an invented number is not.
"""
import pytest

from app.services import nav_history as nh

LAST = {"as_of": "2026-10-21", "nav_ils": 21122.55, "positions": 16}
TWR = {"pct": [0.0, -12.4984, -10.958, -11.2519, -9.9003]}


def _prov(**kw):
    base = dict(points=[LAST], twr=TWR, live_nav=19675.04, live_positions=16,
                today="2026-10-22", flows=[], traded=lambda a, b: False)
    base.update(kw)
    pts = base.pop("points")
    twr = base.pop("twr")
    return nh.provisional_point(pts, twr, **base)


def test_the_live_case_from_production():
    point, why = _prov()
    assert why is None
    assert point["as_of"] == "2026-10-22" and point["since"] == "2026-10-21"
    # (1 - 0.099003) * (19675.04 / 21122.55) - 1 = -16.07%
    assert point["pct"] == pytest.approx(-16.0743, abs=0.01)
    assert point["nav_ils"] == 19675.04
    assert point["label"] == "today so far"


def test_a_rising_day_gives_a_rising_point():
    point, _ = _prov(live_nav=21500.0)
    assert point["pct"] > TWR["pct"][-1]


@pytest.mark.parametrize("kw, reason", [
    (dict(today="2026-10-21"), "already recorded"),
    (dict(live_positions=15), "number of holdings"),
    (dict(flows=[{"occurred_at": "2026-10-22T09:00:00Z", "amount_ils": 500.0}]), "money moved"),
    (dict(traded=lambda a, b: True), "trade"),
    (dict(live_nav=0.0), "no usable value"),
    (dict(twr={"pct": [0.0, None]}), "could not be measured"),
    (dict(twr={"pct": []}), "no recorded series"),
])
def test_it_declines_whenever_the_comparison_is_not_honest(kw, reason):
    point, why = _prov(**kw)
    assert point is None, "a provisional point was invented where the comparison does not hold"
    assert reason in why


def test_a_deposit_on_an_earlier_day_does_not_block_it():
    """Only money moving AFTER the last snapshot matters."""
    point, why = _prov(flows=[{"occurred_at": "2026-10-20T09:00:00Z", "amount_ils": 500.0}])
    assert why is None and point is not None


def test_it_never_writes_anything():
    """Pure: the inputs are unchanged."""
    pts, twr = [dict(LAST)], {"pct": list(TWR["pct"])}
    _prov(points=pts, twr=twr)
    assert pts == [LAST] and twr == TWR


def test_the_endpoint_always_says_whether_it_has_a_provisional_point():
    """Through the API: the key is always present, a point or a reason."""
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient

    import app.main as m

    with TestClient(m.app) as c:
        body = c.get("/api/v1/portfolio/nav-history?range=1M").json()
    if not body.get("ok"):
        pytest.skip("this test book has too little recorded history for a series")
    assert "provisional" in body and "provisional_reason" in body
    assert body["provisional"] is None or body["provisional_reason"] is None
