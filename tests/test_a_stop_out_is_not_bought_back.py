"""PR 139 -- a stop-out is not bought back by the app (G2 of the 2026-10-22 review).

Read live from production on 2026-10-22 (ledger, rule events, audit log):

* 10-12 16:04  SOXL trailing stop accepted: 19.68 shares sold at 130.43.
* 10-14 05:29  "Fund this sleeve" bought 8.75 SOXL at 127.57.
* 10-15 11:46  sleeve funding bought 8.46 SOXL at 110.54.
* 10-16 19:55  SOXL trailing stop accepted: 16.97 shares sold at 93.96.
* 10-19 21:13  sleeve funding bought 6.81 SOXL at 98.65 -- ABOVE the sale price.
* 10-22        SOXL at 82.11.

Neither exit's executed detail carried a `buyback_hold`, and no `buy_back` rule
existed afterwards. Three things let it happen, all reproduced here:

1. `execution_plan` builds a stop-loss / trailing-stop exit as `sell_position`
   with no hold flag, so the sale records nothing.
2. The only hold that exists (PR 75, for take-profits) ends the moment the price
   is below the sale price -- in a falling market, at once.
3. The Plan buttons (`load-basket`, `/plan/sleeves/fund`) call the funder with
   `honour_holds=False`, so even a hold would not have stopped them.

The rule now: a protective exit holds the ticker until the sleeve's OWN signal
has been out and comes back in, or until you end the hold yourself. No price
level ends it.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

SOXL = "btm_trend_soxl"


def _pos(ticker, qty, price=100.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": 90.0, "expected_return_pct": 8, "volatility_pct": 20}


def _book_with_a_fired_stop(c):
    """A SOXL sleeve with a stop-loss above today's price, fired and on Today."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income"), _pos("SOXL", 20.0)]})
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")
    assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
    r = c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": "stop_loss",
                                      "mode": "price", "level": 110.0})
    assert r.status_code == 200, r.text
    c.post("/api/v1/rules/evaluate")
    cards = c.get("/api/v1/recommendations").json()
    from tests._cards import all_cards
    stop = [x for x in all_cards(cards) if (x.get("apply") or {}).get("kind") == "sell_position"
            and (x.get("apply") or {}).get("ticker") == "SOXL"]
    assert stop, "the fired stop must be a card"
    return stop[0]


def _soxl_qty(c):
    rows = c.get("/api/v1/portfolio").json()["positions"]
    return sum(float(p.get("quantity") or 0) for p in rows if p["ticker"] == "SOXL")


def _accept_the_stop(c, card):
    r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
    assert r.status_code == 200, r.text
    assert _soxl_qty(c) == pytest.approx(0.0, abs=1e-6), "a stop is a full exit"
    return r.json()


def test_a_stop_out_records_a_hold():
    with TestClient(m.app) as c:
        out = _accept_the_stop(c, _book_with_a_fired_stop(c))
        holds = [r for r in c.get("/api/v1/rules").json()["rules"]
                 if r["rule_type"] == "buy_back" and r["ticker"] == "SOXL" and r["active"]]
        assert holds, f"no hold after the stop-out: {out}"


def test_the_plan_cannot_buy_back_a_stopped_ticker():
    """The 10-14 / 10-19 path: the Plan's own buttons re-bought the sleeve."""
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        r = c.post(f"/api/v1/strategies/{SOXL}/load-basket", json={"mode": "fund"})
        assert _soxl_qty(c) == pytest.approx(0.0, abs=1e-6), f"load-basket re-bought SOXL: {r.json()}"
        c.post("/api/v1/plan/sleeves/fund?dry_run=false")
        assert _soxl_qty(c) == pytest.approx(0.0, abs=1e-6), "Fund my sleeves re-bought SOXL"


def test_a_lower_price_does_not_end_a_stop_hold():
    """The PR 75 hold ends below the sale price. A stop's must not: in a falling
    market that is immediately, and it is exactly the re-buy that cost money."""
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        c.post("/api/v1/rules/evaluate")
        holds = [r for r in c.get("/api/v1/rules").json()["rules"]
                 if r["rule_type"] == "buy_back" and r["ticker"] == "SOXL" and r["active"]]
        assert holds and not any(h["triggered"] for h in holds), holds


# --------------------------------------------------------------------------- #
# Release, and the paths a hold must not block
# --------------------------------------------------------------------------- #
def _hold(c, ticker="SOXL"):
    hs = [r for r in c.get("/api/v1/rules").json()["rules"]
          if r["rule_type"] == "buy_back" and r["ticker"] == ticker and r["active"]]
    return hs[0] if hs else None


