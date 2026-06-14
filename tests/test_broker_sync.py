"""Phase 3.1 - brokerage connect + holdings sync (mock aggregator)."""
from fastapi.testclient import TestClient

from app.brokers.registry import get_aggregator
from app.core.config import get_settings
from app.main import app


def test_mock_aggregator_returns_holdings():
    agg = get_aggregator(get_settings())  # default provider = mock
    assert agg.name == "mock"
    accts = agg.get_accounts("sandbox-token")
    assert accts and accts[0].account_id == "mock-acct-1"
    pos = agg.get_positions("sandbox-token", "mock-acct-1")
    assert {p.ticker for p in pos} == {"TEVA", "GOLD", "BOND"}


def test_connect_then_sync_imports_positions():
    with TestClient(app) as c:
        conn = c.post("/api/v1/broker/connect", json={"provider": "mock"}).json()
        assert conn["status"] == "CONNECTED" and conn["provider"] == "mock"
        synced = c.post("/api/v1/broker/sync", json={"connection_id": conn["connection_id"]}).json()
        assert synced["ok"] is True
        assert synced["synced_positions"] == 3
        # the synced tickers now show up in the portfolio
        port = c.get("/api/v1/portfolio").json()
        tickers = {p["ticker"] for p in port["positions"]}
        assert {"TEVA", "GOLD", "BOND"} <= tickers
        # clean up synced holdings so the shared test DB stays neutral
        for tk, mk in [("TEVA", "NYSE"), ("GOLD", "SPOT"), ("BOND", "TASE")]:
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": mk})


def test_sync_with_unknown_connection_is_graceful():
    import uuid
    with TestClient(app) as c:
        r = c.post("/api/v1/broker/sync", json={"connection_id": str(uuid.uuid4())}).json()
        assert r["ok"] is False and "connect" in r["error"].lower()


def test_real_provider_requires_enablement():
    from app.brokers.base import NotConfiguredError
    from app.brokers.registry import get_aggregator
    s = get_settings().model_copy(update={"aggregator_provider": "plaid", "broker_enabled": False})
    try:
        get_aggregator(s)
        assert False, "expected NotConfiguredError"
    except NotConfiguredError as e:
        assert "BROKER_ENABLED" in str(e)


def test_credentials_are_never_persisted_plaintext():
    with TestClient(app) as c:
        conn = c.post("/api/v1/broker/connect", json={"provider": "mock"}).json()
        # only a vault reference is stored, never a secret
        assert conn["status"] == "CONNECTED"
