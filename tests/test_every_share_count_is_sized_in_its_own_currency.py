"""PR 105: no card, basket load or holdings add converts shekels into shares at a
dollar (or agorot) price, and nothing values a holding in its trading currency
and calls it shekels.

Each test reproduces a fault proven on 2026-10-08 / 2026-10-11 (probes in
docs/plans/evidence-2026-10-08/probes/scratch_funding/):

* C2b  the sleeve-drift trim: "trim about ₪18,550 of SOXL" sold 185 of 300
       shares (₪68,635).
* C3+  "Replace book": a ₪37,100 book became ₪137,641 (x3.71).
* C3   the assistant's context: SPY ₪37,100 shown as 10,000.
* L-ledger  /portfolio/add on a held ticker REPLACED the position (IAU 100 sh ->
       7.54 sh) and took the money from nowhere.
* agorot  a `.TA` quote arrives in agorot; `fx_rate("ILA")` is 1.0.
"""
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

import app.main as m
from _cards import all_cards
from app.services.fx import fx_rate

USD = fx_rate("USD")


def _p(tk, cls, qty, price=100.0, market="NASDAQ", basis=None):
    return {"ticker": tk, "market": market, "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis if basis is not None else price,
            "expected_return_pct": 8, "volatility_pct": 20}


def _held(c):
    pf = c.get("/api/v1/portfolio").json()
    return {(h.get("ticker") or "").upper(): h for h in (pf.get("holdings") or pf.get("positions") or [])}


# --------------------------------------------------------------------------- #
# C2b: the sleeve-drift trim
# --------------------------------------------------------------------------- #
def test_the_sleeve_drift_trim_sells_what_its_card_says():
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SOXL", "Equities", 300, basis=99.0), _p("V", "Equities", 175),
            _p("SCHD", "Equities", 175), _p("MSFT", "Equities", 175), _p("VTI", "Equities", 175)]})
        assert c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=25").json().get("ok")
        before = _held(c)["SOXL"]
        body = c.get("/api/v1/recommendations").json()
        card = next((x for x in all_cards(body) if "drifted to" in str(x.get("title", ""))), None)
        assert card is not None, [x.get("title") for x in all_cards(body)]
        spec = card["apply"]
        assert spec["kind"] == "trim"
        price_ils = float(before["current_price"]) * USD
        sold_ils = float(spec["shares"]) * price_ils
        claimed = float(card["est_amount"])
        # On HEAD: 185 shares = ₪68,635 against a ₪18,550 card.
        assert abs(sold_ils - claimed) <= 0.02 * claimed, (spec, claimed, sold_ils)

        r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
        assert r.get("ok"), r
        after = float(_held(c)["SOXL"]["quantity"])
        assert abs(after - (float(before["quantity"]) - float(spec["shares"]))) < 1e-3
        meta = card["meta"]
        # Back to (about) the chosen sleeve size, not a third of it.
        assert after >= float(before["quantity"]) * meta["chosen_pct"] / meta["actual_pct"] * 0.98


# --------------------------------------------------------------------------- #
# C3+: Replace book
# --------------------------------------------------------------------------- #
def test_replacing_a_usd_book_keeps_its_value():
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SPY", "Equities", 100)]})
        nav0 = sum(float(h["value_ils"]) for h in _held(c).values())
        pv = c.post("/api/v1/strategies/grow_quality/load-basket",
                    json={"mode": "replace", "dry_run": True}).json()
        assert pv.get("ok"), pv
        assert abs(sum(x["value"] for x in pv["loaded"]) - nav0) <= 0.01 * nav0, pv["loaded"]
        r = c.post("/api/v1/strategies/grow_quality/load-basket",
                   json={"mode": "replace", "dry_run": False}).json()
        assert r.get("ok"), r
        nav1 = sum(float(h["value_ils"]) for h in _held(c).values())
        # On HEAD: 37,100 -> 137,641.
        assert abs(nav1 - nav0) <= 0.01 * nav0, (nav0, nav1)


def test_replace_book_is_refused_while_sleeves_exist():
    """D-e (D69). Already enforced server-side at HEAD (C6); pinned here so the
    UI block at index.html is never the only one."""
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SPY", "Equities", 100), _p("SOXL", "Equities", 20)]})
        assert c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=10").json().get("ok")
        held0 = {k: v["quantity"] for k, v in _held(c).items()}
        for dry in (True, False):
            r = c.post("/api/v1/strategies/grow_quality/load-basket",
                       json={"mode": "replace", "dry_run": dry}).json()
            assert r.get("ok") is False and "btm_trend_soxl" in (r.get("sleeves") or []), r
        assert {k: v["quantity"] for k, v in _held(c).items()} == held0


