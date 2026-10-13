"""PR 124 (D86; owner, 2026-10-13): "I would expect that the information on the
goals chances would be also written in the recommendation cards" -- the cards
that PROPOSE rules ("UGA is in an uptrend": arm a trailing stop; "Arm the
discipline for Trend-Filtered Semiconductors").

The goal replay runs with the proposed rules added and again without them, on
the same paths; the card shows the difference and names any proposed rule the
model does not replay (an entry signal is not a sale).
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs

from tests.test_every_rule_shows_its_goal_impact import ROWS, _rules
from tests.test_frontier_resize import EST_KW, _prep


def _solve(rules, light=True):
    prep = _prep()
    return fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                         cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                         draws=0, rules=rules, light=light)


def test_a_proposed_stop_is_compared_with_and_without_on_the_same_paths():
    t = _prep()["ils"].shape[0]
    rules = _rules(t)
    proposed = {"id": "new:0", "ticker": "CVX", "rule_type": "trailing_stop", "mode": "pct", "level": 5.0}
    rules["_rule_rows"] = ROWS + [proposed]
    spec = fs.rules_spec(rules["_rule_rows"], prices={"CVX": 150.0}, sleeve_tickers={"lev": set(), "calm": set()},
                         holding_tickers={"CVX"})
    rules.update({"holdings": spec["holdings"], "sleeves": spec["sleeves"], "_compare_drop": ["new:0"]})
    c = _solve(rules)["rules"]["compare"]
    assert c["paths"] == fs.PER_RULE_PATHS
    assert c["goal_pts"] == round(c["with"]["p_goal_pct"] - c["without"]["p_goal_pct"], 1)
    # The 5% stop is tighter than the existing 8%, so it changes the replay.
    assert c["with"] != c["without"]


def test_without_proposed_rules_there_is_no_comparison():
    assert _solve(_rules(_prep()["ils"].shape[0]))["rules"]["compare"] is None


def test_the_endpoint_passes_the_cards_rules_to_the_preview(monkeypatch):
    seen = {}

    async def _preview(session, user, **kw):
        seen.update(kw)
        return {"ok": True, "compare": None, "proposed": []}
    monkeypatch.setattr(fs, "preview", _preview)
    rules = [{"ticker": "UGA", "rule_type": "trailing_stop", "mode": "pct", "level": 17}]
    with TestClient(app) as c:
        r = c.post("/api/v1/plan/frontier/rule-impact", json={"rules": rules})
        assert r.status_code == 200, r.text
        assert seen["extra_rules"] == rules
        assert c.post("/api/v1/plan/frontier/rule-impact", json={"rules": []}).status_code == 422


def test_today_fills_the_line_on_every_rule_proposing_card():
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "${_ruleCardSlot(o)}" in html and "setTimeout(_fillRuleImpacts" in html
    assert 'fetch("/api/v1/plan/frontier/rule-impact"' in html
    assert "not in the goal model" in html
