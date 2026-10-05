"""PR 95 - the user's protective rules replayed on paths, with the semantics the app executes."""
import numpy as np
import pytest

from app.engines import paths
from app.services import frontier_service as fs


def _fixed(sleeve, core=None):
    """One deterministic path: block = the whole history, so idx is 0..T-1."""
    sleeve = np.asarray(sleeve, dtype=float)
    core = np.zeros_like(sleeve) if core is None else np.asarray(core, dtype=float)
    return np.column_stack([sleeve, core, np.zeros_like(sleeve)])


def _run(daily, *, w=(0.5, 0.5, 0.0), years=None, **kw):
    t = daily.shape[0]
    return paths.simulate_rules(daily, np.array(w), years=years or (t / 252), n_paths=3, block=t, **kw)


CRASH = [0.01] * 60 + [-0.03] * 60 + [0.0] * 30          # up, then a long fall


def test_rules_off_means_no_rule_acts():
    sim = _run(_fixed(CRASH), sleeves=[{"col": 0, "trail": 0.2}], rules_on=False)
    assert sim["exits_per_year"] == [0.0] and sim["take_profits_per_year"] == [0.0]


def test_a_trailing_exit_fires_in_a_crash():
    sim = _run(_fixed(CRASH), sleeves=[{"col": 0, "trail": 0.2}])
    assert sim["exits_per_year"][0] > 0


def test_first_dip_reentry_buys_back_on_the_way_down_and_rebound_does_not():
    off = _run(_fixed(CRASH), sleeves=[{"col": 0, "trail": 0.2}], rules_on=False)
    dip = _run(_fixed(CRASH), sleeves=[{"col": 0, "trail": 0.2}])
    reb = _run(_fixed(CRASH), sleeves=[{"col": 0, "trail": 0.2}], reentry="rebound")
    # Today's semantics: released on the first print below the exit -> back in
    # almost at once, dodging only a day or so per leg down; the fix stays out
    # until a 15% rebound and protects far more.
    assert reb["final"][0] > dip["final"][0] >= off["final"][0]
    assert reb["worst_drawdown"][0] < dip["worst_drawdown"][0]
    assert dip["pct_time_out"][0] < 5.0 < reb["pct_time_out"][0]


def test_a_take_profit_on_a_straight_rally_sells_slices_and_never_rebuys():
    rally = [0.01] * 120
    off = _run(_fixed(rally), sleeves=[{"col": 0, "tp_ratio": 1.13, "tp_fraction": 0.25}], rules_on=False)
    on = _run(_fixed(rally), sleeves=[{"col": 0, "tp_ratio": 1.13, "tp_fraction": 0.25}])
    assert on["take_profits_per_year"][0] > 0
    assert on["final"][0] < off["final"][0]           # the upside cap is real


def test_the_drawdown_guard_fires_and_rearms():
    sleeve = [0.0] * 10 + [-0.04] * 10 + [0.03] * 40
    sim = _run(_fixed(sleeve), w=(0.6, 0.4, 0.0), sleeves=[{"col": 0}],
               guard={"level": 0.10, "rearm": 0.075, "fraction": 0.25, "col": 0})
    assert sim["guard_fires_per_year"] > 0


def test_the_real_rule_list_maps_to_trail_rung_and_guard():
    rules = [
        {"ticker": "SOXL", "rule_type": "trailing_stop", "mode": "pct", "level": 20.0},
        {"ticker": "SOXL", "rule_type": "profit_protect", "mode": "pct", "level": 20.0,
         "params": {"activated": True}},
        {"ticker": "SOXL", "rule_type": "stop_loss", "mode": "price", "level": 87.49},
        {"ticker": "SOXL", "rule_type": "take_profit", "mode": "price", "level": 183.17},
        {"ticker": "QQQ", "rule_type": "trailing_stop", "mode": "pct", "level": 10.0},
        {"ticker": "VTI", "rule_type": "stop_loss", "mode": "price", "level": 347.71},
        {"ticker": "__BOOK__", "rule_type": "drawdown_guard", "mode": "pct", "level": 10.0},
    ]
    spec = fs.rules_spec(rules, prices={"SOXL": 161.47, "QQQ": 700.0, "VTI": 380.0},
                         sleeve_tickers={"soxl": {"SOXL", "SMH"}, "qqq": {"QQQ"}})
    assert spec["sleeves"]["soxl"]["trail"] == pytest.approx(0.20)
    assert spec["sleeves"]["soxl"]["tp_ratio"] == pytest.approx(183.17 / 161.47)
    assert spec["sleeves"]["soxl"]["tp_fraction"] == pytest.approx(0.25)
    assert spec["sleeves"]["qqq"]["trail"] == pytest.approx(0.10)
    assert spec["guard"] == {"level": 0.10, "rearm": 0.075, "fraction": 0.25}
    assert "stop_loss on VTI" in spec["not_simulated"]


