"""PR 77: the review's state per holding, from the two crosses its signals measure."""
from app.services import holding_review as hr
from tests._review_fixtures import down_both, short_dip, up


def test_an_uptrend_is_hold():
    m = hr.metrics(up())
    assert (m["short"], m["long"]) == ("up", "up") and hr.state_of(m) == "Hold"


def test_both_horizons_broken_is_exit():
    m = hr.metrics(down_both())
    assert (m["short"], m["long"]) == ("down", "down") and hr.state_of(m) == "Exit"
    assert m["drawdown_52w_pct"] < 0 and m["ret_1m_pct"] < 0


def test_only_the_short_horizon_broken_is_trim():
    m = hr.metrics(short_dip())
    assert (m["short"], m["long"]) == ("down", "up") and hr.state_of(m) == "Trim"


def test_too_little_history_is_unmeasured():
    m = hr.metrics(up(40))
    assert m["ok"] is False and hr.state_of(m) == "Unmeasured"


def test_the_evidence_bar():
    better = {"cagr_pct": 12.0, "max_drawdown_pct": -20.0}
    hold = {"cagr_pct": 9.0, "max_drawdown_pct": -25.0}
    assert hr.judge(better, hold)["edge"] is True
    assert hr.judge({"cagr_pct": 8.0, "max_drawdown_pct": -10.0}, hold)["edge"] is False
    deeper = hr.judge({"cagr_pct": 12.0, "max_drawdown_pct": -40.0}, hold)
    assert deeper["edge"] is False and "deeper drawdown" in deeper["why"]
    assert hr.judge({"ok": False, "detail": "no history"}, hold)["measured"] is False


def test_the_measurement_runs_the_real_engine_against_holding():
    from tests._review_fixtures import series
    out = hr.measure_ticker("TST", series("TST", up(700)))
    for sig in ("long", "short"):
        assert out[sig]["measured"] is True
        assert out[sig]["hold_cagr_pct"] is not None and out[sig]["rule_cagr_pct"] is not None
    # A clean uptrend: selling on either signal cannot beat holding it.
    assert out["long"]["edge"] is False and out["short"]["edge"] is False
