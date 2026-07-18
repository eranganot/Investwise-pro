"""The Today list must not contradict itself.

Observed live on 2026-07-18: the app simultaneously showed "Sell Cash" and "Buy
Equities" (two legs of ONE rebalance, both applying the identical action), "Put
idle cash to work" (a third card for the same surplus), and "Markets look
risk-off" advising a 5-10% *increase* in cash. Four cards, mutually impossible.
"""
from app.services.recommendations import _reconcile, _rid


def _rebal(cls, amt):
    return {"id": _rid("rebal", cls), "title": f"Buy {cls}", "dimension": "allocation",
            "severity": "MEDIUM", "est_amount": amt, "apply": {"kind": "rebalance_to_objective"}}


def test_multi_leg_rebalance_collapses_to_one_card():
    recs = [_rebal("Cash", -4089), _rebal("Equities", 4089)]
    out = _reconcile(recs)
    assert len(out) == 1
    assert out[0]["title"] == "Rebalance toward your target mix"
    assert out[0]["meta"]["merged_legs"] == ["Buy Cash", "Buy Equities"]


def test_single_leg_rebalance_is_left_alone():
    recs = [_rebal("Equities", 4089)]
    out = _reconcile(recs)
    assert len(out) == 1 and out[0]["title"] == "Buy Equities"


def test_region_and_currency_concentration_merge():
    recs = [{"id": _rid("divrisk", "geo"), "title": "Diversify across regions",
             "dimension": "diversification", "severity": "MEDIUM", "apply": {"kind": "none"}},
            {"id": _rid("divrisk", "cur"), "title": "Diversify your currency exposure",
             "dimension": "diversification", "severity": "MEDIUM", "apply": {"kind": "none"}}]
    out = _reconcile(recs)
    assert len(out) == 1
    assert out[0]["meta"]["merged"] == ["geographic", "currency"]
    assert "region" in out[0]["title"] and "currency" in out[0]["title"]


def test_cash_drag_is_dropped_when_a_rebalance_already_spends_the_cash():
    recs = [_rebal("Equities", 4089),
            {"id": _rid("cashdrag"), "title": "Put idle cash to work",
             "dimension": "income", "severity": "MEDIUM", "apply": {"kind": "none"}}]
    out = _reconcile(recs)
    assert [r["title"] for r in out] == ["Buy Equities"]


def test_cash_drag_survives_when_nothing_else_redeploys_it():
    recs = [{"id": _rid("cashdrag"), "title": "Put idle cash to work",
             "dimension": "income", "severity": "MEDIUM", "apply": {"kind": "none"}}]
    assert len(_reconcile(recs)) == 1


def test_risk_off_stops_telling_you_to_raise_cash_while_deploying_it():
    recs = [_rebal("Equities", 4089),
            {"id": _rid("macro", "riskoff"), "title": "Markets look risk-off",
             "dimension": "macro", "severity": "MEDIUM", "apply": {"kind": "none"},
             "action": "Consider keeping more cash on hand and trimming your most volatile positions.",
             "how": ["Consider raising cash by ~5-10%"]}]
    out = _reconcile(recs, {"rationale": "futures soft"})
    macro = next(r for r in out if r["dimension"] == "macro")
    assert "phase" in macro["title"].lower()
    assert "raising cash" not in " ".join(macro["how"])
    assert "abandon the rebalance" in macro["action"]


def test_risk_off_keeps_its_original_advice_when_no_rebalance_is_pending():
    recs = [{"id": _rid("macro", "riskoff"), "title": "Markets look risk-off",
             "dimension": "macro", "severity": "MEDIUM", "apply": {"kind": "none"},
             "action": "Consider keeping more cash on hand.",
             "how": ["Consider raising cash by ~5-10%"]}]
    out = _reconcile(recs)
    assert out[0]["title"] == "Markets look risk-off"      # untouched
