"""PR 76 (Eran, 2026-09-28, option B): a fund top-up is only replaced by a fund,
a stock by a stock. Option A (any name past the margin) is in the backlog."""
from tests._redeploy_fixtures import S, run


def leg(tk="QQQ"):
    return [{"ticker": tk, "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]


def test_a_fund_top_up_is_not_replaced_by_a_higher_scoring_stock():
    """The preview that raised it: CRM 75 replaced a QQQ top-up at 41."""
    legs, _u, out = run(leg(), {"QQQ": S(41, kind="etf"), "CRM": S(75, kind="stock")})
    assert legs[0]["ticker"] == "QQQ"
    alt = out["records"][0]["also_considered"][0]
    assert alt["ticker"] == "CRM" and "like for like" in alt["lost_because"]


def test_a_fund_top_up_can_be_replaced_by_a_better_fund():
    legs, _u, out = run(leg(), {"QQQ": S(41, kind="etf"), "CRM": S(90, kind="stock"),
                                "SCHD": S(60, kind="etf")})
    assert legs[0]["ticker"] == "SCHD"
    lost = {a["ticker"]: a["lost_because"] for a in out["records"][0]["also_considered"]}
    assert "like for like" in lost["CRM"]


def test_a_fund_in_a_downtrend_with_no_fund_alternative_stays_in_cash():
    legs, unplaced, out = run(leg(), {"QQQ": S(41, kind="etf"), "CRM": S(90, kind="stock")},
                              states={"QQQ": "downtrend"})
    assert legs == [] and out["records"][0]["decision"] == "held_back"
    assert unplaced[0]["amount_ils"] == 1000.0


def test_a_fund_in_a_downtrend_moves_to_a_fund():
    legs, _u, out = run(leg(), {"QQQ": S(41, kind="etf"), "CRM": S(95, kind="stock"),
                                "SCHD": S(50, kind="etf")}, states={"QQQ": "downtrend"})
    assert legs[0]["ticker"] == "SCHD" and out["records"][0]["decision"] == "swapped"


def test_the_fund_alternative_is_found_behind_three_better_scoring_stocks():
    """Review of PR 76, CONFIRMED: the kind filter ran after the 3-candidate cut,
    so SCHD (39 past the margin) was never looked at."""
    legs, _u, out = run(leg(), {"QQQ": S(41, kind="etf"), "CRM": S(95), "AAPL": S(94),
                                "MSFT": S(93), "SCHD": S(80, kind="etf")})
    assert legs[0]["ticker"] == "SCHD"
    lost = {a["ticker"]: a["lost_because"] for a in out["records"][0]["also_considered"]}
    assert "like for like" in lost["CRM"] and "like for like" in lost["AAPL"]


def test_a_class_you_hold_nothing_in_takes_the_best_name_of_any_kind():
    legs = [{"ticker": "VTI", "amount_ils": 800.0, "asset_class": "Equities",
             "reason": "none held", "new_position": True}]
    legs, _u, _o = run(legs, {"VTI": S(60, kind="etf"), "CRM": S(84, kind="stock")}, held=set())
    assert legs[0]["ticker"] == "CRM"
