"""D123 (owner, 2026-10-23, BACKLOG P14): two sleeves are the same bet when what they
HOLD when invested moves together, not only when the strategies' own returns do.

Production 10-22: btm_trend_soxl vs btm_vol_target_tqqq measured 0.7804 on the
strategies' returns (under the 0.80 limit: "different bets"), while SOXL and TQQQ
moved 0.95 over the month and fell together on 8 of 8 sessions SOXL dropped more
than 3%. The trend sleeve's time in cash diluted the number; it did not change
what the sleeve was on the day the stop fired.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import numpy as np  # noqa: E402

from app.services import sleeve_diversity as sd  # noqa: E402

A, B = "btm_trend_soxl", "btm_vol_target_tqqq"


def _series(seed=7, n=600, beta=1.0, noise=0.004):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.02, n)
    other = beta * base + rng.normal(0, noise, n)
    dates = [f"2024-{1 + i // 28 % 12:02d}-{1 + i % 28:02d}T{i:05d}" for i in range(n)]

    def px(r):
        return [(d, float(100 * np.prod(1 + r[: i + 1]))) for i, d in enumerate(dates)]
    return {"XXX": px(base), "YYY": px(other)}


def test_the_baskets_correlation_is_measured_on_what_is_held():
    s = _series()
    ra = sd.invested_returns(s, {"weights": {"XXX": 1.0}, "base": {"BIL": 1.0}})
    rb = sd.invested_returns(s, {"weights": {"YYY": 1.0}})
    rho = sd.correlation(ra, rb)
    assert rho is not None and rho > 0.9
    # cash in the basket is not a bet and is left out
    assert sd.invested_returns(s, {"weights": {"BIL": 1.0}}) is None


def test_the_live_pair_is_now_the_same_bet():
    corr = {"pairs": {sd.pair_key(A, B): 0.7804}, "invested_pairs": {sd.pair_key(A, B): 0.95}}
    x = sd.same_bet(A, B, corr)
    assert x is not None, "SOXL-trend and TQQQ vol-target are one bet on the day it matters"
    assert "what they hold when invested moves together (correlation 0.95" in x["why"]
    assert x["correlation"] == 0.78 and x["invested_correlation"] == 0.95


def test_baskets_that_do_not_move_together_are_still_different_bets():
    corr = {"pairs": {sd.pair_key(A, B): 0.70}, "invested_pairs": {sd.pair_key(A, B): 0.72}}
    assert sd.same_bet(A, B, corr) is None
    # and a matrix stored before D123 (no invested_pairs) behaves exactly as before
    assert sd.same_bet(A, B, {"pairs": {sd.pair_key(A, B): 0.7804}}) is None


def test_the_nightly_measurement_stores_both():
    s = _series()
    s2 = {"SOXL": s["XXX"], "TQQQ": s["YYY"], "QQQ": s["YYY"], "BIL": [(d, 100.0) for d, _ in s["XXX"]],
          "SPY": s["YYY"], "SMH": s["XXX"]}

    def fetch(tickers):
        return {t: s2[t] for t in tickers if t in s2}, [t for t in tickers if t not in s2]
    out = sd.measure_blocking([A, B], fetch=fetch)
    assert out["invested_pairs"][sd.pair_key(A, B)] > 0.9, out
