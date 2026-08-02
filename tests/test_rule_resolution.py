"""Triggered rules must clear once the user has dealt with them.

Reported after phase 2 shipped: "4 trading rules triggered: CASH, MSFT, META,
META - I already took actions". Two distinct bugs behind one banner:

  1. `triggered` latches True and only price alerts ever reset it, so acting on
     a card never cleared the rule. The banner reads the rules directly, so it
     kept counting work already done.
  2. CASH was in the rule position index at all, so the suggester offered stops
     on a cash balance. When the pricing fix reset the corrupted CASH row from
     ~72.9 back to its true 1.0, those stops "crashed" and fired.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.rules_service import _ONE_SHOT  # noqa: E402


def test_exit_orders_are_one_shot_and_caps_are_standing():
    """A fired stop is spent; a weight cap is a standing constraint."""
    for rt in ("stop_loss", "take_profit", "trailing_stop", "buy_dip"):
        assert rt in _ONE_SHOT, rt
    for rt in ("max_weight", "price_above", "price_below"):
        assert rt not in _ONE_SHOT, rt


def test_cash_is_excluded_from_the_rule_position_index(monkeypatch):
    """No stop-loss on your bank balance.

    Uses monkeypatch rather than assigning the module globals directly: a bare
    assignment leaks into every test that runs afterwards, which silently starved
    test_trading_rule_suggestions of its holdings.
    """
    import asyncio

    from app.services import rules_service

    rows = [{"ticker": "CASH", "market": "TASE", "quantity": 1934.52,
             "cost_basis": 1.0, "current_price": 1.0},
            {"ticker": "META", "market": "NASDAQ", "quantity": 12,
             "cost_basis": 600.0, "current_price": 556.71}]

    async def _fake_load(_s, _u):
        return rows

    monkeypatch.setattr(rules_service, "load_positions", _fake_load)
    monkeypatch.setattr(rules_service, "compute_snapshot",
                        lambda _r: {"nav": 1.0, "exposure_ticker": {"META": 1.0}})

    idx = asyncio.run(rules_service._positions_index(None, None))
    assert "CASH" not in idx, "cash must never be treated as a tradeable holding"
    assert "META" in idx
    assert idx["META"]["qty"] == 12
