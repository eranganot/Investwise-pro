"""PR 110: authorisation and honesty at the edges (C6, C7b, C8, S1, S3, S4, S5, L15).

Evidence: docs/plans/evidence-2026-10-08/inv-security.md (probes in scratch_sec/).
D74 (owner, 2026-10-11): the owner (SUPERADMIN) can do everything; tightening a
route to ANALYST only stops a READ_ONLY token from writing.
"""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as m
from app.core import auth as A
from app.core.auth import Role, create_token
from app.core.config import get_settings

# POSTs that compute and write nothing: a READ_ONLY token may call them.
COMPUTE_ONLY = {
    ("POST", "/api/v1/ask"), ("POST", "/api/v1/ai/holding/{ticker}/research"),
    ("POST", "/api/v1/plan/frontier/preview"),
    ("POST", "/api/v1/plan/frontier/rule-impact"),   # PR 124: read-only replay, nothing saved
    ("POST", "/api/v1/scenario"), ("POST", "/api/v1/whatif"),
}
# Unauthenticated on purpose. Every addition is a decision -- this list is the review.
PUBLIC = {
    "/", "/health", "/health/ready", "/metrics", "/login", "/dashboard", "/dashboard/",
    "/auth/google/login", "/auth/google/callback", "/auth/logout",
    "/api/v1/auth/token", "/api/v1/auth/refresh", "/api/v1/auth/status",
    "/api/v1/push/public-key", "/api/v1/intake/template.csv", "/api/v1/commodities",
    "/api/v1/strategies", "/api/v1/strategies/backtests", "/api/v1/scenario/supported",
    "/api/v1/data-status", "/api/v1/markets/futures",
}
ORDER = ["SUPERADMIN", "ADVISOR", "ANALYST", "READ_ONLY"]


def _level(dependant, acc=None):
    from app.api.deps import acting_user
    acc = set() if acc is None else acc
    call = dependant.call
    if call is acting_user:
        acc.add("READ_ONLY")
    if call is not None and getattr(call, "__name__", "") == "_dep" and call.__closure__:
        for cell in call.__closure__:
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, Role):
                acc.add(v.value)
    for d in dependant.dependencies:
        _level(d, acc)
    return next((r for r in ORDER if r in acc), "NONE")


def _routes():
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield meth, r.path, _level(r.dependant)


# ---- S1 / S4: the class-closers ------------------------------------------------
def test_every_write_route_is_analyst_or_above():
    bad = [(mt, p, lv) for mt, p, lv in _routes()
           if mt in ("POST", "PUT", "PATCH", "DELETE") and lv in ("READ_ONLY", "NONE")
           and (mt, p) not in COMPUTE_ONLY and p not in PUBLIC]
    # On HEAD: 18 writers (accept, position PUT/DELETE, rules, push, broker...).
    assert not bad, "a write route a READ_ONLY token can call:\n  " + "\n  ".join(map(str, bad))


def test_no_new_route_is_public_by_accident():
    bad = [(mt, p) for mt, p, lv in _routes() if lv == "NONE" and p not in PUBLIC]
    # On HEAD: /providers/health, /adversary/diagnostics, /markets/summary.
    assert not bad, "unauthenticated route not on the PUBLIC allowlist:\n  " + "\n  ".join(map(str, bad))


