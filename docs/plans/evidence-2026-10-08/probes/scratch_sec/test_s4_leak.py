from fastapi.testclient import TestClient

from conftest import tok


def test_s4_provider_health_leaks_held_ticker(monkeypatch):
    from app.main import app
    from app.providers import registry

    class Boom:
        name = "builtin"

        def get_quote(self, *a, **k):
            raise RuntimeError("upstream 500")
    monkeypatch.setattr(registry, "market_provider", lambda *a, **k: Boom())
    with TestClient(app) as c:
        sa = tok("SUPERADMIN")
        c.post("/api/v1/intake/portfolio", headers=sa, json={"entity_name": "Personal", "positions": [
            {"ticker": "SECRETHOLDING", "market": "NYSE", "depth": 1, "spot_price": 10,
             "listing_price": 10, "quantity": 1, "cost_basis": 10}]})
        rp = c.post("/api/v1/portfolio/refresh-prices", headers=sa)
        print("\nrefresh-prices", rp.status_code, rp.text[:160])
        ph = c.get("/api/v1/providers/health")
        print("ANON providers/health:", ph.status_code, ph.text[:420])
        print("contains SECRETHOLDING:", "SECRETHOLDING" in ph.text)
