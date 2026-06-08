"""API-level integration tests (review M1) - open mode + RBAC enforcement."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import auth as auth_mod
from app.core.auth import Role, create_token, require_role
from app.core.config import Settings
from app.main import app


# --- open-mode integration over the real app ---
def test_allocation_veto_via_api():
    with TestClient(app) as c:
        g = c.post("/api/v1/decision-feed/generate", json={
            "allocation": {"nav": 1_000_000,
                           "target_allocation": {"Equities": 0.6, "Fixed Income": 0.4},
                           "current_allocation": {"Equities": 0.72, "Fixed Income": 0.28}},
            "asset_class_map": {"TEVA": "Equities"}}).json()
        teva = [i for i in g["items"] if i["ticker"] == "TEVA"][0]
        assert teva["decision"] == "VETOED"


def test_safety_check_via_api():
    with TestClient(app) as c:
        r = c.post("/api/v1/safety/check", json={
            "holdings": {"BIG": 0.40}, "liquidity_ratio": 0.5, "proposals": []}).json()
        assert r["verdict"] == "warn"


def test_scenario_via_api():
    with TestClient(app) as c:
        r = c.post("/api/v1/scenario", json={"scenario": "INFLATION_SHOCK", "nav": 1_000_000}).json()
        assert r["expected_portfolio_value_delta_pct"] == pytest.approx(-4.2)


def test_research_events_via_api():
    with TestClient(app) as c:
        assert c.get("/api/v1/research/events").json()["count"] >= 1


# --- RBAC enforcement (isolated mini-app, auth forced on) ---
def _mini() -> FastAPI:
    mini = FastAPI()

    @mini.get("/protected")
    async def protected(p=Depends(require_role(Role.ANALYST))):
        return {"role": p.role.value}

    return mini


def test_rbac_enforced_when_auth_on(monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: Settings(require_auth=True))
    c = TestClient(_mini())
    assert c.get("/protected").status_code == 401
    ro = {"Authorization": "Bearer " + create_token("u", Role.READ_ONLY)}
    assert c.get("/protected", headers=ro).status_code == 403
    an = {"Authorization": "Bearer " + create_token("u", Role.ANALYST)}
    r = c.get("/protected", headers=an)
    assert r.status_code == 200 and r.json()["role"] == "ANALYST"


def test_rbac_open_when_auth_off(monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: Settings(require_auth=False))
    assert TestClient(_mini()).get("/protected").status_code == 200
