"""C15 -- the core realign sold the sleeve that was SHORT (BACKLOG C15, 2026-10-23).

Read from the production ledger, 10-19 .. 10-21. Each accept of "Bring your core
back to ..." (`core_to_basket`, rec_2d1727 / rec_f6f017) sold a slice of EVERY
sleeve holding at once -- SOXL, TQQQ, UGL, QQQ -- and minutes later "Fund my
sleeves" bought SOXL, TQQQ and UGL back:

    10-19 21:11  core sells TQQQ 8.44, 2.15 ...   21:14  sleeve_fund buys TQQQ 9.68
    10-20 03:59  core sells TQQQ 5.60, 2.56, ...  04:01  sleeve_fund buys TQQQ 6.48
    10-21 05:12  core sells TQQQ 1.37             05:15  sleeve_fund buys TQQQ 6.54

On 10-20 03:59 the same accept sold 1,358 of QQQ and bought 2,311 of QQQ.

The cause: `_core_plan` sizes the trim from the sleeves that are OVER their size
(`sleeve_occupancy`, overshoots only), and `_apply_core_to_basket` then sells it
"pro-rata across the sleeve holdings" -- every sleeve, including those under
their size, which the sleeve funder then refills. And a sleeve ticker the core
basket also holds (QQQ) was sold by one leg and bought by another.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

SOXL, TQQQ = "btm_trend_soxl", "btm_vol_target_tqqq"
CORE = "bal_6040"


@pytest.fixture
def quotes(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _quote(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="USD", market="NYSE")
    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _quote, raising=False)


def _p(tk, qty, cls="Equities"):
    return {"ticker": tk, "market": "NYSE", "asset_class": cls, "depth": 1, "spot_price": 100.0,
            "listing_price": 100.0, "quantity": qty, "cost_basis": 90.0,
            "expected_return_pct": 8, "volatility_pct": 20}


def _qty(c, tk):
    return sum(float(p.get("quantity") or 0) for p in c.get("/api/v1/portfolio").json()["positions"]
               if p["ticker"] == tk)


def _book(c):
    """SOXL far over its 20% sleeve; TQQQ under its 25% sleeve; a core that holds
    none of its basket, so the realign needs money and reaches for the sleeves."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _p("SOXL", 60.0), _p("TQQQ", 10.0), _p("V", 30.0)]})
    c.put("/api/v1/plan", json={"objective": "Grow"})
    assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
    assert c.post(f"/api/v1/strategies/{TQQQ}/apply?sleeve_pct=25").status_code == 200
    assert c.post("/api/v1/plan/core", json={"strategy_id": CORE}).json()["ok"] is True
    occ = {s["strategy_id"]: s for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
    assert occ[SOXL]["held_pct"] * 100 > 20 + 5, occ[SOXL]
    assert occ[TQQQ]["held_pct"] * 100 < 25 - 5, occ[TQQQ]


def _accept_core(own_session):
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import recommendations as R

    async def go():
        async with own_session() as s:
            user = (await s.execute(select(User))).scalars().first()
            return await R._apply_core_to_basket(s, user, {"objective": "Grow"})
    return asyncio.run(go())


def test_the_core_does_not_sell_a_sleeve_that_is_short(quotes, own_session):
    with TestClient(m.app) as c:
        _book(c)
        soxl0, tqqq0 = _qty(c, "SOXL"), _qty(c, "TQQQ")
        out = _accept_core(own_session)
        assert _qty(c, "TQQQ") == pytest.approx(tqqq0, abs=1e-6), \
            f"the realign sold TQQQ, whose sleeve is UNDER its size: {out}"
        assert _qty(c, "SOXL") < soxl0, f"the overshoot (SOXL) is what may be trimmed: {out}"


def test_a_ticker_the_core_basket_holds_is_not_sold_to_be_bought_back(quotes, own_session):
    """10-20 03:59: one accept sold 1,358 of QQQ (a sleeve's overshoot) and
    bought 2,311 of QQQ (the core's basket leg)."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("QQQ", 60.0), _p("SOXL", 40.0), _p("V", 30.0)]})
        c.put("/api/v1/plan", json={"objective": "Grow"})
        assert c.post("/api/v1/strategies/btm_qqq_hold/apply?sleeve_pct=20").status_code == 200
        assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=10").status_code == 200
        assert c.post("/api/v1/plan/core", json={"strategy_id": "grow_diversified"}).json()["ok"] is True
        out = _accept_core(own_session)
        sold = {x["ticker"] for x in (out.get("sold") or [])}
        bought = {x["ticker"] for x in (out.get("bought") or [])}
        assert not (sold & bought), f"sold and bought the same ticker: {sold & bought} ({out})"
        assert "SOXL" in sold, f"SOXL's overshoot is still a source: {out}"


def test_the_trim_never_takes_more_than_a_sleeve_is_over(quotes, own_session):
    with TestClient(m.app) as c:
        _book(c)
        occ = {s["strategy_id"]: s for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        nav = c.get("/api/v1/portfolio").json().get("nav_ils") or None
        out = _accept_core(own_session)
        soxl_sold = sum(x["ils"] for x in out["sold"] if x["ticker"] == "SOXL")
        if nav:
            over = (occ[SOXL]["held_pct"] * 100 - 20) / 100 * float(nav)
            assert soxl_sold <= over + 1.0, (soxl_sold, over)
        # after the accept, the SOXL sleeve is still at or above its size
        occ2 = {s["strategy_id"]: s for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        assert occ2[SOXL]["held_pct"] * 100 >= 20 - 0.5, occ2[SOXL]


def test_the_card_names_where_the_trim_comes_from():
    from app.services.core_realign import describe
    lines = describe({"legs": [{"action": "trim_sleeves", "ils": 1200.0}],
                      "sleeve_trim_from_ils": {"SOXL": 1000.0, "UGL": 200.0}})
    assert lines == ["Trim your sleeves back to the sizes you set — about ₪1,200 "
                     "(from SOXL, UGL, the sleeves that are over)"]
