"""C6: broker connect+sync with defaults writes mock holdings into the real book; READ_ONLY suffices."""
import json

from fastapi.testclient import TestClient

from conftest import tok


def test_c6_broker_sync_writes_mock_holdings():
    from app.main import app
    ro = tok("READ_ONLY")
    with TestClient(app) as c:
        r = c.post("/api/v1/intake/portfolio", headers=tok("SUPERADMIN"), json={
            "entity_name": "Personal",
            "positions": [{"ticker": "VOO", "market": "NYSE", "depth": 1, "spot_price": 500,
                           "listing_price": 500, "quantity": 10, "cost_basis": 400}]})
        print("\nSEED intake", r.status_code)
        before = c.get("/api/v1/portfolio", headers=ro)
        bj = before.json()
        print("BEFORE", before.status_code, json.dumps(
            [(h.get("ticker"), h.get("quantity")) for h in (bj.get("holdings") or bj.get("positions") or [])]))
        r1 = c.post("/api/v1/broker/connect", headers=ro)
        print("CONNECT (READ_ONLY token, no body)", r1.status_code, r1.text[:300])
        r2 = c.post("/api/v1/broker/sync", headers=ro)
        print("SYNC (READ_ONLY token, no body)", r2.status_code, r2.text[:400])
        after = c.get("/api/v1/portfolio", headers=ro)
        aj = after.json()
        print("AFTER", after.status_code, json.dumps(
            [(h.get("ticker"), h.get("quantity"), h.get("account") or h.get("account_name"))
             for h in (aj.get("holdings") or aj.get("positions") or [])]))
        print("AFTER keys", list(aj.keys())[:20])
