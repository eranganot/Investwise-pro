"""PR 81 -- four advanced trading rules, applied to real books and re-measured.

Eran, 2026-09-29 (PLAN_2026-09-29_advanced_rules.md): rebalance band,
profit-protect trail, trend-break exit, whole-book drawdown guard. Decisions:
params in `trading_rules.params` (0025); a one-time caps-to-bands card; the
drawdown guard proposes an ACTIONABLE de-risk; defaults band 3 / +20% / 200 /
10%, and "if they need to be changed a recommendation card should appear".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.tables import KVSetting, TradingRule  # noqa: E402
from app.schemas.intake import IntakePosition  # noqa: E402
from app.schemas.state_machine import Market  # noqa: E402
from app.services import advanced_rules as adv  # noqa: E402
from app.services import buyback_hold as bh  # noqa: E402
from app.services import rules_service as rs  # noqa: E402
from app.services import sleeve_service as sv  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ensure_account, ensure_entity, set_cash, upsert_positions)
from app.services.plan_service import upsert_plan  # noqa: E402


class _R:
    """A rule-shaped object for the pure functions."""
    def __init__(self, rule_type, level, ticker="VTI", params=None, peak=None):
        self.rule_type, self.level, self.ticker = rule_type, level, ticker
        self.params, self.peak_price, self.active, self.mode = params, peak, True, "pct"


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(ticker, qty, price, basis, cls):
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=basis, asset_class=cls)


async def _book(s, email, positions, cash=0.0, core="bal_6040"):
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    await upsert_positions(s, account, positions)
    await s.commit()
    if cash:
        await set_cash(s, user, cash)
    await upsert_plan(s, user, objective="Balanced", risk_tolerance="Medium",
                      horizon_years=6, target_amount=100000)
    if core:
        await sv.set_core(s, user, core)
    await s.commit()
    return user


async def _measure(s, tk, *, long="down", short="up", long_edge=False, short_edge=False,
                   vol=20.0):
    from app.services.backtest_service import ENGINE_VERSION
    m = {"ok": True, "price": 100.0, "sma20": 100, "sma50": 100, "sma200": 100,
         "long": long, "short": short, "vol_20d_pct": vol}
    val = {"ticker": tk, "engine_version": ENGINE_VERSION,
           "measured_at": datetime.now(timezone.utc).isoformat(), "metrics": m,
           "long": {"measured": True, "edge": long_edge, "why": "x"},
           "short": {"measured": True, "edge": short_edge, "why": "x"}}
    s.add(KVSetting(key=f"holding_review:{tk}", value=json.dumps(val)))
    await s.commit()


@pytest.fixture
def ils_quotes(monkeypatch):
    """The book is TASE/ILS at 100; the executor must price the buy the same way,
    or the test measures FX, not the rule."""
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _q(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="ILS", market="TASE")
    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _q, raising=False)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_validate_clamps_each_rule_to_what_it_can_mean():
    assert adv.validate("rebalance_band", 3, None) == (3.0, None)
    assert adv.validate("profit_protect", 15, {"activation_pct": 20, "junk": 1}) == (
        15.0, {"activation_pct": 20.0})
    assert adv.validate("trend_break", 200, None) == (200.0, None)
    for bad in [("rebalance_band", 40), ("profit_protect", 90), ("trend_break", 100),
                ("drawdown_guard", 80)]:
        with pytest.raises(ValueError):
            adv.validate(bad[0], bad[1], None)


# --------------------------------------------------------------------------- #
# Rebalance band (pure)
# --------------------------------------------------------------------------- #
def test_a_band_trims_back_to_the_target_not_to_the_edge():
    r = _R("rebalance_band", 3)
    pos = {"price": 100, "qty": 250, "weight_pct": 25.0, "value_ils": 25000, "nav": 100000,
           "plan_target_pct": 18.0}
    hit, title, _a, t = adv.evaluate(r, pos)
    assert hit and "above" in title and t == 18.0
    plan = adv.execution_plan(r, pos)
    assert plan["kind"] == "trim"
    assert plan["shares"] == pytest.approx(250 * (25 - 18) / 25)   # to 18%, not 21%
    # below the band: fires, but the BUY is built elsewhere (it needs funding)
    below = {**pos, "weight_pct": 12.0}
    assert adv.evaluate(r, below)[0] and adv.execution_plan(r, below) is None
    # inside the band: nothing, and it clears only well inside
    assert not adv.evaluate(r, {**pos, "weight_pct": 20.0})[0]
    assert adv.clears(r, {**pos, "weight_pct": 19.0})


def test_a_band_breach_too_small_to_trade_does_not_fire():
    r = _R("rebalance_band", 0.5)
    pos = {"price": 100, "qty": 10, "weight_pct": 19.0, "value_ils": 190, "nav": 1000,
           "plan_target_pct": 18.0}
    assert not adv.evaluate(r, pos)[0], "a ₪10 trade is below MIN_TRADE_ILS"


# --------------------------------------------------------------------------- #
# Profit-protect (pure + evaluator)
# --------------------------------------------------------------------------- #
def test_profit_protect_is_dormant_until_armed_then_trails_the_peak():
    r = _R("profit_protect", 15, params={"activation_pct": 20})
    assert not adv.evaluate(r, {"price": 110, "cost": 100})[0]
    r.params = {"activation_pct": 20, "activated": True}
    r.peak_price = 150.0
    assert not adv.evaluate(r, {"price": 130, "cost": 100})[0]          # 13% off the peak
    hit, _t, _a, stop = adv.evaluate(r, {"price": 127, "cost": 100})
    assert hit and stop == pytest.approx(127.5)
    assert adv.execution_plan(r, {"qty": 7, "price": 127})["kind"] == "sell_position"


@pytest.mark.asyncio
async def test_the_evaluator_arms_profit_protect_and_ratchets_the_peak():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pp@example.com", [_pos("VTI", 10, 125.0, 100.0, "Equities")])
            r = await rs.create_rule(s, user, ticker="VTI", rule_type="profit_protect",
                                     mode="pct", level=15, params={"activation_pct": 20})
            await rs.evaluate_user(s, user)
            r = await s.get(TradingRule, r.id)
            assert r.params["activated"] is True and r.peak_price == 125.0
            assert not r.triggered
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Trend break
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_trend_break_sells_only_with_a_measured_edge():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "tb@example.com", [_pos("QQQ", 10, 100.0, 90.0, "Equities")],
                               core=None)
            await _measure(s, "QQQ", long="down", long_edge=True)
            r = await rs.create_rule(s, user, ticker="QQQ", rule_type="trend_break",
                                     mode="pct", level=200)
            fired = await rs.evaluate_user(s, user)
            assert [f for f in fired if f["ticker"] == "QQQ"], "a fresh 'down' must fire"
            cards = await rs.triggered_rule_recs(s, user)
            c = next(c for c in cards if c["id"] == f"rule_{str(r.id)[:8]}")
            assert c["apply"]["kind"] == "sell_position"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_trend_break_without_an_edge_arms_a_stop_instead():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "tb2@example.com", [_pos("QQQ", 10, 100.0, 90.0, "Equities")],
                               core=None)
            await _measure(s, "QQQ", long="down", long_edge=False)
            r = await rs.create_rule(s, user, ticker="QQQ", rule_type="trend_break",
                                     mode="pct", level=200)
            await rs.evaluate_user(s, user)
            assert not [c for c in await rs.triggered_rule_recs(s, user)
                        if c["id"] == f"rule_{str(r.id)[:8]}"], "no sell card without an edge"
            extra = await adv.extra_cards(s, user, rows=[], snap={}, plan=None,
                                          objective="Balanced", cap=0.4, cash=0.0,
                                          book_target=None, name_caps={}, ledger=None)
            c = next(c for c in extra if c["id"] == f"rule_{str(r.id)[:8]}")
            assert c["apply"]["kind"] == "create_rules"
            assert c["apply"]["rules"][0]["rule_type"] == "stop_loss"
            assert c["apply"]["rules"][0]["level"] < 100.0
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Drawdown guard
# --------------------------------------------------------------------------- #
def test_drawdown_ignores_a_withdrawal():
    pts = [{"as_of": f"2026-09-0{i}", "nav_ils": v, "invested_ils": 0, "positions": 3}
           for i, v in enumerate([100000, 110000, 80000], 1)]
    # 30,000 of the fall on 09-03 was a WITHDRAWAL, so the book lost nothing.
    flows = [{"occurred_at": "2026-09-03", "amount_ils": -30000}]
    dd = adv.drawdown_from(pts, flows, None)
    assert dd["dd_pct"] == pytest.approx(0.0, abs=0.01)
    dd2 = adv.drawdown_from(pts, [], None)
    assert dd2["dd_pct"] == pytest.approx(27.27, abs=0.01)


@pytest.mark.asyncio
async def test_the_drawdown_guard_proposes_an_actionable_de_risk_and_holds_rebuys(ils_quotes):
    from app.models.tables import NavSnapshot
    from app.services.recommendations import apply_recommendation
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "dd@example.com", [
                _pos("SOXL", 100, 50.0, 40.0, "Equities"),
                _pos("VTI", 100, 100.0, 90.0, "Equities"),
                _pos("BND", 100, 100.0, 100.0, "Fixed Income")])
            await _measure(s, "SOXL", vol=90.0, long="up")
            await _measure(s, "VTI", vol=15.0, long="up")
            # the book was 30,000 at its peak and is 25,000 now: 16.7% down
            for d, v in [("2026-09-01", 30000.0), ("2026-09-02", 25000.0)]:
                s.add(NavSnapshot(subject=user.email, as_of=d, nav_ils=v, cash_ils=0,
                                  invested_ils=25000, positions=3))
            await s.commit()
            r = await rs.create_rule(s, user, ticker="anything", rule_type="drawdown_guard",
                                     mode="pct", level=10)
            assert r.ticker == adv.BOOK
            fired = await rs.evaluate_user(s, user)
            assert [f for f in fired if f["ticker"] == adv.BOOK]
            extra = await adv.extra_cards(s, user, rows=[], snap={}, plan=None,
                                          objective="Balanced", cap=0.4, cash=0.0,
                                          book_target=None, name_caps={}, ledger=None)
            c = next(c for c in extra if c["id"] == f"rule_{str(r.id)[:8]}")
            ap = c["apply"]
            assert ap["kind"] == "trim" and ap["ticker"] == "SOXL", "the most volatile holding"
            assert ap["shares"] == pytest.approx(25.0) and ap["hold_until_rule"] == str(r.id)
            assert c["meta"]["protective"] is True
            # ACCEPT it: SOXL sold into cash, only tax leaves, re-buys held, the
            # guard waits for recovery.
            from app.services.fx import book_nav_ils
            from app.services.intake_service import list_positions
            before = book_nav_ils(await list_positions(s, user))
            out = await apply_recommendation(s, user, c["id"])
            assert out and out.get("applied") == "trim", out
            after = book_nav_ils(await list_positions(s, user))
            assert abs(after - before + float(out.get("tax_ils") or 0)) < 1.0, (before, after)
            holds = await bh.load(s, user)
            assert holds["SOXL"]["until_rule"] == str(r.id)
            r2 = await s.get(TradingRule, r.id)
            assert r2.params.get("await_clear") is True and r2.active
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_recovery_hold_does_not_release_on_a_falling_price_only_on_rearm():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "hold@example.com", [_pos("SOXL", 10, 50.0, 40.0, "Equities")],
                               core=None)
            await bh.record(s, user, ticker="SOXL", sold_price=50.0, shares=2.5,
                            strategy_id=None, rule_id="r1", until_rule="r1")
            await s.commit()
            from app.services.intake_service import list_positions
            rows = await list_positions(s, user)
            rows[0].current_price = 1.0          # the price collapses mid-drawdown
            assert "SOXL" in bh.active(await bh.load(s, user), rows), "still held"
            assert await bh.release_for_rule(s, user, "r1") == ["SOXL"]
            await s.commit()
            assert await bh.load(s, user) == {}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_dealt_with_guard_waits_for_recovery_before_firing_again():
    from app.models.tables import NavSnapshot
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "wait@example.com", [_pos("VTI", 100, 100.0, 90.0, "Equities")],
                               core=None)
            for d, v in [("2026-09-01", 13000.0), ("2026-09-02", 10000.0)]:
                s.add(NavSnapshot(subject=user.email, as_of=d, nav_ils=v, cash_ils=0,
                                  invested_ils=10000, positions=1))
            await s.commit()
            r = await rs.create_rule(s, user, ticker=adv.BOOK, rule_type="drawdown_guard",
                                     mode="pct", level=10)
            assert await rs.evaluate_user(s, user)
            await rs.resolve_rule(s, user, str(r.id), "executed")
            r = await s.get(TradingRule, r.id)
            assert r.params.get("await_clear") is True
            assert not await rs.evaluate_user(s, user), "the same drawdown must not re-fire"
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Tuning cards (decision 4)
# --------------------------------------------------------------------------- #
def test_a_band_too_tight_for_the_holding_gets_a_tuning_card():
    r = _R("rebalance_band", 3, ticker="SOXL")
    r.id = "00000000-0000-0000-0000-000000000001"
    pos = {"plan_target_pct": 40.0, "trend": {"metrics": {"vol_20d_pct": 90.0}}}
    c = adv._tune_card(r, pos)
    assert c and c["apply"]["kind"] == "edit_rule" and c["apply"]["level"] > 3
    assert "rule_id" not in c["apply"], "tuning must not resolve the rule"
    calm = {"plan_target_pct": 18.0, "trend": {"metrics": {"vol_20d_pct": 15.0}}}
    assert adv._tune_card(_R("rebalance_band", 3), calm) is None


def test_a_trail_inside_the_noise_gets_a_tuning_card():
    r = _R("profit_protect", 5, ticker="SOXL", params={"activation_pct": 20})
    r.id = "00000000-0000-0000-0000-000000000002"
    c = adv._tune_card(r, {"trend": {"metrics": {"vol_20d_pct": 90.0}}})
    assert c and c["apply"]["level"] >= 10


# --------------------------------------------------------------------------- #
# One-time caps-to-bands card (decision 2) + API
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_core_caps_are_offered_once_as_bands_and_converted():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "caps@example.com", [
                _pos("VTI", 100, 100.0, 90.0, "Equities"),
                _pos("BND", 100, 100.0, 100.0, "Fixed Income")])
            await rs.create_rule(s, user, ticker="VTI", rule_type="max_weight", mode="pct",
                                 level=40)
            rules = (await s.execute(__import__("sqlalchemy").select(TradingRule))).scalars().all()
            idx, _nav = await rs._positions_index_and_nav(s, user)
            card = await adv._convert_caps_card(s, user, rules, idx)
            assert card and card["apply"]["kind"] == "caps_to_bands"
            out = await adv.apply_caps_to_bands(s, user, card["apply"])
            assert out["converted"] == ["VTI"]
            types = {(r.ticker, r.rule_type) for r in (await s.execute(
                __import__("sqlalchemy").select(TradingRule))).scalars().all()}
            assert ("VTI", "rebalance_band") in types and ("VTI", "max_weight") not in types
            rules = (await s.execute(__import__("sqlalchemy").select(TradingRule))).scalars().all()
            assert await adv._convert_caps_card(s, user, rules, idx) is None, "shown once"
    finally:
        await eng.dispose()


def test_the_api_creates_and_edits_advanced_rules():
    from fastapi.testclient import TestClient

    import app.main as m
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
             "spot_price": 371.04, "listing_price": 371.04, "quantity": 9.7,
             "cost_basis": 300, "expected_return_pct": 7, "volatility_pct": 20}]})
        r = c.post("/api/v1/rules", json={"ticker": "V", "rule_type": "profit_protect",
                                          "mode": "pct", "level": 15,
                                          "params": {"activation_pct": 25}})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        e = c.patch(f"/api/v1/rules/{rid}", json={"params": {"activation_pct": 30}})
        assert e.status_code == 200 and e.json()["params"]["activation_pct"] == 30
        d = c.post("/api/v1/rules", json={"ticker": "V", "rule_type": "drawdown_guard",
                                          "mode": "pct", "level": 10})
        assert d.status_code == 200
        rows = c.get("/api/v1/rules").json()["rules"]
        assert any(x["ticker"] == "__BOOK__" and x["rule_type"] == "drawdown_guard" for x in rows)
        bad = c.post("/api/v1/rules", json={"ticker": "V", "rule_type": "trend_break",
                                            "mode": "pct", "level": 100})
        assert bad.status_code == 400


@pytest.mark.asyncio
async def test_a_band_breached_below_becomes_a_funded_buy_on_today(ils_quotes):
    """Below the band the card BUYS, funded through propose_funded_buy -- and
    Accept conserves the book (only tax may leave)."""
    from app.services import recommendations as rr
    from app.services.fx import book_nav_ils
    from app.services.intake_service import list_positions
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "bandbuy@example.com", [
                _pos("VTI", 20, 100.0, 90.0, "Equities"),
                _pos("BND", 150, 100.0, 100.0, "Fixed Income")], cash=3000.0)
            t = (await rr.plan_ticker_targets(s, user))["VTI"] * 100
            r = await rs.create_rule(s, user, ticker="VTI", rule_type="rebalance_band",
                                     mode="pct", level=3)
            await rs.evaluate_user(s, user)
            r = await s.get(TradingRule, r.id)
            assert r.triggered, f"VTI far under its {t:.1f}% target must breach the band"
            built = await rr.build_recommendations(s, user)
            cards = built["recommendations"] + built.get("more_recommendations", [])
            c = next((c for c in cards if c["id"] == f"rule_{str(r.id)[:8]}"), None)
            assert c is not None, [x["id"] for x in cards]
            assert c["apply"]["kind"] == "buy_funded", c["apply"]
            before = book_nav_ils(await list_positions(s, user))
            out = await rr.apply_recommendation(s, user, c["id"])
            after = book_nav_ils(await list_positions(s, user))
            assert abs(after - before + float(out.get("tax_ils") or 0)) < 1.0, (before, after, out)
    finally:
        await eng.dispose()
