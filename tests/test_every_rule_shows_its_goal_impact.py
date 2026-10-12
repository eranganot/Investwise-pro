"""PR 123 (D85; owner, 2026-10-12): "you need to add to the rules cards the impact
on the chances of meeting the goals" -- after "I have 30 rules... I need to know
which one of them need my attention" and "why does it not run on all the rules?".

- A HELD ticker with a sale rule is its own column in the goal replay (it was
  folded into the core basket, so 22 of 30 rules were "not simulated").
- Each rule's effect = the replay with that one rule off, on the same paths.
- Rules the model does not replay say why; a rule another rule covers says so.
- GET /rules carries each rule's `goal_impact`.
"""

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_inputs as fi
from app.services import frontier_service as fs

from tests.test_frontier_goal import _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import EST_KW, _prep, _store_verdict


def test_a_held_ticker_is_priced_on_the_solves_calendar():
    prep = {"dates": ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"], "fx_r": np.array([0.0, 0.01, 0.0])}
    rows = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-06", 121.0)]     # 01-05 missing: carried
    r = fi.ticker_ils(prep, rows, "ILS", min_coverage=0.7)
    assert np.allclose(r, [0.10, 0.0, 0.10])
    usd = fi.ticker_ils(prep, rows, "USD", min_coverage=0.7)
    assert np.isclose(usd[1], 0.01)                                # the shekel moved, the price did not
    assert fi.ticker_ils(prep, rows, "USD") is None                # 3 of 4 days < 90%: not guessed
    assert fi.ticker_ils(prep, rows, "EUR", min_coverage=0.5) is None


ROWS = [{"id": "g", "ticker": "__BOOK__", "rule_type": "drawdown_guard", "mode": "pct", "level": 15.0},
        {"id": "t1", "ticker": "CVX", "rule_type": "trailing_stop", "mode": "pct", "level": 8.0},
        {"id": "t2", "ticker": "CVX", "rule_type": "trailing_stop", "mode": "pct", "level": 30.0},
        {"id": "a1", "ticker": "CVX", "rule_type": "price_above", "mode": "price", "level": 200.0}]


def _rules(t):
    spec = fs.rules_spec(ROWS, prices={"CVX": 150.0}, sleeve_tickers={"lev": set(), "calm": set()},
                         holding_tickers={"CVX"})
    rng = np.random.default_rng(9)
    spec.update({"_rule_rows": ROWS, "_prices": {"CVX": 150.0},
                 "_sleeve_tickers": {"lev": set(), "calm": set()},
                 "unmodelled": {"a1": "an alert or a weight rule, not a sale the goal model replays"},
                 "holding_weights": {"CVX": 0.15},
                 "holding_returns": {"CVX": rng.normal(0.0004, 0.03, t)}})
    return spec


def test_rules_spec_models_a_held_ticker_instead_of_calling_it_unsimulated():
    spec = fs.rules_spec(ROWS, prices={"CVX": 150.0}, sleeve_tickers={"lev": set()}, holding_tickers={"CVX"})
    assert spec["holdings"]["CVX"]["trail"] == 0.08                 # the tightest stop wins, as for sleeves
    assert spec["holdings"]["CVX"]["exit_rule_ids"] == ["t1", "t2"]
    assert not any("CVX" in x for x in spec["not_simulated"])


def test_every_rule_gets_its_own_effect_or_its_reason():
    prep = _prep()
    rules = _rules(prep["ils"].shape[0])
    v = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                      draws=0, rules=rules)
    rb = v["rules"]
    assert rb["holdings_modelled"] == ["CVX"]
    pr = rb["per_rule"]["rules"]
    assert pr["t1"]["modelled"] and not pr["t1"]["covered"] and isinstance(pr["t1"]["goal_pts"], float)
    assert pr["t2"] == {"modelled": True, "covered": True, "goal_pts": 0.0,
                        "halve_with_pct": pr["t2"]["halve_with_pct"], "halve_without_pct": pr["t2"]["halve_with_pct"]}
    assert pr["a1"] == {"modelled": False, "reason": "an alert or a weight rule, not a sale the goal model replays"}
    assert pr["g"]["modelled"] and pr["g"]["halve_without_pct"] >= pr["g"]["halve_with_pct"]   # the guard protects
    assert rb["per_rule"]["paths"] == fs.PER_RULE_PATHS


def test_a_light_preview_models_the_holding_but_skips_the_per_rule_pass():
    prep = _prep()
    v = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                      draws=0, rules=_rules(prep["ils"].shape[0]), light=True)
    assert v["rules"]["holdings_modelled"] == ["CVX"] and v["rules"]["per_rule"] is None


def test_take_profits_count_in_the_rules_fingerprint():
    assert "take_profit" in fs.FP_RULE_TYPES


async def test_the_rules_tab_shows_each_rules_goal_impact(own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule, delete_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        r = await create_rule(s, user, ticker="QQQ", rule_type="trailing_stop", mode="pct", level=10.0)
        await s.commit()
        rid = str(r.id)
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            st = {"p_goal_pct": 20.0, "p_halve_pct": 1.0, "median": 1, "worst5_mean": 1,
                  "exits_per_year": [0.0], "pct_time_out": [0.0]}
            await _store_verdict(own_session, _goal_verdict(cur, rules={
                "on": st, "off": st, "rebound": st, "effect_pts": 0.0, "fix_effect_pts": 0.0,
                "sleeves": {"btm_qqq_hold": {}},
                "per_rule": {"paths": 500, "base_goal_pct": 20.0, "rules": {
                    rid: {"modelled": True, "covered": False, "goal_pts": -3.4,
                          "halve_with_pct": 1.0, "halve_without_pct": 6.0}}}}))
            j = c.get("/api/v1/rules").json()
            row = next(x for x in j["rules"] if str(x["id"]) == rid)
            assert row["goal_impact"]["goal_pts"] == -3.4
            assert j["goal_impact"]["base_goal_pct"] == 20.0
        html = open("app/static_app/index.html", encoding="utf-8").read()
        assert "function _ruleImpact" in html and "function _costliest" in html
    finally:
        async with own_session() as s:
            await delete_rule(s, await ensure_superadmin(s), rid)
        await _reset_plan(own_session)


def test_the_retune_can_pause_a_held_tickers_take_profit():
    """Holdings join the retune: their take-profits are searched like a sleeve's (D82)."""
    sl = [{"col": 0, "id": "lev", "trail": None}, {"col": 1, "id": "CVX", "trail": None, "tp_ratio": 1.1}]
    rules = {"sleeves": {"lev": {}}, "holdings": {"CVX": {"tp_rule_ids": ["tp9"], "rules": ["take_profit CVX (+10%)"]}},
             "guard": {"rule_id": "g1"}}

    def run(sl=None, gd=None, **kw):
        tp_on = any(s.get("tp_ratio") for s in sl)
        return {"p_goal_pct": 10.0 if tp_on else 17.0, "p_halve_pct": 0.5, "median": 1, "worst5_mean": 1}
    g = {"level": 0.15, "rearm": 0.125, "fraction": 0.25, "col": 0}
    t = fs._tune_protection(run, sl, g, rules, ["lev"], np.zeros((10, 2)), 1, run(sl=sl), floor=1.0)
    assert t["material"] and t["best"]["pause_take_profits"] == ["CVX"]
    assert t["changes"]["pause_rule_ids"] == ["tp9"]