def test_the_goal_verdict_carries_the_rules_replay_and_the_stress_case():
    from tests.test_frontier_resize import EST_KW, _prep
    rules = {"sleeves": {"lev": {"trail": 0.2, "tp_ratio": 1.13, "tp_fraction": 0.25}},
             "guard": {"level": 0.10, "rearm": 0.075, "fraction": 0.25}, "not_simulated": ["stop_loss on VTI"]}
    v = fs.goal_solve(_prep(), sleeves_pct={"lev": 40.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw={**EST_KW, "confidence": "blended"},
                      nav=10_000, target=30_000, years=5.0, draws=0, rules=rules)
    rb = v["rules"]
    assert set(rb) >= {"off", "on", "rebound", "effect_pts", "fix_effect_pts", "guard_target", "assumptions"}
    assert rb["off"]["exits_per_year"] == [0.0, 0.0]
    assert rb["effect_pts"] == pytest.approx(rb["on"]["p_goal_pct"] - rb["off"]["p_goal_pct"], abs=0.11)
    assert rb["guard_target"] == "lev"                    # the most volatile sleeve
    assert v["stress"]["confidence"] == "shrunk" and v["stress"]["before_p_pct"] is not None
    no_rules = fs.goal_solve(_prep(), sleeves_pct={"lev": 40.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                             cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=30_000,
                             years=5.0, draws=0)
    assert no_rules["rules"] is None


async def test_goal_cards_state_the_rules_and_the_stress_case(own_session):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
    from tests.test_frontier_resize import _store_verdict
    st = {"p_goal_pct": 1.0, "p_halve_pct": 0.0, "median": 30000, "worst5_mean": 20000,
          "exits_per_year": [2.8], "take_profits_per_year": [0.9], "pct_time_out": [62.1],
          "guard_fires_per_year": 0.9}
    off = {**st, "p_goal_pct": 33.0, "p_halve_pct": 40.0, "pct_time_out": [0.0]}
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(
                cur, stress={"confidence": "shrunk", "before_p_pct": 22.9, "after_p_pct": 22.9},
                rules={"on": st, "off": off, "rebound": {**st, "p_goal_pct": 1.5}, "effect_pts": -32.0,
                       "fix_effect_pts": 0.5, "sleeves": {"btm_qqq_hold": {}}}))
            dep = next(x for x in _cards(c) if (x.get("meta") or {}).get("lever") == "deposit")
            assert "Stress case (shrunk estimate" in dep["why"] and "22.9% chance" in dep["why"]
            assert "With your protective rules as they work today: 1% chance" in dep["why"]
            assert "rules off: 33%" in dep["why"]
            assert "Your rules cost this goal 32 points" in dep["why"]
            assert "btm_qqq_hold 62.1%" in dep["why"]
    finally:
        await _reset_plan(own_session)


def test_a_dormant_profit_protect_does_not_count():
    spec = fs.rules_spec([{"ticker": "SOXL", "rule_type": "profit_protect", "mode": "pct", "level": 5.0,
                           "params": {"activated": False}}],
                         prices={"SOXL": 100.0}, sleeve_tickers={"soxl": {"SOXL"}})
    assert spec["sleeves"]["soxl"]["trail"] is None