async def _step(target):
    """Run the 06:15 job's hold step for the SOXL sleeve with `target`."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import TradingRule
    from app.services import buyback_hold as bh
    from app.services.feed_service import ensure_user
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            row = (await s.scalars(select(TradingRule).where(
                TradingRule.rule_type == "buy_back", TradingRule.ticker == "SOXL"))).first()
            user = await ensure_user(s, row.subject)
            gone = await bh.step_signal(s, user, SOXL, target)
            await s.commit()
            return gone
    finally:
        await eng.dispose()


def test_the_hold_names_its_sleeve_and_ends_only_on_a_re_entry():
    import asyncio
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        h = _hold(c)
        assert h and h["params"].get("kind") == "stop"
        assert h["params"].get("strategy_id") == SOXL, h
        in_now = {"SOXL": 0.2}
        # No signal has been measured on this book yet: unknown is NOT out, or
        # the first reading of "in" would end the hold with no re-entry at all.
        assert not h["params"].get("signal_out"), h["params"]
        assert asyncio.run(_step(in_now)) == []
        assert _hold(c), "a signal that never went out must not end the hold"
        assert asyncio.run(_step({})) == []                       # out
        assert _hold(c)["params"].get("signal_out") is True
        assert asyncio.run(_step(in_now)) == ["SOXL"]             # back in
        assert _hold(c) is None


def test_a_today_card_that_would_buy_it_refuses_untouched():
    """A funded buy carries its sells; refusing after them would be half a trade."""
    import app.services.recommendations as rr
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        before = {p["ticker"]: float(p["quantity"]) for p in c.get("/api/v1/portfolio").json()["positions"]}
        fake = {"id": "rec_rebuy", "title": "Add to SOXL", "dimension": "allocation",
                "apply": {"kind": "buy_funded", "ticker": "SOXL", "market": "NASDAQ",
                          "amount_ils": 2000.0, "from_cash_ils": 0.0,
                          "sells": [{"ticker": "VTI", "shares": 5.0}]}}
        real = rr.build_recommendations

        async def with_fake(session, user, *a, **kw):
            out = await real(session, user, *a, **kw)
            return {**out, "recommendations": list(out.get("recommendations") or []) + [fake]}
        rr.build_recommendations = with_fake
        try:
            r = c.post("/api/v1/recommendations/rec_rebuy/accept")
        finally:
            rr.build_recommendations = real
        assert r.status_code == 400 and "on hold" in r.json()["detail"], r.text
        after = {p["ticker"]: float(p["quantity"]) for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert after == before, "nothing may be sold when the buy is refused"


def test_you_can_still_record_a_purchase_you_made():
    """Holdings > Add is you entering a trade you placed; refusing it would make
    the book disagree with your broker."""
    from app.services import buyback_hold as bh
    from app.services import intake_service as isvc
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        import inspect
        assert "recording=True" in inspect.getsource(isvc.add_by_amount)
        assert "recording" in inspect.signature(isvc.buy_into_book).parameters
        assert issubclass(bh.HeldBack, Exception)


def test_ending_the_hold_lets_the_plan_buy_again():
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        h = _hold(c)
        assert c.delete(f"/api/v1/rules/{h['id']}").status_code == 200
        c.post(f"/api/v1/strategies/{SOXL}/load-basket", json={"mode": "fund"})
        assert _soxl_qty(c) > 0, "after you end the hold the sleeve can be funded"


def test_a_later_take_profit_keeps_the_stop_hold():
    import asyncio

    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services import buyback_hold as bh
        from app.services.feed_service import ensure_user
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = await ensure_user(s, "tp-after-stop@example.com")
                await bh.record_stop(s, u, ticker="XYZ", sold_price=50.0, shares=2,
                                     exit_rule_type="trailing_stop", rule_id=None)
                await bh.record(s, u, ticker="XYZ", sold_price=60.0, shares=1,
                                strategy_id=None, rule_id=None)
                await s.commit()
                h = (await bh.load(s, u))["XYZ"]
                # Below its old price level, still held: no price ends a stop hold.
                return h, bh.active({"XYZ": h}, [])
        finally:
            await eng.dispose()
    h, act = asyncio.run(go())
    assert h["kind"] == "stop" and h["level"] == 50.0 and h["shares"] == 3.0
    assert "XYZ" in act


# --------------------------------------------------------------------------- #
# Guard: every app buy path checks the hold before it writes
# --------------------------------------------------------------------------- #
def test_every_buy_path_checks_the_stop_hold():
    """A buy path added later that forgets the check fails here, not in production.

    Every function under app/ that calls `buy_into_book` must either pass
    `recording=True` (you entering your own trade) or call one of the hold
    checks in the same function body. `upsert_positions` callers that create
    holdings on the app's initiative are listed explicitly.
    """
    import ast
    import pathlib
    checks = {"assert_buyable", "stop_held", "_stop_holds", "HeldBack"}
    bad = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            names = {getattr(n.func, "attr", None) or getattr(n.func, "id", None) for n in calls}
            seen = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", None)
                    for n in ast.walk(fn) if isinstance(n, (ast.Attribute, ast.Name))}
            if fn.name == "buy_into_book":
                continue
            for n in calls:
                callee = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if callee != "buy_into_book":
                    continue
                rec = any(k.arg == "recording" and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in n.keywords)
                if not rec and not (checks & (names | seen)):
                    bad.append(f"{path}:{fn.name}")
    assert not bad, f"buy paths with no stop-hold check: {sorted(set(bad))}"


def test_the_held_cash_card_says_what_a_stop_hold_waits_for():
    """PR 139.1. Production 2026-10-23: after the SOXL stop-out the card said the
    app would not buy SOXL back "until your drawdown guard re-arms". A stop sells
    the whole position, so the ticker has no price row, and `price_now = None`
    fell through to the guard sentence. A card may not claim what is not true."""
    import app.services.recommendations as rr
    stop = {"SOXL": {"level": 82.11, "kind": "stop", "strategy_id": SOXL, "exit_rule_type": "stop_loss",
                     "price_now": None}}
    card = rr._held_cash_card(stop, 4027.63, 4178.0, 1500.0, nav=19669.6)
    assert "drawdown guard" not in card["why"], card["why"]
    assert f"until its sleeve's signal ({SOXL}) is out and back in" in card["why"]
    assert "your stop loss sold it at 82.11" in card["why"]
    # a stop no sleeve owns waits for you
    loose = {"VTI": {**stop["SOXL"], "strategy_id": None}}
    assert "until you end the hold" in rr._held_cash_card(loose, 900.0, 2000.0, 1500.0)["why"]
    # the guard hold still says what it always said
    guard = {"SOXL": {"level": 0.0, "until_rule": "g1", "price_now": None}}
    assert "until your drawdown guard re-arms" in rr._held_cash_card(guard, 900.0, 2000.0, 1500.0)["why"]
