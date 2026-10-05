"""PR 90 - the frontier card: resampled, shrink-only, and Accept executes it.

The accept test is the card-claims rule applied to this card: build the real
card, ACCEPT it through the API, and re-measure the book the handler produced.
Never an assertion on the card's wording.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.engines import frontier as fr
from app.main import app
from app.services import frontier_service as fs
from app.services import solver_store

EST_KW = {"objective_target": {"Equities": 0.8, "Fixed Income": 0.1, "Commodities": 0.1},
          "measurements": {},
          "class_assumptions": {"us_equity": (7.5, 15.0), "intl_equity": (6.5, 17.0),
                                "fixed_income": (3.5, 6.0), "commodities": (4.0, 18.0)},
          "cash_rate_pct": 3.0}
IDS = ["us_equity", "intl_equity", "fixed_income", "commodities", "lev", "calm"]


def _prep(seed=3, t=1500):
    """A book where `lev` is a ~85%-vol sleeve riding equities, `calm` a quiet one."""
    rng = np.random.default_rng(seed)
    vol = np.array([0.011, 0.012, 0.004, 0.010, 0.055, 0.012])
    corr = np.full((6, 6), 0.3) + np.eye(6) * 0.7
    corr[0, 4] = corr[4, 0] = 0.9
    cov = corr * np.outer(vol, vol)
    ils = rng.multivariate_normal([4e-4, 3e-4, 1e-4, 2e-4, 6e-4, 4e-4], cov, size=t)
    assets = [{"id": i, "kind": "class" if n < 4 else "sleeve"} for n, i in enumerate(IDS)]
    return {"assets": assets, "ils": ils, "dates": [f"d{i}" for i in range(t)], "fx": True,
            "excluded": [], "degraded": []}


def _solve(sleeves=None, draws=40, seed=fs.SEED, **kw):
    return fs.resize_solve(_prep(), sleeves_pct=sleeves or {"lev": 40.0, "calm": 3.0},
                           core_basket={"VTI": 0.6, "BND": 0.4}, cash_pct=4.0,
                           objective="Grow", est_kw=EST_KW, draws=draws, seed=seed, **kw)


# --------------------------------------------------------------------------
# the solve
# --------------------------------------------------------------------------

def test_an_oversized_leveraged_sleeve_is_resized_inside_its_band():
    v = _solve()
    assert v["outcome"] == fs.RESIZE
    lev = next(s for s in v["sleeves"] if s["id"] == "lev")
    assert lev["act"] and lev["proposed_pct"] < lev["current_pct"] - fs.MIN_MOVE_PCT
    lo, hi = lev["band_pct"]
    assert lo - fs.GRID_PCT <= lev["proposed_pct"] <= hi + fs.GRID_PCT
    assert lev["current_pct"] > hi                      # today is outside the whole band


def test_it_never_proposes_growing_a_sleeve():
    v = _solve()
    for s in v["sleeves"]:
        assert s["proposed_pct"] <= s["current_pct"] + 1e-9
        assert s["band_pct"][1] <= s["current_pct"] + 1e-9


def test_a_sleeve_inside_its_band_is_left_alone():
    v = _solve()
    calm = next(s for s in v["sleeves"] if s["id"] == "calm")
    assert calm["act"] is False                         # 3% is under the 2-point floor anyway


def test_the_resize_moves_the_book_toward_the_plans_kelly_fraction():
    v = _solve()
    target = v["kelly_target"]
    assert target == pytest.approx(fr.KELLY_FRACTION_BY_OBJECTIVE["Grow"])
    b, a = v["before"]["fraction_of_kelly"], v["after"]["fraction_of_kelly"]
    assert abs(a - target) < abs(b - target)
    assert v["after"]["volatility_pct"] < v["before"]["volatility_pct"]
    # The core takes exactly what the sleeves give up; cash does not move.
    moved = sum(s["current_pct"] - s["proposed_pct"] for s in v["sleeves"] if s["act"])
    assert v["core_pct_after"] - v["core_pct_before"] == pytest.approx(moved, abs=0.01)
    assert v["cash_pct"] == pytest.approx(4.0)


def test_the_same_seed_gives_the_same_answer_and_another_seed_a_close_one():
    a, b, c = _solve(), _solve(), _solve(seed=99)
    assert a["sleeves"] == b["sleeves"]
    pa = next(s for s in a["sleeves"] if s["id"] == "lev")["proposed_pct"]
    pc = next(s for s in c["sleeves"] if s["id"] == "lev")["proposed_pct"]
    assert abs(pa - pc) <= 3.0


def test_no_sleeves_or_an_unmodelled_core_is_not_measurable():
    none = fs.resize_solve(_prep(), sleeves_pct={}, core_basket={"VTI": 1.0}, cash_pct=0,
                           objective="Grow", est_kw=EST_KW, draws=5)
    assert none["outcome"] == fs.NOT_MEASURABLE and none["reason"] == "NO_SLEEVES"
    prep = _prep()
    prep["assets"] = [a for a in prep["assets"] if a["id"] != "fixed_income"]
    prep["ils"] = np.delete(prep["ils"], 2, axis=1)
    bad = fs.resize_solve(prep, sleeves_pct={"lev": 40.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                          cash_pct=0, objective="Grow", est_kw=EST_KW, draws=5)
    assert bad["outcome"] == fs.NOT_MEASURABLE and bad["reason"] == "CORE_NOT_MODELLED"


def test_block_bootstrap_keeps_length_and_contiguous_blocks():
    idx = fs.block_bootstrap(100, 21, np.random.default_rng(0))
    assert len(idx) == 100 and idx.min() >= 0 and idx.max() < 100
    assert np.sum(np.diff(idx) == 1) >= 100 - 100 // 21 - 2


def test_core_vector_maps_the_basket_onto_the_proxies():
    ids = IDS + ["cash"]
    v = fs.core_vector({"VTI": 0.6, "BND": 0.4}, ids)
    assert v[ids.index("us_equity")] == pytest.approx(0.6)
    assert v[ids.index("fixed_income")] == pytest.approx(0.4)
    assert fs.core_vector({"VXUS": 1.0}, ids)[ids.index("intl_equity")] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# the trades
# --------------------------------------------------------------------------

V = {"sleeves": [{"id": "s1", "current_pct": 40.0, "proposed_pct": 10.0, "act": True},
                 {"id": "s2", "current_pct": 19.0, "proposed_pct": 13.0, "act": False}],
     "core_basket": {"VTI": 0.6, "BND": 0.4}}
SPECS = {"s1": {"weights": {"SOXL": 1.0}, "base": {"SMH": 1.0}}, "s2": {"weights": {"QQQ": 1.0}}}
HOLD = [{"ticker": "SOXL", "shares": 100, "value_ils": 8000, "tax_ils": 800},
        {"ticker": "SMH", "shares": 10, "value_ils": 2000, "tax_ils": 100},
        {"ticker": "QQQ", "shares": 50, "value_ils": 5000, "tax_ils": 300}]


def test_a_resize_sells_the_same_fraction_of_everything_the_sleeve_holds():
    t = fs.plan_trades(V, specs=SPECS, holdings=HOLD)
    assert [r["strategy_id"] for r in t["resizes"]] == ["s1"]
    by = {x["ticker"]: x for x in t["sells"]}
    assert set(by) == {"SOXL", "SMH"}                   # QQQ is a sleeve not being resized
    assert by["SOXL"]["shares"] == pytest.approx(75.0) and by["SMH"]["shares"] == pytest.approx(7.5)
    assert t["tax_ils"] == pytest.approx(0.75 * 900)
    assert t["net_ils"] == pytest.approx(0.75 * (10000 - 900))
    w = {b["ticker"]: b["amount_ils"] for b in t["buys"]}
    assert w["VTI"] == pytest.approx(0.6 * t["net_ils"], abs=0.01)
    assert w["BND"] == pytest.approx(0.4 * t["net_ils"], abs=0.01)


def test_buys_under_the_minimum_stay_in_cash():
    small = [dict(h, value_ils=h["value_ils"] / 20, tax_ils=0) for h in HOLD]
    t = fs.plan_trades(V, specs=SPECS, holdings=small)
    assert t["buys"] == [] and t["kept_in_cash_ils"] == pytest.approx(t["net_ils"])


def test_a_ticker_two_sleeves_share_is_never_split_by_guesswork():
    specs = {"s1": {"weights": {"SOXL": 1.0}}, "s2": {"weights": {"SOXL": 0.5, "QQQ": 0.5}}}
    t = fs.plan_trades(V, specs=specs, holdings=HOLD)
    assert t["resizes"] == [] and t["skipped"][0]["reason"] == "SHARED_TICKER"


# --------------------------------------------------------------------------
# the card, accepted, re-measured
# --------------------------------------------------------------------------

def _pos(tk, qty, px, basis, cls="Equities"):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": cls, "depth": 2, "spot_price": px,
            "listing_price": px, "quantity": qty, "cost_basis": basis}


async def _store_verdict(own_session, verdict):
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        user = await ensure_superadmin(s)
        row = await fs.record(s, user, verdict)
        return row.id


def _value(c, tickers):
    pf = c.get("/api/v1/portfolio").json()
    return sum(float(p.get("value_ils") or 0) for p in pf.get("positions") or []
               if (p.get("ticker") or "").upper() in tickers)


async def test_accept_resizes_sells_buys_the_core_and_the_card_goes(own_session):
    with TestClient(app) as c:
        for tk in ("SOXL", "SMH", "VTI", "BND", "TQQQ", "QQQ", "SCHD"):
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NASDAQ"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("SOXL", 40, 100, 60), _pos("SMH", 10, 100, 80),
            _pos("VTI", 30, 100, 90), _pos("BND", 20, 100, 100, "Fixed Income")]})
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=40")
        live = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        assert "btm_trend_soxl" in live
        cur = float(live["btm_trend_soxl"])
        sleeve_before = _value(c, {"SOXL", "SMH"})
        core_before = _value(c, {"VTI", "BND"})
        assert sleeve_before > 0

        verdict = {"ok": True, "outcome": fs.RESIZE, "objective": "Grow", "kelly_target": 0.5,
                   "sleeves": [{"id": "btm_trend_soxl", "current_pct": cur, "proposed_pct": cur / 4,
                                "point_pct": cur / 4, "band_pct": [cur / 5, cur / 3], "act": True}],
                   "core_basket": {"VTI": 0.6, "BND": 0.4}, "cash_pct": 0.0,
                   "core_pct_before": 100 - cur, "core_pct_after": 100 - cur / 4,
                   "before": {"fraction_of_kelly": 1.14, "volatility_pct": 42.0, "growth_cagr_pct": 10.1},
                   "after": {"fraction_of_kelly": 0.52, "volatility_pct": 21.0, "growth_cagr_pct": 9.6},
                   "draws": 100, "window": {"sessions": 2215}, "version": fs.FRONTIER_TAG}
        await _store_verdict(own_session, verdict)

        cards = c.get("/api/v1/recommendations").json()
        allc = cards.get("recommendations", []) + cards.get("more_recommendations", [])
        card = next((x for x in allc if (x.get("apply") or {}).get("kind") == fs.CARD_KIND), None)
        assert card is not None, [x.get("title") for x in allc]
        assert not (card["apply"].get("sells"))          # not a funding raid: no `sells` leg

        r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
        assert r.get("applied") == fs.CARD_KIND, r

        # Re-measured, not read off the card.
        after = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
        assert float(after["btm_trend_soxl"]) == pytest.approx(cur / 4)
        sleeve_after = _value(c, {"SOXL", "SMH"})
        assert sleeve_after == pytest.approx(sleeve_before * 0.25, rel=0.02)
        assert _value(c, {"VTI", "BND"}) > core_before
        assert r["tax_ils"] >= 0 and r["sold"] and r["bought"]
        assert not r.get("unrecorded")                   # every share move hit the ledger

        cards2 = c.get("/api/v1/recommendations").json()
        again = [x for x in cards2.get("recommendations", []) + cards2.get("more_recommendations", [])
                 if (x.get("apply") or {}).get("kind") == fs.CARD_KIND]
        assert again == []                               # the verdict was about the old book


def test_a_stale_spec_is_refused_untouched():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.services.feed_service import ensure_superadmin

    async def run():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                user = await ensure_superadmin(s)
                return await fs.apply_resize(s, user, {"resizes": [
                    {"strategy_id": "no_such_sleeve", "from_pct": 40.0, "to_pct": 10.0,
                     "fraction_sold": 0.75, "tickers": ["SOXL"]}], "buy_weights": {}})
        finally:
            await eng.dispose()
    out = asyncio.run(run())
    assert out["refused"] == "BOOK_CHANGED"


def test_target_solver_reads_never_see_a_frontier_row():
    assert fs.FRONTIER_TAG == solver_store.FRONTIER_VERSION
    import inspect
    assert "FRONTIER_VERSION" in inspect.getsource(solver_store.latest)


def test_the_nightly_job_runs_the_frontier_pass(monkeypatch):
    """CLAUDE.md: a job nothing calls is a job nobody has tested."""
    calls = []

    async def fake_refresh(session):
        calls.append(session)
        return {"frontier_solved": 0, "frontier_failed": 0}
    monkeypatch.setattr(fs, "refresh_all", fake_refresh)
    out = solver_store.run_solver_refresh_blocking()
    assert calls and "frontier_solved" in out
