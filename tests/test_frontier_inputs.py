"""PR 89 - the frontier's inputs: ILS returns, shrunk covariance, Black-Litterman."""
import datetime as dt

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.engines import frontier as fr
from app.main import app
from app.services import frontier_inputs as fi

ASSUMPTIONS = {"us_equity": (7.5, 15.0), "intl_equity": (6.5, 17.0),
               "fixed_income": (3.5, 6.0), "commodities": (4.0, 18.0)}
GROW = {"Equities": 0.80, "Fixed Income": 0.10, "Commodities": 0.10}


def _dates(n):
    d0 = dt.date(2020, 1, 1)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _series(n=600, seed=11, fx=True, drop=()):
    """Correlated daily closes for every proxy ticker, a sleeve ticker and FX."""
    rng = np.random.default_rng(seed)
    tks = ["VTI", "VXUS", "BND", "IAU", "DBC", "TQQQ"]
    mu = np.array([4e-4, 3e-4, 1e-4, 2e-4, 1e-4, 1e-3])
    vol = np.array([0.010, 0.011, 0.004, 0.010, 0.012, 0.035])
    corr = np.full((6, 6), 0.3) + np.eye(6) * 0.7
    corr[0, 5] = corr[5, 0] = 0.9
    cov = corr * np.outer(vol, vol)
    rets = rng.multivariate_normal(mu, cov, size=n)
    dates = _dates(n)
    out = {}
    for j, tk in enumerate(tks):
        if tk in drop:
            continue
        p = 100 * np.cumprod(1 + rets[:, j])
        out[tk] = list(zip(dates, p.tolist()))
    if fx:
        f = 3.6 * np.cumprod(1 + rng.normal(0, 0.004, n))
        out[fi.FX_TICKER] = list(zip(dates, f.tolist()))
    return out


SLEEVE = {"id": "lev", "spec": {"id": "lev", "weights": {"TQQQ": 1.0}}, "current_pct": 40.0}
MEASURED = ({"cagr_pct": 30.0, "volatility_pct": 55.0, "observations": 950,
             "start": "2022-01-03", "end": "2025-10-01"}, None)


def _build(series=None, sleeves=(SLEEVE,), measurements=None, target=GROW):
    return fi.build_inputs(series or _series(), sleeves=list(sleeves), objective_target=target,
                           measurements=measurements if measurements is not None else {},
                           class_assumptions=ASSUMPTIONS, cash_rate_pct=3.0)


# --------------------------------------------------------------------------
# THE PRIOR ADDS NO TILT OF ITS OWN
# --------------------------------------------------------------------------

def test_with_no_views_the_optimiser_hands_back_the_plans_target():
    out = _build(sleeves=())
    assert out["ok"], out
    ids, mu, cov = fi.as_arrays(out)
    lam = out["prior"]["risk_aversion"]
    w = fr.optimize(mu, cov, risk_aversion=lam, free_total=1.0)
    ref = fi.reference_mix(GROW)
    for i, a in enumerate(ids):
        assert w[i] == pytest.approx(ref.get(a, 0.0), abs=2e-4), a


def test_a_sleeve_outside_the_reference_gets_no_weight_from_the_prior():
    """The smoke's check: optimising the PRIOR returns the target, sleeves at 0."""
    out = _build(measurements={"lev": MEASURED})
    ids = [a["id"] for a in out["assets"]]
    prior = np.array([a["prior_drift"] for a in out["assets"]])
    _, _, cov = fi.as_arrays(out)
    w = fr.optimize(prior, cov, risk_aversion=out["prior"]["risk_aversion"], free_total=1.0)
    ref = out["prior"]["reference"]
    assert max(abs(w[i] - ref.get(a, 0.0)) for i, a in enumerate(ids)) < 2e-3
    assert w[ids.index("lev")] == pytest.approx(0.0, abs=2e-3)


def test_the_reference_earns_exactly_the_instrument_tables_premium():
    out = _build(sleeves=())
    ids, mu, _ = fi.as_arrays(out)
    ref = fi.reference_mix(GROW)
    rf = out["risk_free"]["drift"]
    w = np.array([ref.get(a, 0.0) for a in ids])
    stated = sum(ref[c] * (fr.arithmetic_drift(*ASSUMPTIONS[c]) - rf) for c in ASSUMPTIONS)
    assert float(w @ (mu - rf)) == pytest.approx(stated, rel=1e-6)


def test_reference_mix_splits_equities_and_sums_to_one():
    ref = fi.reference_mix(GROW)
    assert ref["us_equity"] == pytest.approx(0.48) and ref["intl_equity"] == pytest.approx(0.32)
    assert sum(ref.values()) == pytest.approx(1.0)
    pres = fi.reference_mix({"Equities": 0.3, "Fixed Income": 0.6, "Cash": 0.1})
    assert pres[fi.CASH_ID] == pytest.approx(0.1)


# --------------------------------------------------------------------------
# Black-Litterman
# --------------------------------------------------------------------------

def test_black_litterman_with_no_views_is_the_prior_and_a_sure_view_wins():
    cov = np.array([[0.04, 0.01], [0.01, 0.02]])
    prior = np.array([0.07, 0.04])
    assert np.allclose(fi.black_litterman(cov, prior, np.zeros((0, 2)), np.array([]),
                                          np.zeros((0, 0))), prior)
    p = np.array([[1.0, 0.0]])
    sure = fi.black_litterman(cov, prior, p, np.array([0.15]), np.array([[1e-12]]))
    assert sure[0] == pytest.approx(0.15, abs=1e-6)
    # ...and the correlated asset is pulled the same way, not left behind.
    assert sure[1] > prior[1]


