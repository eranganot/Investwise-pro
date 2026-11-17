"""PR 167 (D150) -- the cap a sleeve arms, the cap card and the rules check all
measure a shared ticker against sleeve + core, on a real database.

The live shape, 2026-11-16: Trend-Filtered Leveraged Nasdaq (35%) parked in QQQ,
2x Gold Trend (35%) parked in BIL, Diversified Global Growth as the core (25%
QQQ). `/rules` showed the sleeve's cap on QQQ at 35.0 beside a band reading
"plan 42.3%". See `test_a_planned_name_may_reach_its_plan_weight` for the
ledger. Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.tables import TradingRule  # noqa: E402
from app.schemas.intake import IntakePosition  # noqa: E402
from app.schemas.state_machine import Market  # noqa: E402
from app.services import funding_service as fs  # noqa: E402
from app.services import recommendations as rr  # noqa: E402
from app.services import rules_service as rs  # noqa: E402
from app.services import sleeve_service as sv  # noqa: E402
from app.services import strategy_service as ss  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ensure_account, ensure_entity, set_cash, upsert_positions)
from app.services.plan_service import upsert_plan  # noqa: E402

NASDAQ, GOLD, CORE = "btm_trend_tqqq", "div_2x_gold_trend", "grow_diversified"
TRIM_QQQ = rr._rid("trim", "QQQ")          # rec_449403, the 18:27:59 sale


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """ILS quotes at 100, both sleeves parked (as live), no screener network."""
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service
    from app.services import redeploy_research as research

    def _q(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="ILS", market="TASE")
    for mod in (registry, pricing_service, ss):
        monkeypatch.setattr(mod, "guarded_quote", _q, raising=False)

    async def _live(session, user):
        return {NASDAQ: {"target": {"QQQ": 1.0}, "as_of": "2026-11-06"},
                GOLD: {"target": {"BIL": 1.0}, "as_of": "2026-11-13"}}
    monkeypatch.setattr(sv, "live_sleeve_targets", _live)
    monkeypatch.setattr(rr, "_buy_ideas", lambda *a, **k: [])
    monkeypatch.setattr(research, "live_scores", lambda *a, **k: {})
    monkeypatch.setattr(research, "live_trend", lambda tk: {"state": "mixed"})


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(tk, ils, cls="Equities"):
    return IntakePosition(ticker=tk, market=Market.TASE, depth=2, spot_price=100.0,
                          listing_price=100.0, quantity=ils / 100.0, cost_basis=100.0,
                          asset_class=cls)


async def _book(s, email, *, qqq=6769.65, cash=1449.59, extra=()):
    """/api/v1/portfolio at 2026-11-16 20:18 UTC, in ILS."""
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    await upsert_positions(s, acc, [
        _pos("BIL", 6835.35, "Fixed Income"), _pos("QQQ", qqq), _pos("VTI", 2259.55),
        _pos("VXUS", 1202.10), _pos("SMH", 843.70), *extra])
    await s.commit()
    await set_cash(s, user, cash)
    plan = await upsert_plan(s, user, objective="Grow", risk_tolerance="High", horizon_years=6,
                             target_amount=100000, cash_target_ils=150, cash_redeploy_at_ils=1000)
    # His settings (`/plan` thresholds_overrides): 35% single name, 70% leveraged.
    plan.thresholds = {"concentration_cap_pct": 35.0, "leveraged_ceiling_pct": 70.0}
    for sid in (GOLD, NASDAQ):
        res = await sv.add_or_resize(s, user, sid, 35.0)
        assert res["ok"], res
    assert (await sv.set_core(s, user, CORE)).get("ok") is not False
    await s.commit()
    return user


async def _cap_rule(s, user, tk="QQQ"):
    return (await s.scalars(select(TradingRule).where(
        TradingRule.subject == user.email, TradingRule.rule_type == "max_weight",
        TradingRule.ticker == tk))).first()


async def _plan_qqq(s, user):
    return (await rr.plan_ticker_targets(s, user))["QQQ"]


@pytest.mark.asyncio
async def test_the_sleeve_arms_its_cap_at_sleeve_plus_core():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167a@example.com")
            await ss._arm_sleeve_caps(s, user)
            plan = await _plan_qqq(s, user)
            assert plan > 0.40, plan                       # precondition: the core wants QQQ too
            rule = await _cap_rule(s, user)
            assert rule is not None and rule.strategy_id == ss.SLEEVE_OWNED
            # On main: 35.0, the sleeve alone.
            assert float(rule.level) == pytest.approx(plan * 100.0, abs=0.06), rule.level
            assert "core" in rule.note and len(rule.note) <= ss.MAX_RULE_NOTE, rule.note
            # BIL: no core share, the sleeve's own size, the old wording.
            bil = await _cap_rule(s, user, "BIL")
            assert float(bil.level) == 35.0 and "core" not in bil.note
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_rules_check_raises_a_stale_sleeve_cap_and_nothing_else():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167b@example.com")
            await ss._arm_sleeve_caps(s, user)
            rule = await _cap_rule(s, user)
            rule.level, rule.triggered = 35.0, True        # production's row; latched
            s.add(TradingRule(subject=user.email, ticker="VTI", rule_type="max_weight",
                              mode="pct", level=5.0, note="mine"))        # hand-set, under plan
            await s.commit()
            plan = await _plan_qqq(s, user)
            out = await ss.relevel_sleeve_caps_to_plan(s, user)
            await s.commit()
            assert [o["ticker"] for o in out] == ["QQQ"], out
            rule = await _cap_rule(s, user)
            assert float(rule.level) == pytest.approx(plan * 100.0, abs=0.06)
            assert rule.triggered is True                  # a check never resets a latch
            assert float((await _cap_rule(s, user, "VTI")).level) == 5.0
            assert await ss.relevel_sleeve_caps_to_plan(s, user) == []      # idempotent
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_evaluate_user_runs_the_relevel():
    """A job nothing calls is a job nobody has tested (CLAUDE.md)."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167c@example.com")
            await ss._arm_sleeve_caps(s, user)
            (await _cap_rule(s, user)).level = 35.0
            await s.commit()
            await rs.evaluate_user(s, user)
            await s.commit()
            assert float((await _cap_rule(s, user)).level) > 40.0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_caps_carry_the_plan_and_read_a_stale_sleeve_cap_at_it():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167d@example.com")
            await ss._arm_sleeve_caps(s, user)
            (await _cap_rule(s, user)).level = 35.0
            s.add(TradingRule(subject=user.email, ticker="VTI", rule_type="max_weight",
                              mode="pct", level=5.0, note="mine"))
            await s.commit()
            caps = await rs.name_caps(s, user)
            plan = await _plan_qqq(s, user)
            assert isinstance(caps, fs.NameCaps) and caps.plan["QQQ"] == pytest.approx(plan)
            assert caps["QQQ"] == pytest.approx(plan)            # read at the plan, row untouched
            assert float((await _cap_rule(s, user)).level) == 35.0
            assert caps["VTI"] == 0.05                           # hand-set: exactly as set
            assert fs.effective_name_cap(0.35, "QQQ", caps) == pytest.approx(plan)
            assert fs.effective_name_cap(0.35, "VTI", caps) == 0.05
    finally:
        await eng.dispose()


