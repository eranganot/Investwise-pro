"""P2 scratch: what happens to a trade between the card minimum (100) and the funded-leg minimum (250)."""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import funding_service as fs  # noqa: E402
from app.services import headsup  # noqa: E402


def _pos(tk, qty, px, cls):
    return NS(ticker=tk, market="TASE", current_price=px, quantity=qty, cost_basis=px,
              meta={"asset_class": cls, "currency": "ILS"}, asset_class=cls)


def _book(com_value):
    nav = 20_000.0
    rows = [_pos("EQ1", 1.0, nav - com_value, "Equities"), _pos("COM1", 1.0, com_value, "Commodities")]
    return rows, {"nav": nav}


TARGET = {"Equities": 0.90, "Commodities": 0.10}


def _buy(gap):
    rows, snap = _book(2000.0 - gap)
    return fs.propose_funded_buy(rows=rows, snap=snap, plan=None, objective="Grow", cap=0.95,
                                 ticker="COM2", buying_class="Commodities", cash_ils=0.0,
                                 requested_ils=gap, market="TASE", book_target=TARGET)


def test_floors():
    print("\ncard floor at NAV 20k:", headsup.min_trade_ils(20_000), " at NAV 35k:", headsup.min_trade_ils(35_000),
          " at NAV 60k:", headsup.min_trade_ils(60_000), " funded floor:", fs.min_trade_ils())


def test_gap_180_funded_buy_refused():
    b = _buy(180.0)
    print("gap 180 ->", None if b is None else (b.amount_ils, b.kind if hasattr(b, 'kind') else ''))
    assert b is None


def test_gap_400_control_is_funded():
    b = _buy(400.0)
    print("gap 400 ->", None if b is None else round(b.amount_ils, 2))
    assert b is not None


def test_a_180_card_without_apply_becomes_headsup_and_with_apply_stays_card():
    # The commodity card's fallback when propose_funded_buy refuses: apply kind "none".
    no_apply = {"id": "commodity_add_x", "dimension": "diversification", "action": "Buy 180 of COM2.",
                "est_amount": 180.0, "apply": {"kind": "none"}}
    # A hypothetical funded card at 180 (what the card floor alone would allow).
    with_apply = {"id": "buy_x", "dimension": "allocation", "action": "Buy 180 of COM2.",
                  "est_amount": 180.0, "apply": {"kind": "buy_funded"}}
    cards, heads, dropped = headsup.split([no_apply, with_apply], nav=20_000,
                                          actionable=lambda c: (c.get("apply") or {}).get("kind") != "none")
    print("cards:", [c["id"] for c in cards], " heads-ups:", [h["id"] for h in heads])
    assert [h["id"] for h in heads] == ["commodity_add_x"]
    assert [c["id"] for c in cards] == ["buy_x"]
