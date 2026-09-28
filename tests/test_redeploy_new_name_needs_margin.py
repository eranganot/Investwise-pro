"""PR 75: a new name replaces a top-up only past the versioned margin, and only
when both were scored on the same factors."""
from app.services import redeploy_research as rr
from tests._redeploy_fixtures import S, run


def leg():
    return [{"ticker": "QQQ", "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]


def test_the_margin_is_a_versioned_unreviewed_assumption():
    assert rr.NEW_NAME_MARGIN["value"] == 10.0 and rr.NEW_NAME_MARGIN["version"] == 1
    assert rr.NEW_NAME_MARGIN["owner_reviewed"] is False
    _l, _u, out = run(leg(), {"QQQ": S(50), "CRM": S(70)})
    assert "redeploy.new_name_margin_pts@1" in out["assumptions"]


def test_past_the_margin_the_leg_becomes_the_new_name():
    legs, _u, out = run(leg(), {"QQQ": S(50), "CRM": S(60.0)})
    assert legs[0]["ticker"] == "CRM" and legs[0]["new_position"] is True
    assert out["records"][0]["decision"] == "swapped"
    assert "10 points above QQQ" in legs[0]["reason"]


def test_inside_the_margin_the_top_up_stays_and_says_why():
    legs, _u, out = run(leg(), {"QQQ": S(50), "CRM": S(59.9)})
    assert legs[0]["ticker"] == "QQQ"
    alt = out["records"][0]["also_considered"][0]
    assert alt["ticker"] == "CRM" and "within the 10-point margin" in alt["lost_because"]


def test_different_factors_are_not_compared():
    """A fund scored without company fundamentals is not beaten by a stock
    scored with them -- the screener's own PR 27 rule."""
    legs, _u, out = run(leg(), {"QQQ": S(30, gaps=["roe: not reported", "eg: none"]),
                                "CRM": S(80)})
    assert legs[0]["ticker"] == "QQQ"
    assert "not comparable" in out["records"][0]["also_considered"][0]["lost_because"]


def test_an_unscored_holding_keeps_its_top_up():
    legs, _u, out = run(leg(), {"CRM": S(90)})
    assert legs[0]["ticker"] == "QQQ"
    assert "no screener score" in out["records"][0]["also_considered"][0]["lost_because"]


def test_a_commodity_pick_never_competes_in_equities():
    """Review finding: GLTR (commodity screen) replaced a QQQ top-up."""
    legs, _u, _o = run(leg(), {"QQQ": S(40), "GLTR": S(79, screen="Commodities")})
    assert legs[0]["ticker"] == "QQQ"