# --------------------------------------------------------------------------- #
# C3: the assistant's context
# --------------------------------------------------------------------------- #
async def test_the_assistant_context_is_in_shekels(own_session):
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import context_service as cs
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions

    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Balanced"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("SPY", "Equities", 100),
            _p("GOVIL.TA", "Fixed Income", 371, market="TASE")]})
    async with own_session() as s:
        u = (await s.execute(select(User))).scalars().first()
        ctx = await cs.gather(s, u)
        mix, nav = current_mix(await list_positions(s, u))
    vals = {h["ticker"]: h["value_ils"] for h in ctx["holdings"]}
    # On HEAD: SPY 10,000 (dollars), GOVIL.TA 37,100.
    assert abs(vals["SPY"] - 100 * 100 * USD) < 1.0, vals
    assert abs(sum(vals.values()) - ctx["nav_ils"]) < 1.0, (vals, ctx["nav_ils"])
    for cls, w in ctx["asset_class_breakdown"].items():
        assert abs(w - mix.get(cls, 0.0)) < 0.01, (ctx["asset_class_breakdown"], mix)


def test_the_fee_scan_and_backtest_value_in_shekels():
    from app.agents.fee_agent import positions_to_holdings
    out = positions_to_holdings([{"ticker": "SPY", "market": "NASDAQ", "quantity": 10,
                                  "current_price": 100.0, "asset_class": "Equities"}])
    assert abs(out[0]["value_ils"] - 1000 * USD) < 0.01, out


# --------------------------------------------------------------------------- #
# L-ledger: /portfolio/add
# --------------------------------------------------------------------------- #
def test_adding_to_a_holding_tops_it_up_and_pays_from_cash():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _p("IAU", "Commodities", 100, price=50.0, market="NYSE", basis=40.0)]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 5000})
        r = c.post("/api/v1/portfolio/add", json={"ticker": "IAU", "amount": 1000,
                                                  "asset_class": "Commodities", "market": "NYSE"}).json()
        assert r.get("ok"), r
        held = _held(c)
        # On HEAD: 7.5358 -- the buy REPLACED the 100 shares.
        assert abs(float(held["IAU"]["quantity"]) - (100 + r["quantity"])) < 1e-3, held["IAU"]
        assert abs(r["cost_ils"] - 1000 * USD) < 1.0, r
        assert abs(float(held["CASH"]["quantity"]) - (5000 - r["cost_ils"])) < 0.01, held["CASH"]
        led = c.get("/api/v1/portfolio/ledger").json()["entries"]
        assert [(e["ticker"], e["side"], e["source"]) for e in led] == [("IAU", "Buy", "holdings_add")]


def test_adding_a_holding_without_the_cash_is_refused():
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 500})
        r = c.post("/api/v1/portfolio/add", json={"ticker": "IAU", "amount": 1000,
                                                  "asset_class": "Commodities", "market": "NYSE"}).json()
        assert r.get("ok") is False and "not enough cash" in r["error"], r
        held = _held(c)
        assert "IAU" not in held and abs(float(held["CASH"]["quantity"]) - 500) < 0.01


# --------------------------------------------------------------------------- #
# agorot: a .TA quote
# --------------------------------------------------------------------------- #
def test_a_quote_in_agorot_is_sized_in_shekels():
    from app.services.fx import quote_in_ils
    assert quote_in_ils(11030.0, "ILA") == (110.3, "ILS", 110.3)
    major, ccy, ils = quote_in_ils(100.0, "USD")
    assert (major, ccy) == (100.0, "USD") and abs(ils - 100 * USD) < 1e-9


def test_redeploying_cash_into_a_ta_ticker_buys_the_right_shares(monkeypatch):
    from app.providers import registry

    def _q(tk):
        return NS(ticker=tk.upper(), price=11030.0, currency="ILA", market="TASE")

    monkeypatch.setattr(registry, "guarded_quote", _q, raising=False)
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 5000})

    import asyncio

    async def run():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.models.tables import User
        from app.services.intake_service import list_positions
        from app.services.recommendations import _apply_redeploy_cash

        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            mk = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with mk() as s:
                u = (await s.execute(select(User))).scalars().first()
                out = await _apply_redeploy_cash(s, u, {"legs": [{"ticker": "TEVA.TA", "amount_ils": 1103.0}]})
                rows = {p.ticker: p for p in await list_positions(s, u)}
                return out, float(rows["TEVA.TA"].quantity), float(rows["TEVA.TA"].current_price)
        finally:
            await eng.dispose()

    out, qty, price = asyncio.run(run())
    # On HEAD: 0.1 shares at an agorot price of 11030.
    assert abs(qty - 10.0) < 1e-3 and abs(price - 110.3) < 1e-6, (out, qty, price)
