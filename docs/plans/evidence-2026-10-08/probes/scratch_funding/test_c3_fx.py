"""C3 probe: native-currency valuations reaching the assistant context, risk_validation, replace."""
import os
import sys

DB = "./.probe_c3.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DB}")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _p(tk, market, cls, qty, price=100.0):
    return {"ticker": tk, "market": market, "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": price, "expected_return_pct": 8, "volatility_pct": 20}


async def _ctx():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.engines.backtest_engine import ASSET_CLASS_BETA
    from app.models.tables import User
    from app.services import context_service as cs
    from app.services.intake_service import list_positions
    from app.services.allocation_mix import current_mix

    eng = create_async_engine(f"sqlite+aiosqlite:///{DB}", poolclass=NullPool)
    mk = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with mk() as s:
        u = (await s.execute(select(User))).scalars().first()
        ctx = await cs.gather(s, u)
        rows = await list_positions(s, u)
        mix, nav = current_mix(rows)
        print("CTX nav_ils", ctx["nav_ils"])
        print("CTX holdings", [(h["ticker"], h["value_ils"], h["asset_class"]) for h in ctx["holdings"]])
        print("CTX asset_class_breakdown", ctx["asset_class_breakdown"])
        print("current_mix (FX-normalised)", {k: round(v, 3) for k, v in mix.items()}, "nav", nav)
        print("CTX beta_validation", {k: (ctx.get("beta_validation") or {}).get(k)
                                     for k in ("structural_beta", "worst_portfolio_drawdown_pct")})
        exp = sum(w * ASSET_CLASS_BETA.get(c, 1.0) for c, w in mix.items())
        print("expected structural beta from FX-normalised mix", round(exp, 4), "betas", ASSET_CLASS_BETA)
    await eng.dispose()


def test_c3_probe():
    import asyncio
    try:
        os.remove(DB)
    except OSError:
        pass
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Balanced"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SPY", "NASDAQ", "Equities", 100), _p("GOVIL.TA", "TASE", "Fixed Income", 371)]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 10000})
    asyncio.run(_ctx())


def test_c3_replace_book():
    try:
        os.remove(DB)
    except OSError:
        pass
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SPY", "NASDAQ", "Equities", 100)]})
        nav0 = c.get("/api/v1/mix").json()
        print("BEFORE replace cash_ils", nav0.get("cash_ils"), "current", nav0.get("current_allocation"))
        pv = c.post("/api/v1/strategies/grow_quality/load-basket",
                    json={"mode": "replace", "dry_run": True}).json()
        print("PREVIEW budget", pv.get("budget"), "removing_value_ils", pv.get("removing_value_ils"))
        for x in pv.get("loaded") or []:
            print("  LOAD", x)
        r = c.post("/api/v1/strategies/grow_quality/load-basket",
                   json={"mode": "replace", "dry_run": False}).json()
        print("REPLACE ok", r.get("ok"), r.get("error"))
        pf = c.get("/api/v1/portfolio").json()
        tot = 0.0
        for h in pf.get("holdings") or pf.get("positions") or []:
            print("  HELD", h.get("ticker"), h.get("quantity"), h.get("current_price"), h.get("value_ils"))
            tot += float(h.get("value_ils") or 0)
        print("AFTER total value_ils", round(tot, 2), "nav field", pf.get("nav") or pf.get("nav_ils"))