@pytest.fixture
def enforced(monkeypatch):
    from app.api import deps
    s = get_settings().model_copy(update={"require_auth": True, "allowed_emails": "", "agent_api_key": ""})
    monkeypatch.setattr(A, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    return lambda role, sub="eran.ganot@gmail.com": {
        "Authorization": "Bearer " + create_token(sub, Role(role), "access")}


def test_read_only_cannot_write_and_the_owner_can_do_everything(enforced):
    ro, owner = enforced("READ_ONLY"), enforced("SUPERADMIN")
    with TestClient(m.app) as c:
        for meth, path in (("POST", "/api/v1/recommendations/rec_x/accept"),
                           ("DELETE", "/api/v1/portfolio/position?ticker=X&market=NASDAQ"),
                           ("POST", "/api/v1/rules/evaluate"), ("POST", "/api/v1/push/test")):
            assert c.request(meth, path, headers=ro).status_code == 403, (meth, path)
            assert c.request(meth, path, headers=owner).status_code not in (401, 403), (meth, path)
        for path in ("/api/v1/providers/health", "/api/v1/adversary/diagnostics", "/api/v1/markets/summary"):
            assert c.get(path).status_code == 401, path
        assert c.get("/api/v1/providers/health", headers=owner).status_code == 200


def test_the_api_map_is_not_served_in_production(monkeypatch):
    monkeypatch.setattr(m, "settings", m.settings.model_copy(update={"environment": "production"}))
    app = m.create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert not ({"/docs", "/redoc", "/openapi.json"} & paths)


# ---- C7b -------------------------------------------------------------------------
def test_whatif_on_an_empty_book_recommends_nothing_it_does_not_hold():
    assert get_settings().demo_signals is False
    with TestClient(m.app) as c:
        r = c.post("/api/v1/whatif", json={}).json()
    # On HEAD: ['Buy TEVA (NYSE)', 'Rebalance GOLD (SPOT)'], unlabelled.
    assert r["risk_profile"]["recommended_titles"] == [] and r["grounded"] is False, r["risk_profile"]


# ---- C8 --------------------------------------------------------------------------
def test_ask_never_shows_an_invented_number(monkeypatch):
    from app.services import ask_service
    monkeypatch.setattr(ask_service, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ask_service, "gemini_generate_ex", lambda p: (
        "Your portfolio returned 37.4% last year and TEVA will hit 412 by March. Buy more.", None))
    with TestClient(m.app) as c:
        r = c.post("/api/v1/ask", json={"question": "how am I doing?"}).json()
    assert r["verified"] is False and 37.4 in r["unsupported_numbers"], r
    assert "37.4" not in r["answer"] and r["answer"].endswith("Not financial advice.")


def test_ask_keeps_a_grounded_answer_and_adds_the_disclaimer(monkeypatch):
    from app.services import ask_service
    monkeypatch.setattr(ask_service, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ask_service, "gemini_generate_ex",
                        lambda p: ("Buying 50 shares is a sizeable step; weigh it against your mix.", None))
    with TestClient(m.app) as c:
        r = c.post("/api/v1/ask", json={"question": "should I buy 50 shares of VTI?"}).json()
    assert r["verified"] is True and "50 shares" in r["answer"]
    assert r["answer"].endswith("Not financial advice.")


# ---- S3 --------------------------------------------------------------------------
def test_another_principal_cannot_silence_or_steal_the_owners_device(enforced):
    owner, other = enforced("ANALYST"), enforced("ANALYST", sub="m2m:other-client")
    sub = {"endpoint": "https://push.example/device-1", "keys": {"p256dh": "k", "auth": "a"}}
    with TestClient(m.app) as c:
        assert c.post("/api/v1/push/subscribe", json=sub, headers=owner).status_code == 200
        r = c.post("/api/v1/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers=other).json()
        # On HEAD: {"ok": true} and the owner's device is gone.
        assert r["removed"] == 0
        assert c.post("/api/v1/push/subscribe", json=sub, headers=other).status_code == 403
        assert c.get("/api/v1/push/status", headers=owner).json()["subscriptions"] == 1


# ---- L15 -------------------------------------------------------------------------
def test_grounded_research_on_429_calls_once_and_says_billing(monkeypatch):
    import urllib.error
    import urllib.request

    from app.services import llm
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests: prepayment credits are depleted",
                                     {}, None)
    monkeypatch.setattr(llm, "gemini_key", lambda: "k")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res, err = llm.gemini_generate_grounded_ex("research TEVA", timeout=2)
    # On HEAD: a second, ungrounded call into the same exhausted quota, then None.
    assert res is None and len(calls) == 1 and "billing" in err, (len(calls), err)


def test_research_labels_prices_in_their_own_currency(monkeypatch):
    from app.services import ai_service
    seen = {}

    def fake(prompt, **k):
        seen["p"] = prompt
        return {"text": "ok", "sources": []}, None
    monkeypatch.setattr(ai_service, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ai_service, "gemini_generate_grounded_ex", fake)
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "SPY", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
             "spot_price": 600.0, "listing_price": 600.0, "quantity": 2, "cost_basis": 500.0}]})
        r = c.post("/api/v1/ai/holding/SPY/research").json()
    assert "600.0 USD" in seen["p"] and "₪600" not in seen["p"]
    assert r["research"].lower().endswith("not financial advice.")


def test_summaries_drop_an_invented_number(monkeypatch):
    from app.services import ai_service
    monkeypatch.setattr(ai_service, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ai_service, "gemini_generate_ex", lambda p: ("SPY will reach 912 next week.", None))
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "SPY", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
             "spot_price": 600.0, "listing_price": 600.0, "quantity": 2, "cost_basis": 500.0}]})
        r = c.get("/api/v1/ai/holding/SPY/summary").json()
    assert r["llm"] is False and "912" not in r["summary"], r
    _ = NS  # keep the import for readers of this file's other tests
