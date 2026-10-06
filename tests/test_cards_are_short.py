"""PR 103: shorter cards.

Shape (a guideline, Eran: "word limits are guidelines"): title with its number ->
one or two plain sentences -> "Accept will..." -> before -> after chips -> Details.
The test flags a card that grows FAR past this -- about twice the plan's own
example (~72 words for title + sentences + Accept) -- not one a few words over.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs
from app.services.recommendations import first_sentences
from tests.test_card_claims import BOOKS, _seed
from tests.test_frontier_goal import _goal_verdict, _reset_plan
from tests.test_frontier_goal import _seed as _goal_seed
from tests.test_frontier_resize import _store_verdict

GUIDELINE_WORDS = 72
FLAG_AT = 2 * GUIDELINE_WORDS + 6          # "about 2x"


def _words(*parts) -> int:
    return sum(len(str(p or "").split()) for p in parts)


def test_no_card_grows_far_past_the_shape():
    for name, book in sorted(BOOKS.items()):
        with TestClient(app) as c:
            _seed(c, book)
            r = c.get("/api/v1/recommendations").json()
        for x in (r.get("recommendations") or []) + (r.get("more_recommendations") or []):
            n = _words(x["title"], x.get("summary"), x.get("action"))
            assert n <= FLAG_AT, f"[{name}] '{x['title']}' is {n} words (guideline ~{GUIDELINE_WORDS})"
            assert isinstance(x.get("chips"), list)


def test_the_body_is_the_cards_own_first_sentences():
    assert first_sentences("BND is 12% of the book. It pays 3%. Nobody asked.", 2) == \
        "BND is 12% of the book. It pays 3%."
    assert first_sentences("", 2) == ""


TUNING = {"material": True, "halve_floor_pct": 1.0,
          "current": {"guard_pct": 10.0, "stats": {"p_goal_pct": 1.0, "p_halve_pct": 0.0, "median": 31968,
                                                   "worst5_mean": 22169}},
          "best": {"retire_sleeve_exits": True, "guard_pct": 15.0,
                   "stats": {"p_goal_pct": 16.0, "p_halve_pct": 0.6, "median": 36055, "worst5_mean": 17061}},
          "changes": {"pause_rule_ids": ["a", "b"], "pause_rules": ["SOXL trailing stop", "QQQ trailing stop"],
                      "guard": {"rule_id": "g", "from_pct": 10.0, "to_pct": 15.0}}}
ON = {"p_goal_pct": 1.0, "p_halve_pct": 0.0, "median": 31968, "worst5_mean": 22169,
      "exits_per_year": [2.8, 1.1], "take_profits_per_year": [0.9, 0.2], "pct_time_out": [62.1, 30.0],
      "guard_fires_per_year": 0.9}


async def test_the_goal_group_says_your_goal_once_and_each_card_its_own_claim(own_session):
    try:
        with TestClient(app) as c:
            cur = _goal_seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(
                cur, rules={"on": ON, "off": {**ON, "p_goal_pct": 29.0, "pct_time_out": [0, 0]},
                            "rebound": {**ON, "p_goal_pct": 1.5}, "effect_pts": -28.0, "fix_effect_pts": 0.5,
                            "sleeves": {"btm_trend_soxl": {}, "btm_qqq_hold": {}},
                            "sleeve_ids": ["btm_trend_soxl", "btm_qqq_hold"], "tuning": TUNING},
                headline={"p_goal_pct": 1.0, "without_rules_pct": 29.0, "basis": "rules"}))
            body = c.get("/api/v1/recommendations").json()
        goal = [x for x in (body.get("recommendations") or []) + (body.get("more_recommendations") or [])
                if x.get("dimension") == "goal"]
        assert goal, "fixture sanity: goal cards"
        assert body["goal_summary"] and body["goal_summary"].startswith("Your goal:")
        protect = next(x for x in goal if x["id"].startswith("rec_goal_protect_"))
        # The plan's own example, card by card.
        assert protect["title"] == "Retune your protection: goal odds 1% -> 16%"
        assert protect["summary"].startswith("After a stop fires, the app keeps btm_trend_soxl and btm_qqq_hold out")
        assert protect["summary"].endswith("That costs your goal 28 points.")
        assert protect["action"] == ("Accept will pause 2 stop rules and move the drawdown guard 10% -> 15%. "
                                     "Nothing is bought or sold; one tap re-arms them.")
        assert body["goal_summary_head"].startswith("Your goal: 82,692 by 2031-12-31 (30%/yr")
        assert "Your chance today: 1% with your protective rules as they work (29% without them)." \
            in body["goal_summary_head"]
        assert [ch["label"] for ch in protect["chips"]] == ["odds", "halving", "median", "worst 5%"]
        assert protect["chips"][0] == {"label": "odds", "before": "1%", "after": "16%"}
        for x in goal:
            assert "Your goal:" not in x["summary"], "the goal summary is said once, above the group"
            assert x["chips"], f"{x['title']}: no before -> after chips"
    finally:
        await _reset_plan(own_session)


def test_chip_formats_both_sides_alike():
    assert fs._chip("median", 31968, 36055, fmt="{:,.0f}") == {"label": "median", "before": "31,968",
                                                              "after": "36,055"}
    assert fs._chip("odds", None, 70, unit="%") == {"label": "odds", "before": "-", "after": "70%"}


def test_per_sleeve_figures_keep_their_order_when_postgres_sorts_the_keys():
    """JSONB returns {"btm_trend_soxl", "btm_qqq_hold"} as qqq-first; the time-out
    figures must still belong to the sleeve they were measured on."""
    rb = {"sleeves": {"btm_qqq_hold": {}, "btm_trend_soxl": {}},      # as Postgres hands it back
          "sleeve_ids": ["btm_trend_soxl", "btm_qqq_hold"],
          "on": {"pct_time_out": [62.1, 0.0]}, "effect_pts": -28.0}
    assert fs.rule_sleeve_order(rb) == ["btm_trend_soxl", "btm_qqq_hold"]
    assert fs._protect_summary(rb, {}).startswith("After a stop fires, the app keeps btm_trend_soxl out")
    # a verdict stored before sleeve_ids: the verdict's own sleeve rows (a list) give the order
    old = {k: v for k, v in rb.items() if k != "sleeve_ids"}
    v = {"sleeves": [{"id": "btm_trend_soxl"}, {"id": "btm_qqq_hold"}]}
    assert fs.rule_sleeve_order(old, v) == ["btm_trend_soxl", "btm_qqq_hold"]
