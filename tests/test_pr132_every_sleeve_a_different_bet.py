"""PR 132 (D97). Owner, 2026-10-15: "I don't want that all the sleeves will be
similar and will be depending on the same 2-3 stocks. each sleeve (that I
chose) MUST be different".

Two sleeves are the same bet when they are bets on the same market for at least
`sleeve_overlap_alert_pct` (40%) of the smaller one -- TQQQ, QLD and QQQ are all
the Nasdaq-100 -- or when their daily returns correlate at
`sleeve_correlation_limit` (0.80) or more, as last measured. A NEW sleeve that
is the same bet as one held is refused, with "replace instead"; sleeves already
held are left alone (owner: card only) and reported for the wave-4 card.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import json  # noqa: E402
import pathlib  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import sleeve_diversity as div  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402

TREND_NDX = "btm_trend_tqqq"
VOL_NDX = "btm_vol_target_tqqq"
SEMIS = "btm_trend_soxl"
FACTORS = "btm_factor_stack"


def _spec(i):
    return cat._spec(cat.get(i))


def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("TQQQ", 3000.0), _pos("VTI", 4000.0), _pos("BND", 3000.0, cls="Fixed Income")]})


# --------------------------------------------------------------------------- #
# the map
# --------------------------------------------------------------------------- #
def test_leveraged_and_plain_nasdaq_are_one_market():
    assert div.market_of("TQQQ") == div.market_of("QLD") == div.market_of("QQQ") == "Nasdaq-100"
    assert div.market_of("SOXL") == div.market_of("SMH")
    assert div.market_of("SOXL") != div.market_of("TQQQ")
    assert div.market_of("BIL") == div.CASH


def test_a_nasdaq_rule_is_a_nasdaq_bet_on_or_off_and_cash_is_not_a_bet():
    # On: TQQQ. Off: QQQ (trend) or BIL (to cash). Both are a Nasdaq bet, and
    # stepping back to T-bills does not make two rules the same bet.
    assert div.profile(_spec("btm_tqqq_trend_cash")) == {"Nasdaq-100": 1.0}
    assert div.CASH not in div.profile(_spec("btm_dual_momentum"))


def test_every_pair_of_nasdaq_rules_is_the_same_bet_and_semis_and_factors_are_not():
    ov, markets = div.overlap_pct(_spec(TREND_NDX), _spec(VOL_NDX))
    assert ov == 100.0 and markets == ["Nasdaq-100"]
    assert div.overlap_pct(_spec(TREND_NDX), _spec(SEMIS))[0] == 0.0
    assert div.overlap_pct(_spec(VOL_NDX), _spec(FACTORS))[0] == 0.0
    assert div.same_bet(TREND_NDX, VOL_NDX, {}) is not None
    assert div.same_bet(VOL_NDX, SEMIS, {}) is None


def test_a_measured_correlation_over_the_limit_makes_different_tickers_one_bet():
    """Semis and the Nasdaq share no ticker; if they move together, they are one bet."""
    corr = {"pairs": {div.pair_key(VOL_NDX, SEMIS): 0.86}}
    x = div.same_bet(VOL_NDX, SEMIS, corr)
    assert x and x["correlation"] == 0.86 and "move together" in x["why"]
    corr["pairs"][div.pair_key(VOL_NDX, SEMIS)] = 0.79
    assert div.same_bet(VOL_NDX, SEMIS, corr) is None


def test_both_limits_are_settings():
    from app.services import thresholds
    assert "sleeve_overlap_alert_pct" in thresholds.SPEC and "sleeve_correlation_limit" in thresholds.SPEC
    d = thresholds.defaults()
    assert d["sleeve_overlap_alert_pct"] == 40.0 and d["sleeve_correlation_limit"] == 0.80
    tok = thresholds._OVERRIDES.set({"sleeve_overlap_alert_pct": 100.0, "sleeve_correlation_limit": 0.9})
    try:
        # 100% shared still trips a 100% limit; 0.86 no longer trips 0.9.
        assert div.same_bet(TREND_NDX, VOL_NDX, {}) is not None
        assert div.same_bet(VOL_NDX, SEMIS, {"pairs": {div.pair_key(VOL_NDX, SEMIS): 0.86}}) is None
    finally:
        thresholds._OVERRIDES.reset(tok)


# --------------------------------------------------------------------------- #
# the correlation itself
# --------------------------------------------------------------------------- #
def _walk(seed, n=600, mix=None, base=None):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.01, n)
    if base is not None:
        r = mix * base + (1 - mix) * r
    return r


def _series(returns, start=100.0):
    from datetime import date, timedelta
    px, out, d0 = start, [], date(2018, 1, 1)
    for i, r in enumerate(returns):
        px *= 1 + r
        out.append(((d0 + timedelta(days=i)).isoformat(), px))
    return out


def test_correlation_of_rebuilt_daily_returns_is_right():
    """Two buy-and-hold baskets on constructed prices: identical moves read 1.0,
    independent ones read near 0. The method, not the data."""
    a = _walk(1)
    b = _walk(2)
    series = {"QQQ": _series(a), "SPY": _series(a), "GLD": _series(b)}
    ra = div.daily_returns(series, {"weights": {"QQQ": 1.0}, "overlay": {"kind": "buy_hold"}})
    rb = div.daily_returns(series, {"weights": {"SPY": 1.0}, "overlay": {"kind": "buy_hold"}})
    rc = div.daily_returns(series, {"weights": {"GLD": 1.0}, "overlay": {"kind": "buy_hold"}})
    assert div.correlation(ra, rb) == pytest.approx(1.0, abs=1e-6)
    assert abs(div.correlation(ra, rc)) < 0.15


async def test_refresh_stores_the_matrix_and_keeps_a_good_one_when_prices_fail(own_session):
    a, b = _walk(1, 400), _walk(2, 400)

    def fetch(tickers):
        return {t: _series(a if t in ("QQQ", "TQQQ", "BIL") else b) for t in tickers}, []

    async with own_session() as s:
        out = await div.refresh_correlations(s, fetch)
        assert out["stored"] and out["pairs"]
        stored = await div.load_correlations(s)
        assert stored["pairs"] == out["pairs"]

        def dead(tickers):
            return {}, list(tickers)
        again = await div.refresh_correlations(s, dead)
        assert again["stored"] is False
        assert (await div.load_correlations(s))["pairs"] == out["pairs"]


# --------------------------------------------------------------------------- #
# enforced where you add a sleeve
# --------------------------------------------------------------------------- #
def test_adding_a_second_nasdaq_sleeve_is_refused_with_replace_instead():
    with TestClient(m.app) as c:
        _seed(c)
        assert c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=25").json()["ok"]
        r = c.post(f"/api/v1/strategies/{TREND_NDX}/apply?sleeve_pct=20").json()
        assert r["ok"] is False and r["error"] == "same bet as a sleeve you hold", r
        assert r["replace"] == VOL_NDX and "Nasdaq-100" in r["reason"]
        ids = [s["strategy_id"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]]
        assert ids == [VOL_NDX], "a refused add wrote a sleeve"


def test_a_different_bet_is_added_and_a_held_sleeve_can_still_be_resized():
    with TestClient(m.app) as c:
        _seed(c)
        assert c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=25").json()["ok"]
        assert c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=15").json()["ok"]
        r = c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=30").json()
        assert r["ok"] and r["sleeve"]["action"] == "resized", r


def test_replacing_a_sleeve_with_its_look_alike_is_allowed():
    """The swap the refusal offers: the departing sleeve does not count."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=25")
        r = c.post(f"/api/v1/plan/sleeves/{VOL_NDX}/swap?to={TREND_NDX}").json()
        assert r.get("ok"), r
        ids = [s["strategy_id"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]]
        assert ids == [TREND_NDX]


