"""C10 probe: Today's risk-off card reads the futures regime; strategies read engines/regime."""
import os
import sys

DB = "./.probe_c10.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DB}")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul/tests")

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from _cards import all_cards  # noqa: E402


def _p(tk, qty, price=100.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": price, "expected_return_pct": 8, "volatility_pct": 20}


def test_probe():
    from app.engines import regime as rg
    from app.services import markets_service as ms

    # A calm, steady uptrend: every index above its 200-day, realized vol flat.
    n = 800
    rng = np.random.default_rng(0)
    px = {t: 100 * np.cumprod(1 + 0.0006 + rng.normal(0, 0.006, n)) for t in rg.tickers_needed()}
    print("engines/regime.latest ->", {k: v for k, v in rg.latest(px).items() if k in ("state", "score")})

    # One red morning in futures: VIX 22.5, equity futures -0.4%.
    items = [{"symbol": "^VIX", "price": 22.5, "change_pct": 3.0},
             {"symbol": "ES=F", "price": 1, "change_pct": -0.4},
             {"symbol": "NQ=F", "price": 1, "change_pct": -0.5},
             {"symbol": "YM=F", "price": 1, "change_pct": -0.3}]
    mk = ms._regime(items)
    print("markets_service._regime ->", {k: mk[k] for k in ("regime", "score", "rationale")})

    # Written to the cache three DAYS ago, far past the 300 s TTL.
    import time
    ms._CACHE.update(ts=time.time() - 3 * 86400, data={"as_of": "3 days ago", "futures": items,
                                                       "market": mk})
    print("cached_regime() with a 3-day-old cache ->", ms.cached_regime().get("regime"))

    try:
        os.remove(DB)
    except OSError:
        pass
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("V", 100), _p("MSFT", 100), _p("SCHD", 100), _p("QQQ", 100)]})
        body = c.get("/api/v1/recommendations").json()
        mac = [x for x in all_cards(body) if "risk-off" in str(x.get("title", "")).lower()]
        for x in mac:
            print("CARD", x["title"], "|", x.get("why"), "|", x.get("how"))
        ms._CACHE.update(ts=0.0, data=None)
        body = c.get("/api/v1/recommendations").json()
        print("cold cache -> risk-off cards:",
              [x["title"] for x in all_cards(body) if "risk-off" in str(x.get("title", "")).lower()])
