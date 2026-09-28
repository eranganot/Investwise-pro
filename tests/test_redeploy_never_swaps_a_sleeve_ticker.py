"""PR 75: a sleeve's instrument is its strategy's -- research never touches it."""
from tests._redeploy_fixtures import S, run


def test_a_sleeve_leg_is_left_alone_even_with_a_far_better_name():
    legs = [{"ticker": "SOXL", "amount_ils": 900.0, "asset_class": "Equities", "reason": "sleeve"}]
    legs, _u, out = run(legs, {"SOXL": S(5), "CRM": S(95)}, sleeves={"SOXL"},
                        held={"SOXL"}, states={"SOXL": "downtrend"})
    assert legs == [{"ticker": "SOXL", "amount_ils": 900.0, "asset_class": "Equities",
                     "reason": "sleeve"}]
    assert out["records"] == []


def test_a_sleeve_ticker_is_never_offered_as_a_candidate():
    legs = [{"ticker": "QQQ", "amount_ils": 900.0, "asset_class": "Equities", "reason": "gap"}]
    legs, _u, out = run(legs, {"QQQ": S(20), "SOXL": S(99)}, sleeves={"SOXL"},
                        held={"QQQ", "SOXL"})
    assert legs[0]["ticker"] == "QQQ"
    assert all(a["ticker"] != "SOXL" for a in out["records"][0]["also_considered"])
