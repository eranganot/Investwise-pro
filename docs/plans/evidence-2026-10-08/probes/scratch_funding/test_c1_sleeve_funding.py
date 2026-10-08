"""C1 probe: sleeve funding measures 'overweight' against OBJ_TARGET, not the book target."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.probe_c1.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _p(tk, cls, qty, price=100.0, basis=99.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def test_c1_probe():
    with TestClient(m.app) as c:
        r = c.put("/api/v1/plan", json={"objective": "Grow"})
        print("PUT plan", r.status_code, r.json().get("objective"))
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("V", "Equities", 200, basis=40.0), _p("SCHD", "Equities", 200, basis=40.0),
            _p("MSFT", "Equities", 200, basis=40.0), _p("VTI", "Equities", 200, basis=40.0),
            _p("BND", "Fixed Income", 200, basis=99.0)]})
        print("core", c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"}).status_code)
        ap = c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=10").json()
        print("apply ok", ap.get("ok"), ap.get("error"))
        mix = c.get("/api/v1/mix").json()
        print("MIX current", mix.get("current_allocation"))
        print("MIX book target", mix.get("target_allocation"), "source", mix.get("target_source"))
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()
        f = res.get("funding") or {}
        print("FUND amount", res.get("amount_ils"), "from_cash", f.get("from_cash_ils"))
        for s in f.get("sells", []):
            print("  SELL", s["ticker"], s["asset_class"], s["value_ils"], "|", s["reason"])
        print("SUMMARY", res.get("funding_summary"))

        # Contrast: same plan_funding call, with and without the book target.
        import asyncio

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.models.tables import User
        from app.services import funding_service as fs
        from app.services.book_target import book_target
        from app.services.intake_service import list_positions
        from app.services.strategy_service import _snapshot

        async def go():
            eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
            mk = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with mk() as s:
                u = (await s.execute(select(User))).scalars().first()
                rows = await list_positions(s, u)
                snap = _snapshot(rows)
                from app.services.plan_service import get_plan
                plan = await get_plan(s, u)
                bt = (await book_target(s, u, "Grow"))["target"]
                print("book_target()", bt)
                for label, kw in (("OBJ_TARGET", {}), ("book_target", {"book_target": bt})):
                    fd = fs.plan_funding(rows, snap, plan, "Grow", 0.25, 37100.0, cash_ils=0.0,
                                         exclude={"SOXL"}, buying_class="Equities", **kw)
                    print(label, [(x["ticker"], x["value_ils"]) for x in fd["sells"]],
                          "shortfall", fd["shortfall_ils"])
                from app.services import strategy_service as ss
                from app.services import target_solver as ts
                one = await ss._fund_sleeve(s, u, "btm_trend_soxl", {}, total=None,
                                            sleeve_pct=10.0, dry_run=True)
                print("_fund_sleeve sells", [(x["ticker"], x["asset_class"], x["value_ils"])
                                             for x in (one.get("funding") or {}).get("sells", [])])
                prev = await ts._funding_preview(s, u, [{"current_pct": 0.0}], 10.0, snap["nav"])
                print("solver preview", {k: prev.get(k) for k in ("needed_ils", "raised_ils", "shortfall_ils")},
                      [(x["ticker"], x["value_ils"]) for x in (prev.get("legs") or [])])
            await eng.dispose()

        try:
            asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            print("contrast failed:", type(e).__name__, e)
