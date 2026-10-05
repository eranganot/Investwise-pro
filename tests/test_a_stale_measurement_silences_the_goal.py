"""PR 99.1: a goal verdict built on out-of-date measurements speaks for nothing.

After PR 101's engine bump every sleeve's backtest was stale until re-measured;
the solve fell back to priors, read 0.19%/yr for a book it put at 17.9% the day
before, and the goal cards offered "Set a target this book can reach: -7%/yr".
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_inputs as fi
from app.services import frontier_service as fs
from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict

STALE = [{"asset": "btm_qqq_hold", "used": False, "reason": fi.VIEW_STALE}]


def test_stale_views_are_named():
    assert fs.stale_views({"views": STALE}) == ["btm_qqq_hold"]
    assert fs.stale_views({"views": [{"asset": "x", "used": True}]}) == []
    assert fs.stale_views({"views": [{"asset": "x", "used": False, "reason": "TOO_FEW_OBSERVATIONS"}]}) == []


async def test_goal_cards_and_screens_stand_down_on_stale_measurements(own_session):
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur, views=STALE))
            assert [x for x in _cards(c) if x.get("dimension") == "goal"] == []
            async with own_session() as s:
                assert await fs.book_expectation(s, await ensure_superadmin(s)) is None
    finally:
        await _reset_plan(own_session)


async def test_a_target_at_or_below_zero_is_never_offered(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(
                cur, levers={"required_cagr_pct": 26.0, "on_track_cagr_pct": 0.2, "amount_at_confidence": 13513,
                             "roi_at_confidence_pct": -7.0, "sized_on": "no_rules", "sized_p_goal_pct": 9.0}))
            goal = [x for x in _cards(c) if x.get("dimension") == "goal"]
            assert not [x for x in goal if (x.get("meta") or {}).get("lever") == "target"]
            assert all("aim for -7" not in x["why"] for x in goal)
    finally:
        await _reset_plan(own_session)


def test_a_gated_headline_keeps_its_return_basis_and_benchmark(monkeypatch):
    """PR 99.1: the gated run is a fresh dict; btm_vol_target_tqqq was stored
    as price_only (and with no benchmark) on dividend-adjusted data."""
    from app.core.config import get_settings
    from app.services import backtest_service as bs

    monkeypatch.setenv("REGIME_GATE_ENABLED_FOR", "x1")
    get_settings.cache_clear()
    try:
        def _run(series, spec, **kw):
            return {"ok": True, "cagr_pct": 9.0 if kw.get("regime_gate") else 10.0,
                    "max_drawdown_pct": -20.0, "observations": 2513, "start": "a", "end": "b"}

        def _fetch(t, classify=False, basis=None):
            if basis is not None:
                basis.update({tk: "total_return" for tk in t})
            return {tk: [("d", 1.0)] * 5 for tk in t}, []

        monkeypatch.setattr(bs.bt, "run", _run)
        monkeypatch.setattr(bs.bt, "gate_verdict", lambda *a, **k: {})
        monkeypatch.setattr(bs, "_fetch", _fetch)
        monkeypatch.setattr(bs.bt, "tickers_needed", lambda s, **k: ["A"])
        monkeypatch.setattr(bs.bt, "out_of_sample", lambda *a, **k: {})
        out = bs.measure({"id": "x1", "overlay": {"kind": "buy_hold"}})
        assert out["regime_gate_applied"] is True and out["cagr_pct"] == 9.0
        assert out["return_basis"] == "total_return"
        assert out["benchmark_ticker"] == get_settings().benchmark_ticker
    finally:
        get_settings.cache_clear()
