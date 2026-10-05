"""PR 97 - the goal reacts to today's market: recent volatility, the regime, and rule firings."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.engines import paths
from app.main import app
from app.services import frontier_inputs as fi
from app.services import frontier_service as fs

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import EST_KW, _prep, _store_verdict


def test_recent_volatility_follows_a_late_spike():
    rng = np.random.default_rng(0)
    calm = rng.normal(0, 0.01, (1000, 1))
    spiky = calm.copy()
    spiky[-40:] *= 4.0                                   # the last two months turn violent
    assert fi.ewma_vol(spiky)[0] > 2 * fi.ewma_vol(calm)[0]
    assert fi.ewma_vol(calm)[0] == pytest.approx(0.01 * np.sqrt(252), rel=0.2)


def test_the_regime_sets_how_much_recent_volatility_counts():
    prep = _prep()
    prep["ils"] = prep["ils"].copy()
    prep["ils"][-60:] *= 3.0
    calm, stressed = fi.vol_scale(prep, "risk_on"), fi.vol_scale(prep, "risk_off")
    assert calm["recent_weight"] == 0.4 and stressed["recent_weight"] == 0.7
    assert np.all(stressed["scale"] > calm["scale"]) and np.all(calm["scale"] > 1.0)
    assert fi.vol_scale(prep, None)["recent_weight"] == fi.DEFAULT_RECENT_WEIGHT


def test_the_scale_multiplies_volatility_and_keeps_correlations():
    prep = _prep()
    s = np.array([2.0, 1.0, 1.0, 1.0, 0.5, 1.0])
    base = fi.estimate(prep["ils"], prep["assets"], **EST_KW)
    scaled = fi.estimate(prep["ils"], prep["assets"], **EST_KW, vol_scale=s)
    assert scaled["cov"][0, 0] == pytest.approx(base["cov"][0, 0] * 4)
    assert scaled["cov"][4, 4] == pytest.approx(base["cov"][4, 4] * 0.25)
    c_b = base["cov"][0, 4] / np.sqrt(base["cov"][0, 0] * base["cov"][4, 4])
    c_s = scaled["cov"][0, 4] / np.sqrt(scaled["cov"][0, 0] * scaled["cov"][4, 4])
    assert c_s == pytest.approx(c_b)
    short = fi.estimate(prep["ils"], prep["assets"], **EST_KW, vol_scale=s[:2])   # padded with 1.0
    assert short["cov"][3, 3] == pytest.approx(base["cov"][3, 3])


def test_paths_carry_the_scale():
    r = np.random.default_rng(1).normal(0.0005, 0.01, (500, 2))
    out = paths.recentre(r, np.array([0.1, 0.1]), vol_scale=np.array([2.0, 1.0]))
    assert out[:, 0].std() == pytest.approx(2 * r[:, 0].std())
    assert out[:, 1].std() == pytest.approx(r[:, 1].std())
    assert np.allclose(out.mean(axis=0), 0.1 / 252)


def test_the_inputs_endpoint_and_the_goal_use_the_same_scaling():
    from tests.test_frontier_inputs import ASSUMPTIONS, GROW, SLEEVE, _series
    plain = fi.build_inputs(_series(), sleeves=[SLEEVE], objective_target=GROW, measurements={},
                            class_assumptions=ASSUMPTIONS, cash_rate_pct=3.0)
    scaled = fi.build_inputs(_series(), sleeves=[SLEEVE], objective_target=GROW, measurements={},
                             class_assumptions=ASSUMPTIONS, cash_rate_pct=3.0, recent_vol={"regime": "risk_off"})
    assert plain["recent_vol"] is None and scaled["recent_vol"]["recent_weight"] == 0.7
    prep = fi.prepare(_series(), sleeves=[SLEEVE])
    s = fi.vol_scale(prep, "risk_off")["scale"]
    direct = fi.estimate(prep["ils"], prep["assets"], objective_target=GROW, measurements={},
                         class_assumptions=ASSUMPTIONS, cash_rate_pct=3.0, vol_scale=s)
    assert np.allclose(np.array(scaled["cov"]), direct["cov"], atol=1e-9)


# --------------------------------------------------------------------------
# a rule firing re-solves the goal
# --------------------------------------------------------------------------

async def test_a_protective_firing_resolves_and_noise_does_not(own_session, monkeypatch):
    from app.services.feed_service import ensure_superadmin
    calls = []

    async def fake_compute(session, user, **kw):
        calls.append(kw.get("source"))
        return {"outcome": fs.GOAL_OUT_OF_REACH}

    async def no_row(session, user):
        return None
    monkeypatch.setattr(fs, "compute_for", fake_compute)
    monkeypatch.setattr(fs, "latest", no_row)
    async with own_session() as s:
        user = await ensure_superadmin(s)
        assert await fs.resolve_after_rule(s, user, [{"rule_type": "price_above"}]) is None
        out = await fs.resolve_after_rule(s, user, [{"rule_type": "trailing_stop"}, {"rule_type": "max_weight"}])
    assert out["resolved"] and calls == ["rule_fired"] and out["fired"] == ["max_weight", "trailing_stop"]


async def test_a_recent_verdict_is_not_resolved_twice(own_session, monkeypatch):
    from datetime import datetime, timezone

    from app.services.feed_service import ensure_superadmin

    class Row:
        created_at = datetime.now(timezone.utc)

    async def recent(session, user):
        return Row()

    async def boom(*a, **k):
        raise AssertionError("must not solve")
    monkeypatch.setattr(fs, "latest", recent)
    monkeypatch.setattr(fs, "compute_for", boom)
    async with own_session() as s:
        user = await ensure_superadmin(s)
        out = await fs.resolve_after_rule(s, user, [{"rule_type": "drawdown_guard"}])
    assert out["skipped"] == "RECENT_VERDICT"


async def test_the_price_job_rule_pass_calls_the_resolve(own_session, monkeypatch):
    """CLAUDE.md: a scheduled path nothing exercises is a path nobody has tested."""
    from app.services import rules_service
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule, delete_rule
    seen = []

    async def fake_eval(session, user, notify=False):
        return [{"rule_type": "trailing_stop", "id": "x"}]

    async def fake_resolve(session, user, fired):
        seen.append(fired)
    monkeypatch.setattr(rules_service, "evaluate_user", fake_eval)
    monkeypatch.setattr(fs, "resolve_after_rule", fake_resolve)
    async with own_session() as s:
        user = await ensure_superadmin(s)
        r = await create_rule(s, user, ticker="QQQ", rule_type="trailing_stop", mode="pct", level=10.0)
        await s.commit()
        try:
            out = await rules_service.evaluate_all(s)
        finally:
            await delete_rule(s, user, str(r.id))
    assert out["triggered"] >= 1 and seen and seen[0][0]["rule_type"] == "trailing_stop"


async def test_goal_cards_say_what_the_market_is_doing(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur, market={
                "ok": True, "state": "risk_off", "components": {"trend_up": False, "vol_percentile": 91.0},
                "recent_weight": 0.7, "vol": [{"id": "btm_trend_soxl", "long_run_vol_pct": 88.0,
                                               "recent_vol_pct": 120.0, "scale": 1.25}]}))
            dep = next(x for x in _cards(c) if (x.get("meta") or {}).get("lever") == "deposit")
            assert "Markets now: stressed (risk-off) - trend down" in dep["why"]
            assert "91th percentile" in dep["why"] or "91.0th percentile" in dep["why"]
            assert "recent volatility weighs 70%" in dep["why"]
            assert "btm_trend_soxl 120% volatility lately vs 88% long-run" in dep["why"]
    finally:
        await _reset_plan(own_session)


def test_the_crash_risk_limit_is_the_users_one_percent():
    assert fs.HALVE_FLOOR_PCT == 1.0
