"""C2 probe: the sleeve-drift trim card sizes shares as ILS / native price."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.probe_c2.db")
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


def _soxl(c):
    pf = c.get("/api/v1/portfolio").json()
    for h in pf.get("holdings") or pf.get("positions") or []:
        if (h.get("ticker") or "").upper() == "SOXL":
            return h
    return None


def test_c2_probe():
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SOXL", "Equities", 300), _p("V", "Equities", 175), _p("SCHD", "Equities", 175),
            _p("MSFT", "Equities", 175), _p("VTI", "Equities", 175)]})
        print("apply", c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=25").json().get("ok"))
        print("SOXL before", {k: _soxl(c).get(k) for k in ("quantity", "value_ils", "weight", "current_price")})
        body = c.get("/api/v1/recommendations").json()
        nav = body.get("nav") or body.get("snapshot", {}).get("nav")
        print("nav", nav)
        card = next((x for x in all_cards(body) if str(x.get("id", "")).startswith("rec_sleeve_drift")
                     or "drifted to" in str(x.get("title", ""))), None)
        if card is None:
            print("no drift card; titles:", [x.get("title") for x in all_cards(body)])
            return
        print("TITLE", card["title"])
        print("ACTION", card["action"])
        print("APPLY", card["apply"], "est_amount", card.get("est_amount"))
        r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
        print("ACCEPT", {k: r.get(k) for k in ("ok", "detail")})
        print("SOXL after", (_soxl(c) or {}).get("quantity"))
