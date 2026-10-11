from fastapi.testclient import TestClient

from conftest import tok


def test_c7b_whatif_demo_fallback():
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/v1/whatif", headers=tok("READ_ONLY"), json={})
        j = r.json()
        print("\nC7b /whatif EMPTY BOOK", r.status_code, j["risk_profile"],
              "| keys:", list(j.keys()))