async def _today(s, user):
    built = await rr.build_recommendations(s, user)
    cards = built["recommendations"] + built.get("more_recommendations", [])
    return cards, built


@pytest.mark.asyncio
async def test_no_trim_card_for_a_planned_name_between_the_cap_and_its_plan():
    """QQQ at ~41.8% (what the realign bought to): over the 35% cap, under the
    42.3% plan. On main: "Trim QQQ", the card that sold 1,299.59."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167e@example.com", qqq=8069.24, cash=150.0)
            await ss._arm_sleeve_caps(s, user)
            cards, built = await _today(s, user)
            ids = {c["id"] for c in cards} | {h["id"] for h in built.get("headsups", [])}
            assert TRIM_QQQ not in ids, [c["title"] for c in cards]
            assert not any((c.get("apply") or {}).get("kind") == "trim"
                           and (c.get("apply") or {}).get("ticker") == "QQQ" for c in cards)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_name_outside_the_plan_is_still_trimmed_at_the_cap():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167f@example.com", extra=(_pos("NVDA", 14000.0),))
            await ss._arm_sleeve_caps(s, user)
            cards, _ = await _today(s, user)
            trim = next((c for c in cards if c["id"] == rr._rid("trim", "NVDA")), None)
            assert trim is not None, [c["title"] for c in cards]
            assert trim["meta"]["limit_pct"] == 35.0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_live_book_puts_the_cash_into_qqq_and_accepting_it_fires_nothing():
    """Tonight's book end to end: the card, its accept, and Today afterwards."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167g@example.com")
            await ss._arm_sleeve_caps(s, user)
            cards, built = await _today(s, user)
            cash = next((c for c in cards if (c.get("apply") or {}).get("kind") == "redeploy_cash"),
                        None)
            assert cash is not None, [c["title"] for c in cards]
            legs = {x["ticker"]: x["amount_ils"] for x in cash["apply"]["legs"]}
            assert set(legs) == {"QQQ"}, legs               # on main: VTI, VXUS, SMH
            assert legs["QQQ"] > 1200.0
            assert TRIM_QQQ not in {c["id"] for c in cards}

            done = await rr.apply_recommendation(s, user, cash["id"])
            assert done is not None
            await s.commit()
            cards2, _ = await _today(s, user)
            sells = [c for c in cards2
                     if (c.get("apply") or {}).get("kind") in ("trim", "sell_position")]
            assert sells == [], [(c["id"], c["title"]) for c in sells]
            again = [c for c in cards2 if (c.get("apply") or {}).get("kind") == "redeploy_cash"]
            assert again == [], [c["title"] for c in again]
    finally:
        await eng.dispose()


