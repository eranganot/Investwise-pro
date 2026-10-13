"""PR 128 -- leverage over a longer history, and risk parity (D92).

The ten-year window (late 2016 on) has no real crash in it, and every strategy
here was designed by someone who had seen it. The long-history test re-runs a
strategy over 2000-2016, rebuilding leveraged funds that did not exist yet from
their index (calibrated on 2011-2026: synthetic TQQQ within 0.6 points a year of
the real fund, daily correlation 0.999). Measured 2026-10-13 on that window:
Trend Nasdaq's worst drop is -85.5%, not the -62% the ten-year card showed,
because its "off" state is QQQ, which itself fell ~83% in 2000-2002.

Plan: docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md. Not financial advice.
"""
import json

import numpy as np
import pytest

from app.engines import strategy_backtest as bt


def _days(n):
    import datetime as dt
    out, d = [], dt.date(2000, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def test_a_synthetic_fund_is_leverage_minus_its_costs():
    d = _days(3)
    under = [(d[0], 100.0), (d[1], 101.0), (d[2], 101.0)]
    rate = [(x, 5.0) for x in d]               # 5% T-bill
    s = bt.synthetic_leveraged(under, 3, rate, fee=0.0095, spread=0.0075)
    day_cost = (0.0095 + 2 * (0.05 + 0.0075)) / 252
    assert s[1][1] == pytest.approx(100 * (1 + 0.03 - day_cost))
    assert s[2][1] == pytest.approx(s[1][1] * (1 - day_cost))


def test_a_fund_cannot_go_below_zero():
    d = _days(3)
    s = bt.synthetic_leveraged([(d[0], 100.0), (d[1], 60.0), (d[2], 60.0)], 3,
                               [(x, 1.0) for x in d])
    assert all(v > 0 for _, v in s)


def test_cash_accrues_the_t_bill_yield():
    d = _days(253)
    s = bt.synthetic_cash([(x, 4.0) for x in d])
    assert s[-1][1] / 100.0 == pytest.approx((1 + 0.04 / 252) ** 253, rel=1e-9)


def test_risk_parity_sends_an_off_leg_to_cash():
    n = 400
    up = np.linspace(100, 200, n)
    down = np.concatenate([np.linspace(100, 150, 250), np.linspace(150, 90, 150)])
    rng = np.random.default_rng(3)
    px = {"SPY": up, "TLT": down,
          "UPRO": 100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)),
          "TMF": 100 * np.cumprod(1 + rng.normal(0.0, 0.01, n)),
          "BIL": np.linspace(100, 101, n)}
    spec = {"weights": {"UPRO": 0.4, "TMF": 0.6},
            "overlay": {"kind": "risk_parity", "legs": {"UPRO": "SPY", "TMF": "TLT"},
                        "cash": "BIL", "vol_days": 60, "trend_days": 200}}
    tg = bt.targets_for(px, spec)
    early, late = tg[260], tg[-1]
    assert set(early) == {"UPRO", "TMF"}
    assert set(late) == {"UPRO", "BIL"}, late
    assert late["BIL"] > late["UPRO"], "TMF, the calmer leg, should carry the larger share"
    assert sum(late.values()) == pytest.approx(1.0)


def test_the_long_history_test_compares_with_the_base(monkeypatch):
    from app.services import backtest_service as bs

    d = _days(5000)
    rng = np.random.default_rng(11)
    qqq = list(100 * np.cumprod(1 + rng.normal(0.0003, 0.015, 5000)))
    rows = {"QQQ": qqq, "SPY": list(100 * np.cumprod(1 + rng.normal(0.0002, 0.01, 5000))),
            "^IRX": [3.0] * 5000}
    got = {t: list(zip(d, v)) for t, v in rows.items()}
    monkeypatch.setattr(bs, "_fetch_long", lambda tickers: (
        {t: got[t] for t in tickers if t in got}, [t for t in tickers if t not in got]))
    spec = {"id": "x", "weights": {"TQQQ": 1.0}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200,
                        "confirm_days": 3}}
    r = bs.long_history(spec, benchmark_ticker="SPY")
    assert r["ok"], r
    assert r["end"] < bs.LONG_END and r["compared_with"] == "its base"
    assert r["passes"] == (r["cagr_pct"] >= r["base_cagr_pct"])
    assert "TQQQ=3xQQQ" in r["rebuilt"]


def test_only_flagged_strategies_pay_for_the_long_history(monkeypatch):
    from app.services import backtest_service as bs

    calls = []
    monkeypatch.setattr(bs, "long_history", lambda spec, **kw: calls.append(spec["id"]) or {"ok": True})
    d = _days(800)
    rng = np.random.default_rng(1)
    monkeypatch.setattr(bs, "_fetch", lambda tickers, **kw: (
        {t: list(zip(d, 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 800)))) for t in tickers}, []))
    bs.measure({"id": "plain", "weights": {"QQQ": 1.0}, "overlay": {"kind": "buy_hold"}})
    bs.measure({"id": "flagged", "weights": {"QQQ": 1.0}, "long_history": True,
                "overlay": {"kind": "buy_hold"}})
    assert calls == ["flagged"]


def test_the_kept_strategies_are_measured_over_the_long_history():
    from app.services import strategy_catalog as cat
    for sid in ("btm_trend_tqqq", "btm_trend_soxl", "btm_vol_target_tqqq", "btm_qqq_hold"):
        assert cat.backtestable([sid])[0].get("long_history") is True, sid


def test_the_provider_refuses_a_monthly_answer(monkeypatch):
    from app.providers import live

    ts = [946684800 + k * 2_629_800 for k in range(120)]          # ten years, monthly
    body = {"chart": {"result": [{"timestamp": ts, "indicators": {
        "adjclose": [{"adjclose": [100.0 + k for k in range(120)]}]}}]}}
    monkeypatch.setattr(live, "_http_text", lambda *a, **k: json.dumps(body))
    with pytest.raises(ValueError, match="granularity"):
        live.YahooMarketDataProvider().get_long_history("SPY")


def test_the_card_shows_the_long_drop():
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "Worst drop incl. 2000–2016" in html