def test_a_swap_into_a_bet_another_sleeve_already_makes_is_refused():
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=25")
        c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=15")
        r = c.post(f"/api/v1/plan/sleeves/{SEMIS}/swap?to={TREND_NDX}").json()
        assert r.get("ok") is False and r.get("replace") == VOL_NDX, r


async def test_the_sleeves_view_marks_look_alikes_and_held_pairs(own_session):
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{VOL_NDX}/apply?sleeve_pct=25")
        j = c.get("/api/v1/plan/sleeves").json()
        assert j["same_bet"][TREND_NDX][0]["strategy_id"] == VOL_NDX
        assert SEMIS not in j["same_bet"] and FACTORS not in j["same_bet"]
        assert j["held_same_bets"] == []
        # A book that already holds two (the owner's did) is reported, not changed.
        async with own_session() as s:
            await sv.add_or_resize(s, await ensure_superadmin(s), TREND_NDX, 20.0)
            await s.commit()
        j = c.get("/api/v1/plan/sleeves").json()
        assert len(j["held_same_bets"]) == 1
        pair = j["held_same_bets"][0]
        assert {pair["a"], pair["b"]} == {VOL_NDX, TREND_NDX} and pair["overlap_pct"] == 100.0


def test_the_correlation_table_is_behind_the_login_and_lists_every_pair():
    with TestClient(m.app) as c:
        j = c.get("/api/v1/strategies/correlations").json()
    n = len(cat.active_ids())
    assert len(j["pairs"]) == n * (n - 1) // 2
    assert j["overlap_limit_pct"] == 40.0 and j["correlation_limit"] == 0.80
    ndx = next(p for p in j["pairs"] if {p["a"], p["b"]} == {TREND_NDX, VOL_NDX})
    assert ndx["same_bet"] and ndx["overlap_pct"] == 100.0


def test_explore_offers_replace_for_a_look_alike():
    html = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    assert "function _sameBet(" in html and "with this</button>" in html
    assert "Same bet as your" in html


def test_the_kv_payload_round_trips():
    assert json.loads(json.dumps({"pairs": {div.pair_key("b", "a"): 0.5}}))["pairs"]["a|b"] == 0.5