def _idea(tk):
    return {"ticker": tk, "name": tk, "score": 74.5, "kind": "stock", "sector": "Energy",
            "asset_class": "Equities", "reasons": ["ranks well"], "flags": [], "metrics": {},
            "data_gaps": [],
            "ranked_for": {"objective": "Grow", "coverage": {"requested": 58, "ranked": 58}}}


@pytest.mark.asyncio
async def test_today_offers_no_new_name_the_realign_would_sell(monkeypatch):
    """"Consider CVX -- 463" was live on 2026-11-16. C30 (11-09) filters ideas to
    the core basket, but read the core off a dict the caller never sends; this
    goes through the caller. VXUS (in the basket, held) is the control."""
    monkeypatch.setattr(rr, "_buy_ideas", lambda *a, **k: [_idea("CVX"), _idea("XOM")])
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr167h@example.com")
            await ss._arm_sleeve_caps(s, user)
            cards, built = await _today(s, user)
            shown = cards + [h.get("card") or h for h in built.get("headsups", [])]
            bought = {str((c.get("apply") or {}).get("ticker") or "").upper() for c in shown
                      if (c.get("apply") or {}).get("kind") == "buy_funded"}
            titles = [c.get("title") for c in shown]
            assert not ({"CVX", "XOM"} & bought), titles
            assert not any("CVX" in str(t) or "XOM" in str(t) for t in titles), titles
    finally:
        await eng.dispose()


_MOVES = ("redeploy_cash", "core_to_basket", "fund_sleeve", "trim", "sell_position")


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["core_to_basket", "redeploy_cash", "fund_sleeve"])
async def test_whichever_card_is_accepted_first_the_book_settles(first):
    """The 18:27 sequence, replayed. On main the realign's buy was followed by
    "Trim QQQ", whose cash fed the cash card, whose VTI leg breached VTI's band.
    Accept `first`, then keep accepting whatever moves money: it must stop, with
    nothing sold that was just bought and QQQ at its plan."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, f"pr167-{first}@example.com")
            await ss._arm_sleeve_caps(s, user)
            accepted = []
            for _ in range(6):
                cards, _b = await _today(s, user)
                movers = [c for c in cards if (c.get("apply") or {}).get("kind") in _MOVES]
                pick = (next((c for c in movers if c["apply"]["kind"] == first), None)
                        if not accepted else None) or (movers[0] if movers else None)
                if pick is None:
                    break
                accepted.append((pick["apply"]["kind"], pick["title"]))
                assert await rr.apply_recommendation(s, user, pick["id"]) is not None
                await s.commit()
            else:
                pytest.fail(f"never settled: {accepted}")
            assert accepted and accepted[0][0] == first, accepted
            assert not any(k in ("trim", "sell_position") for k, _t in accepted), accepted
            assert len(accepted) <= 2, accepted
            from app.services.intake_service import list_positions
            rows = await list_positions(s, user)
            val = {r.ticker: float(r.quantity) * float(r.current_price or 0) for r in rows}
            nav = sum(val.values())
            plan = await _plan_qqq(s, user)
            assert val["QQQ"] / nav == pytest.approx(plan, abs=0.012), (val["QQQ"] / nav, plan)
            assert val["VTI"] / nav < 0.147                 # the top of VTI's band
    finally:
        await eng.dispose()
