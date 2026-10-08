import json

from fastapi.testclient import TestClient

from conftest import tok

SA = None


def _client():
    from app.main import app
    return TestClient(app)


def test_c7_weekly_feed_demo_fallback():
    h = tok("READ_ONLY")
    with _client() as c:
        r = c.get("/api/v1/decision-feed/weekly", headers=h)
        j = r.json()
        print("\nC7 EMPTY BOOK", r.status_code, "keys=", list(j.keys()))
        print("C7 tickers", [i["ticker"] for i in j["items"]])
        print("C7 any 'demo'/'sample'/'illustrative' in body:",
              any(w in r.text.lower() for w in ("demo", "sample", "illustrative", "fallback")))
        # Real book whose rows have no spot/listing meta (the /portfolio/add path)
        r0 = c.post("/api/v1/portfolio/add", headers=tok("SUPERADMIN"),
                    json={"ticker": "VOO", "market": "NYSE", "amount": 5000})
        print("C7 seed /portfolio/add", r0.status_code, r0.text[:200])
        r2 = c.get("/api/v1/decision-feed/weekly", headers=h)
        print("C7 REAL BOOK tickers", [i["ticker"] for i in r2.json()["items"]])
        print("C7 portfolio tickers", [p.get("ticker") for p in c.get("/api/v1/portfolio", headers=h).json().get("positions", [])])
        c.post("/api/v1/intake/portfolio", headers=tok("SUPERADMIN"), json={"entity_name": "Personal",
            "positions": [{"ticker": "AAPL", "market": "NYSE", "depth": 1, "spot_price": 200,
                           "listing_price": 200, "quantity": 5, "cost_basis": 150}]})
        r3 = c.get("/api/v1/decision-feed/weekly", headers=h)
        print("C7 BOOK WITH INTAKE ROW (spot==listing, no lag) tickers", [i["ticker"] for i in r3.json()["items"]])


def test_c8_ask_returns_llm_text_unmodified(monkeypatch):
    from app.services import ask_service, digest_service
    from app.services.context_service import gather
    invented = "Your portfolio returned 37.4% last year and TEVA will hit 412 by March. Buy more."
    monkeypatch.setattr(ask_service, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ask_service, "gemini_generate_ex", lambda prompt: (invented, None))
    with _client() as c:
        c.post("/api/v1/intake/portfolio", headers=tok("SUPERADMIN"), json={
            "entity_name": "Personal",
            "positions": [{"ticker": "VOO", "market": "NYSE", "depth": 1, "spot_price": 500,
                           "listing_price": 500, "quantity": 10, "cost_basis": 400}]})
        r = c.post("/api/v1/ask", headers=tok("READ_ONLY"), json={"question": "how am I doing?"})
        j = r.json()
        print("\nC8 /ask", r.status_code, json.dumps({k: j[k] for k in ("llm", "answer")}))
        print("C8 answer == stub:", j["answer"] == invented,
              "| has disclaimer:", "Not financial advice" in j["answer"])

    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.services.feed_service import ensure_user

    async def run():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        async with async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)() as s:
            u = await ensure_user(s, "eran.ganot@gmail.com", name="e", role="SUPERADMIN")
            ctx = await gather(s, u)
        await eng.dispose()
        return ctx
    ctx = asyncio.run(run())
    print("C8 digest_service.unsupported_numbers(same text, same ctx) ->",
          digest_service.unsupported_numbers(invented, ctx))


def test_s2_token_survives_logout():
    h = tok("SUPERADMIN")
    with _client() as c:
        a = c.get("/api/v1/auth/me", headers=h)
        c.cookies.set("iw_session", h["Authorization"].split(" ", 1)[1])
        lo = c.get("/auth/logout", follow_redirects=False)
        print("\nS2 before", a.status_code, a.json(),
              "| logout", lo.status_code, lo.headers.get("location"), lo.headers.get("set-cookie"))
        b = c.get("/api/v1/auth/me", headers=h)
        print("S2 same bearer after logout", b.status_code, b.json())
        from fastapi.testclient import TestClient as TC

        from app.main import app
        with TC(app) as c2:
            c2.cookies.set("iw_session", h["Authorization"].split(" ", 1)[1])
            print("S2 replayed cookie after logout", c2.get("/api/v1/portfolio").status_code)


def test_s3_unsubscribe_not_scoped():
    # m2m-style subject for victim is not allow-listed-email so use two subjects:
    # victim = allow-listed email, attacker = m2m subject (no '@', allowlist exempt)
    victim = tok("SUPERADMIN", "eran.ganot@gmail.com")
    attacker = tok("READ_ONLY", "m2m:other-client")
    ep = "https://fcm.googleapis.com/fcm/send/victim-device-123"
    with _client() as c:
        s = c.post("/api/v1/push/subscribe", headers=victim,
                   json={"endpoint": ep, "keys": {"p256dh": "k", "auth": "a"}})
        st = c.get("/api/v1/push/status", headers=victim).json()
        print("\nS3 victim subscribe", s.status_code, "| victim status subs:",
              json.dumps({k: v for k, v in st.items() if "sub" in k.lower()})[:300])
        u = c.post("/api/v1/push/unsubscribe", headers=attacker, json={"endpoint": ep})
        print("S3 attacker (other subject) unsubscribe", u.status_code, u.text)
        st2 = c.get("/api/v1/push/status", headers=victim).json()
        print("S3 victim status after:", json.dumps({k: v for k, v in st2.items() if "sub" in k.lower()})[:300])
