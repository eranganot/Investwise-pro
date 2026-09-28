"""PR 75: the gap sizes the leg (unchanged); the score picks the name; a
confirmed downtrend is never bought into."""
from app.services import redeploy_research as rr
from tests._redeploy_fixtures import S, run


def test_an_empty_class_gets_its_best_scored_name_not_the_first_idea():
    legs = [{"ticker": "BND", "amount_ils": 800.0, "asset_class": "Fixed Income",
             "reason": "you hold no Fixed Income", "new_position": True}]
    scores = {"BND": S(70, "Fixed Income"), "AGG": S(84, "Fixed Income"),
              "VCIT": S(80, "Fixed Income")}
    legs, _u, out = run(legs, scores, held=set())
    assert legs[0]["ticker"] == "AGG" and legs[0]["amount_ils"] == 800.0, "the gap is the size"
    assert [a["ticker"] for a in out["records"][0]["also_considered"]] == ["VCIT", "BND"]


def test_a_candidate_in_a_confirmed_downtrend_is_listed_and_skipped():
    legs = [{"ticker": "QQQ", "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]
    legs, _u, out = run(legs, {"QQQ": S(40), "CRM": S(90), "V": S(60)},
                        states={"CRM": "downtrend"})
    assert legs[0]["ticker"] == "V"
    lost = {a["ticker"]: a["lost_because"] for a in out["records"][0]["also_considered"]}
    assert "confirmed downtrend" in lost["CRM"]


def test_a_holding_in_a_confirmed_downtrend_is_not_topped_up():
    legs = [{"ticker": "QQQ", "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]
    legs, unplaced, out = run(legs, {"QQQ": S(40)}, states={"QQQ": "downtrend"})
    assert legs == [] and out["records"][0]["decision"] == "held_back"
    assert unplaced[0]["amount_ils"] == 1000.0 and "kept in cash" in unplaced[0]["reason"]


def test_the_trend_rule():
    up = [100 + i * 0.5 for i in range(260)]
    down = [230 - i * 0.5 for i in range(260)]
    assert rr.trend_from_closes(up)["state"] == "uptrend"
    assert rr.trend_from_closes(down)["state"] == "downtrend"
    assert rr.trend_from_closes(up[:100])["state"] == "unmeasured"


def test_a_new_position_in_a_downtrend_is_not_bought_when_the_alternative_has_no_room():
    """Review, CONFIRMED: this fell through to "kept" and bought BND in a downtrend."""
    legs = [{"ticker": "BND", "amount_ils": 800.0, "asset_class": "Fixed Income",
             "reason": "you hold no Fixed Income", "new_position": True}]
    legs, unplaced, out = run(legs, {"BND": S(70, "Fixed Income"), "AGG": S(84, "Fixed Income")},
                              held=set(), states={"BND": "downtrend"}, room=100.0)
    assert legs == [] and out["records"][0]["decision"] == "held_back"
    assert unplaced[-1]["amount_ils"] == 800.0
