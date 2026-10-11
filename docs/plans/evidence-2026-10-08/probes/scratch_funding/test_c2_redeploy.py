"""C2 probe: redeploy_cash card claim vs simulated post-trade mix (book target)."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.probe_c2r.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul/tests")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from _cards import all_cards  # noqa: E402


def _p(tk, cls, qty, price=100.0, basis=99.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _drift(mix, tgt):
    ks = set(mix) | set(tgt)
    return round(sum(abs(mix.get(k, 0) - tgt.get(k, 0)) for k in ks), 4)


def test_probe():
    try:
        os.remove("./.probe_c2r.db")
    except OSError:
        pass
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SOXL", "Equities", 100), _p("V", "Equities", 175), _p("SCHD", "Equities", 175),
            _p("MSFT", "Equities", 175), _p("VTI", "Equities", 175), _p("BND", "Fixed Income", 200)]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 60000})
        c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"})
        c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=10")
        mx = c.get("/api/v1/mix").json()
        tgt = mx["target_allocation"]
        print("BEFORE", mx["current_allocation"], "target", tgt, "drift", _drift(mx["current_allocation"], tgt))
        body = c.get("/api/v1/recommendations").json()
        card = next((x for x in all_cards(body) if (x.get("apply") or {}).get("kind") == "redeploy_cash"), None)
        if not card:
            print("no redeploy card", [x.get("title") for x in all_cards(body)])
            return
        print("TITLE", card["title"])
        print("IMPACT", card["impact"])
        for h in card["how"]:
            print("  HOW", h)
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
        print("ACCEPT", r.status_code)
        mx2 = c.get("/api/v1/mix").json()
        print("AFTER", mx2["current_allocation"], "drift", _drift(mx2["current_allocation"], tgt))
