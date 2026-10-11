"""C9 probe: does GET /recommendations (and the assistant context) write?"""
import os
import sys

DB = "./.probe_c9.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DB}")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul/tests")

import sqlite3  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from _cards import all_cards  # noqa: E402


def _p(tk, qty, price=100.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": price, "expected_return_pct": 8, "volatility_pct": 20}


def _rules():
    con = sqlite3.connect(DB)
    out = con.execute("select substr(id,1,8), ticker, rule_type, level, active, triggered "
                      "from trading_rules order by ticker").fetchall()
    ev = con.execute("select rule_id, outcome from rule_events").fetchall()
    con.close()
    return out, ev


def _sql(q):
    con = sqlite3.connect(DB)
    con.execute(q)
    con.commit()
    con.close()


def test_probe():
    try:
        os.remove(DB)
    except OSError:
        pass
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("V", 100), _p("MSFT", 100), _p("SCHD", 100), _p("QQQ", 100), _p("VTI", 100)]})
        ids = {}
        for tk, rt, lvl in (("V", "stop_loss", 150.0), ("MSFT", "take_profit", 50.0),
                            ("NVDA", "stop_loss", 150.0), ("AMZN", "buy_dip", 90.0), ("GOOGL", "price_below", 90.0), ("SCHD", "trailing_stop", 10.0)):
            r = c.post("/api/v1/rules", json={"ticker": tk, "rule_type": rt, "mode": "price",
                                              "level": lvl}).json()
            ids[tk] = r.get("id")
            print("create", tk, rt, r.get("ok"), r.get("detail"))
        # Latch every rule as fired (what the 30-minute evaluator does).
        _sql("update trading_rules set triggered = 1")
        print("RULES t0", _rules())

        b1 = c.get("/api/v1/recommendations").json()
        rule_cards = [x["id"] for x in all_cards(b1) if str(x.get("id", "")).startswith("rule_")]
        print("GET#1 rule cards", rule_cards, "banner", b1.get("rule_banner"))
        print("RULES after GET#1", _rules())

        # The user dismisses the V card; the NEXT read resolves the rule.
        vcard = next((x for x in rule_cards if x[5:] == str(ids["V"])[:8]), None)
        if vcard:
            print("dismiss", c.post(f"/api/v1/recommendations/{vcard}/dismiss").status_code)
            print("RULES after dismiss (before GET)", _rules())
        b2 = c.get("/api/v1/recommendations").json()
        print("GET#2 banner", b2.get("rule_banner"))
        print("RULES after GET#2", _rules())
