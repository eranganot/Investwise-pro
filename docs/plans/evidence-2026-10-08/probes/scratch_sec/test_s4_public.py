from fastapi.testclient import TestClient

from conftest import tok


def test_s4_public_probe():
    from app.main import app
    with TestClient(app) as c:
        for p in ("/docs", "/openapi.json", "/redoc", "/metrics", "/api/v1/providers/health",
                  "/api/v1/data-status", "/api/v1/markets/futures", "/api/v1/markets/summary",
                  "/api/v1/adversary/diagnostics", "/api/v1/jobs", "/app/", "/dashboard"):
            r = c.get(p, follow_redirects=False)
            print(f"\nANON GET {p} -> {r.status_code} {r.headers.get('location') or ''} {r.text[:200]!r}")
        # Does /providers/health reveal held tickers? seed an unpriceable holding, reprice, read anon.
        sa = tok("SUPERADMIN")
        c.post("/api/v1/intake/portfolio", headers=sa, json={"entity_name": "Personal", "positions": [
            {"ticker": "SECRETHOLDING", "market": "NYSE", "depth": 1, "spot_price": 10,
             "listing_price": 10, "quantity": 1, "cost_basis": 10}]})
        rp = c.post("/api/v1/portfolio/refresh-prices", headers=sa)
        print("\nrefresh-prices", rp.status_code, rp.text[:200])
        ph = c.get("/api/v1/providers/health")
        print("ANON providers/health after reprice:", ph.status_code, ph.text[:700])
        print("contains SECRETHOLDING:", "SECRETHOLDING" in ph.text)
        m = c.get("/metrics").text
        print("metrics mentions ticker/email:", "SECRETHOLDING" in m, "eran" in m,
              "| sample label lines:", [l for l in m.splitlines() if l.startswith("http_requests_total")][:4])
        oa = c.get("/openapi.json").json()
        print("openapi paths:", len(oa.get("paths", {})))