def test_a_view_on_more_years_moves_the_answer_further():
    base = _build(measurements={"lev": MEASURED})
    short = dict(MEASURED[0], observations=300)
    weak = _build(measurements={"lev": (short, None)})
    prior = next(a for a in base["assets"] if a["id"] == "lev")["prior_drift"]
    target = fr.arithmetic_drift(30.0, 55.0)
    strong_d = next(a for a in base["assets"] if a["id"] == "lev")["drift"]
    weak_d = next(a for a in weak["assets"] if a["id"] == "lev")["drift"]
    assert abs(strong_d - target) < abs(weak_d - target)
    assert min(prior, target) <= weak_d <= max(prior, target)


def test_a_sleeve_without_a_measurement_has_no_view_and_says_why():
    out = _build(measurements={"lev": (None, fi.VIEW_STALE)})
    v = next(v for v in out["views"] if v["asset"] == "lev")
    assert v["used"] is False and v["reason"] == fi.VIEW_STALE
    lev = next(a for a in out["assets"] if a["id"] == "lev")
    assert lev["drift"] == lev["prior_drift"]


@pytest.mark.parametrize("payload,reason", [
    (None, fi.VIEW_NOT_MEASURED),
    ({"ok": False}, fi.VIEW_FAILED),
    ({"ok": True, "engine_version": "old"}, fi.VIEW_STALE),
    ({"ok": True, "engine_version": "a5", "robustness": {}}, fi.VIEW_NO_OOS),
    ({"ok": True, "engine_version": "a5", "robustness": {"out_of_sample": {"out_of_sample": {
        "cagr_pct": 10.0, "volatility_pct": 20.0, "observations": 100}}}}, fi.VIEW_SHORT),
])
def test_every_unusable_measurement_is_named(payload, reason):
    meas, why = fi.view_from_measurement(payload, engine_version="a5", min_observations=260)
    assert meas is None and why == reason


def test_a_usable_measurement_reads_the_out_of_sample_fold():
    payload = {"ok": True, "engine_version": "a5", "metrics": {"cagr_pct": 80.0},
               "robustness": {"out_of_sample": {"out_of_sample": {
                   "cagr_pct": 21.0, "volatility_pct": 50.0, "observations": 950}}}}
    meas, why = fi.view_from_measurement(payload, engine_version="a5", min_observations=260)
    assert why is None and meas["cagr_pct"] == 21.0      # not the 80% full sample


# --------------------------------------------------------------------------
# ILS, cash, exclusions
# --------------------------------------------------------------------------

def test_currency_risk_is_in_the_covariance():
    ils = _build(sleeves=())
    usd = _build(series=_series(fx=False), sleeves=())
    assert ils["fx_basis"] == "historical" and usd["fx_basis"] == "spot"
    assert fi.DEGRADED_FX_SPOT in usd["degraded"] and not ils["degraded"]
    bnd = lambda o: next(a for a in o["assets"] if a["id"] == "fixed_income")["volatility"]  # noqa: E731
    # A low-vol USD bond fund is much riskier to a shekel investor.
    assert bnd(ils) > bnd(usd) * 1.2


def test_cash_is_riskless_in_shekels_and_earns_the_stated_rate():
    out = _build()
    _, mu, cov = fi.as_arrays(out)
    cash = out["assets"][-1]
    assert cash["id"] == fi.CASH_ID and cash["volatility"] == 0.0
    assert np.all(cov[-1] == 0.0) and np.all(cov[:, -1] == 0.0)
    assert mu[-1] == pytest.approx(fr.arithmetic_drift(3.0, 0.0))
    assert cash["cagr_pct"] == pytest.approx(3.0, abs=1e-6)


def test_the_sleeve_is_its_rules_stream_and_sits_in_the_covariance():
    out = _build(measurements={"lev": MEASURED})
    ids, _, cov = fi.as_arrays(out)
    i, j = ids.index("lev"), ids.index("us_equity")
    corr = cov[i, j] / np.sqrt(cov[i, i] * cov[j, j])
    assert corr > 0.7                         # built correlated at 0.9 with VTI
    assert out["assets"][i]["kind"] == "sleeve"


def test_a_missing_ticker_excludes_its_asset_by_name_and_nothing_else():
    out = _build(series=_series(drop=("DBC",)))
    assert out["ok"]
    ids = [a["id"] for a in out["assets"]]
    assert "commodities" not in ids and "us_equity" in ids and "lev" in ids
    assert {"id": "commodities", "reason": "NO_PRICE_HISTORY", "tickers": ["DBC"]} in out["excluded"]


def test_too_short_a_history_refuses():
    out = _build(series=_series(n=100))
    assert out["ok"] is False and out["reason"] == "INSUFFICIENT_HISTORY"


def test_the_covariance_is_shrunk_and_positive_definite_on_the_risky_block():
    out = _build(measurements={"lev": MEASURED})
    _, _, cov = fi.as_arrays(out)
    assert 0.0 <= out["shrinkage"] <= 1.0
    assert np.linalg.eigvalsh(cov[:-1, :-1]).min() > 0


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------

def test_endpoint_returns_inputs_without_touching_the_book(monkeypatch):
    series = _series()
    monkeypatch.setattr(fi, "_fetch_blocking",
                        lambda tks: ({t: series[t] for t in tks if t in series},
                                     sorted(t for t in tks if t not in series), []))
    with TestClient(app) as c:
        before = c.get("/api/v1/mix").json()
        r = c.get("/api/v1/plan/frontier/inputs").json()
        after = c.get("/api/v1/mix").json()
    assert r["ok"] is True, r
    ids = [a["id"] for a in r["assets"]]
    assert ids[-1] == fi.CASH_ID and {"us_equity", "fixed_income"} <= set(ids)
    assert r["fx_basis"] == "historical" and "objective" in r
    assert before == after
