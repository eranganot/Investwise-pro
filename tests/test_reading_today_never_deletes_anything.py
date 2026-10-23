"""PR 109: reading Today never deletes or retires a rule (D-b).

Proven 2026-10-08, re-proven 2026-10-11 (probe docs/plans/evidence-2026-10-08/
probes/scratch_funding/test_c9_get_writes.py): ONE GET of Today deleted the
watch alerts AMZN buy_dip and GOOGL price_below and the NVDA stop -- before any
card was shown -- because `list_rules` purged rules for unheld tickers on every
read. Inferred and now proven here: the banner reconciliation "healed" a
triggered rule with no served card by acknowledging it, and acknowledging a
one-shot rule sets active=False -- a read could switch off a stop.

D-b (owner, 2026-10-08): entry rules (buy_dip, price_below, price_above) on a
ticker you don't hold are watch alerts and are kept; only exit rules of a
position you no longer hold are cleared, and only by the evaluator.
"""
import asyncio

from fastapi.testclient import TestClient

import app.main as m


def _p(tk, qty, price=100.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": price, "expected_return_pct": 8, "volatility_pct": 20}


RULES = (("V", "stop_loss", 150.0), ("MSFT", "take_profit", 50.0),
         ("NVDA", "stop_loss", 150.0), ("AMZN", "buy_dip", 90.0),
         ("GOOGL", "price_below", 90.0), ("SCHD", "trailing_stop", 10.0))


def _db(fn):
    """Run `fn(session)` on a throwaway NullPool engine in this loop."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    async def go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)() as s:
                return await fn(s)
        finally:
            await eng.dispose()
    return asyncio.run(go())


def _rows():
    from sqlalchemy import func, select

    from app.models.tables import RuleEvent, TradingRule

    async def q(s):
        rules = (await s.execute(select(TradingRule))).scalars().all()
        n_ev = await s.scalar(select(func.count()).select_from(RuleEvent))
        return ({(str(r.id), r.ticker, r.rule_type, float(r.level), bool(r.active)) for r in rules}, n_ev)
    return _db(q)


def _latch_all():
    from sqlalchemy import update

    from app.models.tables import TradingRule

    async def q(s):
        await s.execute(update(TradingRule).values(triggered=True))
        await s.commit()
    _db(q)


def _seed(c):
    c.put("/api/v1/plan", json={"objective": "Grow"})
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _p("V", 100), _p("MSFT", 100), _p("SCHD", 100), _p("QQQ", 100), _p("VTI", 100)]})
    ids = {}
    for tk, rt, lvl in RULES:
        ids[tk] = c.post("/api/v1/rules", json={"ticker": tk, "rule_type": rt, "mode": "price",
                                                "level": lvl}).json()["id"]
    return ids


def test_two_reads_of_today_and_the_rules_tab_write_no_rule():
    with TestClient(m.app) as c:
        _seed(c)
        _latch_all()
        before = _rows()
        c.get("/api/v1/recommendations")
        c.get("/api/v1/rules")
        c.get("/api/v1/recommendations")
        after = _rows()
    # On HEAD: NVDA stop, AMZN buy_dip and GOOGL price_below are gone after the first read.
    assert after == before, {"deleted_or_changed": sorted(before[0] - after[0]),
                             "events": (before[1], after[1])}


def test_a_triggered_stop_whose_card_is_not_served_stays_armed(monkeypatch):
    """The inferred retirement, proven: no card served -> the banner 'healed' it."""
    from app.services import rules_service as rs

    async def none(session, user):
        return []
    with TestClient(m.app) as c:
        ids = _seed(c)
        _latch_all()
        monkeypatch.setattr(rs, "triggered_rule_recs", none)
        body = c.get("/api/v1/recommendations").json()
        active = {r[0]: r[4] for r in _rows()[0]}
    # On HEAD: the V stop is acknowledged by the read and switched off.
    assert active[ids["V"]] is True, body.get("rule_banner")
    assert str(ids["V"])[:8] in body["rule_banner"]["uncarded"]


def test_the_rules_tab_lists_watch_alerts_and_hides_orphaned_stops():
    with TestClient(m.app) as c:
        _seed(c)
        listed = c.get("/api/v1/rules").json()
    rows = listed if isinstance(listed, list) else listed.get("rules", [])
    shown = {(r["ticker"], r["rule_type"]) for r in rows}
    assert ("AMZN", "buy_dip") in shown and ("GOOGL", "price_below") in shown
    assert ("NVDA", "stop_loss") not in shown, "an exit rule on a sold position is not armed protection"


def test_the_evaluator_quotes_watch_alerts_and_clears_only_orphaned_exits(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry

    with TestClient(m.app) as c:
        _seed(c)

    def quote(tk):
        return NS(ticker=tk, price={"GOOGL": 85.0, "AMZN": 120.0}.get(tk.upper(), 100.0),
                  currency="USD", market="NASDAQ")
    monkeypatch.setattr(registry, "guarded_quote", quote, raising=False)

    from sqlalchemy import select

    from app.models.tables import TradingRule
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import evaluate_user

    async def run(s):
        user = await ensure_superadmin(s)
        newly = await evaluate_user(s, user)
        await s.commit()
        rules = {(r.ticker, r.rule_type): r for r in (await s.execute(select(TradingRule))).scalars().all()}
        return newly, rules
    newly, rules = _db(run)
    assert ("NVDA", "stop_loss") not in rules, "the evaluator clears an exit rule of a sold position"
    g, a = rules[("GOOGL", "price_below")], rules[("AMZN", "buy_dip")]
    assert g.active and g.triggered and g.params["watch_price"] == 85.0
    assert a.active and not a.triggered and a.params["watch_price"] == 120.0
    assert any(n["ticker"] == "GOOGL" for n in newly)


def test_a_dismissed_rule_is_resolved_by_the_evaluator():
    from app.services import recommendations as rec
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import heal_dismissed_rules

    with TestClient(m.app) as c:
        ids = _seed(c)
        _latch_all()
        cid = f"rule_{str(ids['V'])[:8]}"

        async def mark(s):
            user = await ensure_superadmin(s)
            await rec._mark(s, rec._dismiss_key(user), cid, rec._DISMISS_TTL_DAYS)
            await s.commit()
        _db(mark)
        c.get("/api/v1/recommendations")              # a read: changes nothing
        assert {r[0]: r[4] for r in _rows()[0]}[ids["V"]] is True

    async def heal(s):
        return await heal_dismissed_rules(s, await ensure_superadmin(s))
    assert _db(heal) == [cid]
    # PR 142 (D117): Ignore keeps the position, so it keeps the stop -- still on,
    # re-armed one step below today's 100 (it used to be switched off here).
    v = {r[0]: r for r in _rows()[0]}[ids["V"]]
    assert v[4] is True and v[3] == 90.0, v
