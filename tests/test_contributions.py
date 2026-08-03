"""What you put in is external money -- not the book's current cost basis.

Reported live: 20,000 deposited, "You put in 20,790" displayed. ``invested_ils``
summed every position's ``cost_basis`` and FX-converted it at today's rate, so
it moved when the shekel moved, when a sale replaced an original basis with
net-of-CGT proceeds (taking a profit *raised* it), and when a fee swap
re-stamped basis at the live price. Only a deposit or a withdrawal may move it.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m


def _seed(c, qty=100, basis=100):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TEVA", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 120, "listing_price": 120, "quantity": qty, "cost_basis": basis,
         "expected_return_pct": 7, "volatility_pct": 14},
    ]})


def test_untracked_users_keep_the_legacy_estimate_not_a_confident_zero():
    with TestClient(m.app) as c:
        _seed(c)
        p = c.get("/api/v1/portfolio").json()
        assert p["invested_source"] == "cost_basis_estimate"
        assert p["invested_ils"] > 0
        assert c.get("/api/v1/portfolio/contributions").json()["tracked"] is False


def test_a_recorded_deposit_becomes_the_reported_figure():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        p = c.get("/api/v1/portfolio").json()
        assert p["invested_ils"] == 20000.0
        assert p["invested_source"] == "contributions"


def test_gain_is_measured_against_contributions():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        p = c.get("/api/v1/portfolio").json()
        assert round(p["gain_ils"], 2) == round(p["nav_ils"] - 20000.0, 2)
        assert p["gain_pct"] == round(p["gain_ils"] / 20000.0 * 100, 2)


def test_adjust_accumulates_and_withdrawals_subtract():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 5000, "mode": "adjust"})
        assert c.get("/api/v1/portfolio").json()["invested_ils"] == 25000.0
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": -3000, "mode": "adjust"})
        assert c.get("/api/v1/portfolio").json()["invested_ils"] == 22000.0


def test_selling_a_position_does_not_change_what_you_put_in():
    """The regression. Accepting a sell credits CASH with net-of-CGT proceeds at
    ``cost_basis = 1.0``; under the old derivation that replaced the sold
    position's basis with its realized value, so a profitable exit inflated
    "you put in"."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        before = c.get("/api/v1/portfolio").json()["invested_ils"]
        pos = c.get("/api/v1/portfolio").json()["positions"]
        holding = next(p for p in pos if p["ticker"] == "TEVA")
        c.request("DELETE", f"/api/v1/portfolio/holdings/{holding['id']}")
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 12000, "mode": "set"})
        after = c.get("/api/v1/portfolio").json()
        assert after["invested_ils"] == before == 20000.0
        assert after["invested_source"] == "contributions"


def test_adding_cash_is_not_a_contribution():
    """Setting the cash balance records liquidity, not new money from outside."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 3000, "mode": "set"})
        assert c.get("/api/v1/portfolio").json()["invested_ils"] == 20000.0


def test_set_replaces_the_ledger_rather_than_accumulating():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 9000, "mode": "set"})
        body = c.get("/api/v1/portfolio/contributions").json()
        assert body["total_ils"] == 9000.0
        assert len(body["entries"]) == 1


def test_entries_are_listed_with_their_direction():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        c.post("/api/v1/portfolio/contributions",
               json={"amount_ils": -2500, "mode": "adjust", "note": "took some out"})
        entries = c.get("/api/v1/portfolio/contributions").json()["entries"]
        kinds = {e["kind"] for e in entries}
        assert kinds == {"deposit", "withdrawal"}
        assert any(e["note"] == "took some out" for e in entries)


def test_a_zero_adjustment_is_a_no_op():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 0, "mode": "adjust"})
        body = c.get("/api/v1/portfolio/contributions").json()
        assert body["total_ils"] == 20000.0 and len(body["entries"]) == 1
