"""Standing orders: a monthly deposit into cash that records itself.

Eran, 2026-09-20: "I want to allow and create repetitive contribution (standing
order per monthly) to my cash." Chosen: auto-credit + notify.

What is pinned here is what would cost money or trust if it were wrong:
  * a month is credited ONCE, however many times the job runs;
  * a deposit is a deposit -- it reaches the contributions ledger, so gain and
    the NAV-flow detector never read it as a return;
  * it lands on the day it was for, not the day the job noticed;
  * pausing means no deposits, including when you resume;
  * the scheduled entrypoint is actually invoked (CLAUDE.md: "a job nothing
    calls is a job nobody has tested" -- the NAV snapshot failed 7 nights of 7).
  * the goal projection and the behind-your-goal card count the order.

Not financial advice.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.services.standing_order_service as so
from app.main import app

TODAY = date(2026, 9, 20)


def _pos(ticker, qty, price):
    return {"ticker": ticker, "market": "TASE", "asset_class": "Equities", "depth": 2,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": price, "expected_return_pct": 8, "volatility_pct": 18}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(so, "local_today", lambda now=None: TODAY)
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio",
               json={"entity_name": "Personal", "positions": [_pos("AAA", 100, 100.0)]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 1000.0, "mode": "set"})
        yield c


def _cash(c):
    return c.get("/api/v1/portfolio/cash").json()["cash_ils"]


def _deposits(c):
    return [e for e in c.get("/api/v1/portfolio/contributions").json()["entries"]
            if (e.get("note") or "").startswith("standing order")]


def _make(c, amount=1067.0, day=1):
    r = c.post("/api/v1/portfolio/standing-orders",
               json={"amount_ils": amount, "day_of_month": day})
    assert r.status_code == 200, r.text
    return r.json()["order"]


def test_first_run_is_the_next_occurrence_not_today(client):
    assert _make(client, day=1)["next_run_on"] == "2026-10-01"
    assert _make(client, day=25)["next_run_on"] == "2026-09-25"
    # Created ON its day: today's transfer is not assumed to have arrived.
    assert _make(client, day=20)["next_run_on"] == "2026-10-20"


@pytest.mark.parametrize("body", [{"amount_ils": 0}, {"amount_ils": -5},
                                  {"amount_ils": 100, "day_of_month": 29},
                                  {"amount_ils": 100, "day_of_month": 0}])
def test_bad_orders_are_refused(client, body):
    assert client.post("/api/v1/portfolio/standing-orders", json=body).status_code == 422


def test_nothing_is_credited_before_the_day(client):
    _make(client, day=1)
    r = client.post("/api/v1/portfolio/standing-orders/run").json()
    assert r["credited"] == []
    assert _cash(client) == pytest.approx(1000.0)


def test_on_the_day_cash_and_the_ledger_both_move_once(client, monkeypatch):
    _make(client, amount=1067.0, day=1)
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2026, 10, 1))
    first = client.post("/api/v1/portfolio/standing-orders/run").json()
    assert [c["for"] for c in first["credited"]] == ["2026-10-01"]
    assert _cash(client) == pytest.approx(2067.0)
    deps = _deposits(client)
    assert len(deps) == 1 and deps[0]["amount_ils"] == pytest.approx(1067.0)
    assert deps[0]["occurred_at"].startswith("2026-10-01")
    # Run it again -- and again. Still one deposit.
    for _ in range(3):
        again = client.post("/api/v1/portfolio/standing-orders/run").json()
        assert again["credited"] == []
    assert _cash(client) == pytest.approx(2067.0)
    assert len(_deposits(client)) == 1
    order = client.get("/api/v1/portfolio/standing-orders").json()["orders"][0]
    assert order["next_run_on"] == "2026-11-01" and order["runs"] == 1


def test_the_ledger_note_alone_prevents_a_double_credit(client, monkeypatch):
    """The second guard: if next_run_on were somehow not advanced (a race), the
    deposit already in the ledger for that date still stops a second credit."""
    import asyncio

    from sqlalchemy import update

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import StandingOrder

    _make(client, amount=500.0, day=1)
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2026, 10, 1))
    client.post("/api/v1/portfolio/standing-orders/run")

    async def _rewind():
        # Own NullPool engine in this loop -- CLAUDE.md, cross-loop sessions.
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as s:
                await s.execute(update(StandingOrder).values(next_run_on="2026-10-01"))
                await s.commit()
        finally:
            await engine.dispose()
    asyncio.run(_rewind())
    client.post("/api/v1/portfolio/standing-orders/run")
    assert _cash(client) == pytest.approx(1500.0)
    assert len(_deposits(client)) == 1


def test_missed_months_are_caught_up_each_on_its_own_date(client, monkeypatch):
    _make(client, amount=100.0, day=1)
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2027, 3, 15))
    done = client.post("/api/v1/portfolio/standing-orders/run").json()["credited"]
    # Capped: Oct..Mar is six due runs, MAX_CATCH_UP credits three per tick.
    assert [c["for"] for c in done] == ["2026-10-01", "2026-11-01", "2026-12-01"]
    done = client.post("/api/v1/portfolio/standing-orders/run").json()["credited"]
    assert [c["for"] for c in done] == ["2027-01-01", "2027-02-01", "2027-03-01"]
    assert _cash(client) == pytest.approx(1600.0)


def test_paused_means_no_deposits_even_after_resuming(client, monkeypatch):
    oid = _make(client, amount=200.0, day=1)["id"]
    client.put(f"/api/v1/portfolio/standing-orders/{oid}", json={"active": False})
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2026, 12, 5))
    assert client.post("/api/v1/portfolio/standing-orders/run").json()["credited"] == []
    r = client.put(f"/api/v1/portfolio/standing-orders/{oid}", json={"active": True}).json()
    assert r["order"]["next_run_on"] == "2027-01-01", "resuming must not back-fill the pause"
    assert client.post("/api/v1/portfolio/standing-orders/run").json()["credited"] == []
    assert _cash(client) == pytest.approx(1000.0)


def test_a_deposit_is_not_a_gain(client, monkeypatch):
    """Gain vs contributions must not move when a standing order lands."""
    before = client.get("/api/v1/portfolio").json()
    _make(client, amount=1067.0, day=1)
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2026, 10, 1))
    client.post("/api/v1/portfolio/standing-orders/run")
    after = client.get("/api/v1/portfolio").json()
    assert after["nav_ils"] == pytest.approx(before["nav_ils"] + 1067.0, abs=0.01)
    if before.get("gain_vs_contributions_ils") is not None:
        assert after["gain_vs_contributions_ils"] == pytest.approx(
            before["gain_vs_contributions_ils"], abs=0.01)


def test_the_scheduled_job_actually_runs(client, monkeypatch):
    _make(client, amount=300.0, day=1)
    monkeypatch.setattr(so, "local_today", lambda now=None: date(2026, 10, 2))
    out = so.run_standing_orders_blocking()
    assert out["credited"] == 1 and out["users"] == 1, out
    assert _cash(client) == pytest.approx(1300.0)
    assert so.run_standing_orders_blocking()["credited"] == 0


def test_delete_and_totals(client):
    a = _make(client, amount=100.0)
    _make(client, amount=250.0, day=15)
    got = client.get("/api/v1/portfolio/standing-orders").json()
    assert got["monthly_total_ils"] == pytest.approx(350.0)
    client.delete(f"/api/v1/portfolio/standing-orders/{a['id']}")
    assert client.get("/api/v1/portfolio/standing-orders").json()["monthly_total_ils"] == 250.0


def test_the_projection_counts_the_standing_order(client):
    client.put("/api/v1/plan", json={"objective": "Grow", "target_amount": 200000,
                                     "horizon_years": 6})
    base = client.get("/api/v1/plan/projection").json()
    _make(client, amount=1000.0)
    wc = client.get("/api/v1/plan/projection").json()["with_contributions"]
    assert wc["available"] and wc["source"] == "standing_order", wc
    assert wc["per_year"] == pytest.approx(12000.0)
    assert "standing order" in wc["note"]
    assert base.get("with_contributions", {}).get("source") != "standing_order"


def _behind_card(c):
    cards = c.get("/api/v1/recommendations").json().get("recommendations", [])
    return next((x for x in cards if (x.get("apply") or {}).get("kind") == "set_standing_order"),
                None)


def test_the_behind_card_sets_up_the_order_it_recommends(client):
    client.put("/api/v1/plan", json={"objective": "Grow", "target_amount": 500000,
                                     "horizon_years": 5, "target_date": "2031"})
    card = _behind_card(client)
    assert card, "a book this far behind must get the contribution card"
    add = card["apply"]["monthly_total_ils"]
    r = client.post(f"/api/v1/recommendations/{card['id']}/accept")
    assert r.status_code == 200, r.text
    got = client.get("/api/v1/portfolio/standing-orders").json()
    assert got["monthly_total_ils"] == pytest.approx(add)
    # Accepting moved no money today.
    assert _cash(client) == pytest.approx(1000.0)


def test_with_an_order_the_card_asks_for_the_difference_not_the_whole(client):
    client.put("/api/v1/plan", json={"objective": "Grow", "target_amount": 500000,
                                     "horizon_years": 5, "target_date": "2031"})
    without = _behind_card(client)["est_amount"]
    _make(client, amount=1000.0)
    card = _behind_card(client)
    assert card["title"].endswith("raise your monthly contribution")
    assert card["est_amount"] < without, (card["est_amount"], without)
    assert card["apply"]["monthly_total_ils"] > 1000.0
