"""PR 129 -- does a strategy make the whole book better? (owner, 2026-10-13)

"You can proceed with option A ... but I want to add to the test also the
'make the whole book better' parameter." Recorded and shown beside the gate; it
does not change who passes. The rule was fixed and sent to the owner before
anything was measured: return per unit of volatility up by >= 0.02, worst drop
no more than 2 points deeper.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import numpy as np
import pytest

from app.services import book_effect as be
from app.services import strategy_candidates as sc
from app.services import strategy_catalog as cat


def _days(n):
    import datetime as dt
    out, d = [], dt.date(2015, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _fetcher(paths):
    d = _days(len(next(iter(paths.values()))))

    def fetch(tickers, **kw):
        return ({t: list(zip(d, map(float, paths[t]))) for t in tickers if t in paths},
                [t for t in tickers if t not in paths])
    return fetch


def _book(core_w=None, sleeves=()):
    return {"sleeves": list(sleeves), "floor": 0.03, "bench_tk": "SPY",
            "core": {"id": "core", "weights": core_w or {"CORE": 1.0},
                     "overlay": {"kind": "buy_hold"}}}


def _walk(n, drift, vol, seed):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(drift, vol, n))


N = 900


def test_a_calm_diversifier_improves_a_volatile_book():
    rng_core = _walk(N, 0.0006, 0.02, 1)
    calm = _walk(N, 0.0005, 0.004, 2)                    # uncorrelated, much calmer
    fetch = _fetcher({"CORE": rng_core, "CALM": calm, "SPY": _walk(N, 0.0004, 0.01, 3)})
    cand = {"id": "x", "weights": {"CALM": 1.0}, "overlay": {"kind": "buy_hold"}}
    r = be.compare_blocking(_book(), cand, 30, fetch=fetch)
    assert r["ok"], r
    assert r["improves"] and r["return_per_risk_gain"] >= be.RATIO_GAIN
    assert r["sleeve_pct"] == 30 and not r["already_held"]


def test_a_wilder_copy_of_the_core_does_not():
    core = _walk(N, 0.0006, 0.015, 1)
    wild = 100 * np.cumprod(1 + 3 * (np.diff(np.insert(core, 0, 100)) / np.insert(core, 0, 100)[:-1]))
    fetch = _fetcher({"CORE": core, "WILD": wild, "SPY": _walk(N, 0.0004, 0.01, 3)})
    cand = {"id": "x", "weights": {"WILD": 1.0}, "overlay": {"kind": "buy_hold"}}
    r = be.compare_blocking(_book(), cand, 30, fetch=fetch)
    assert r["ok"] and r["improves"] is False
    assert r["worst_drop_change_pts"] > be.DD_TOLERANCE_PTS


def test_the_room_comes_from_the_core_and_never_beyond_it():
    fetch = _fetcher({"CORE": _walk(N, 0.0005, 0.01, 1), "S": _walk(N, 0.0005, 0.01, 2),
                      "C": _walk(N, 0.0005, 0.01, 3), "SPY": _walk(N, 0.0004, 0.01, 4)})
    sleeve = {"id": "s1", "spec": {"id": "s1", "weights": {"S": 1.0}, "overlay": {"kind": "buy_hold"}},
              "current_pct": 90.0}
    cand = {"id": "x", "weights": {"C": 1.0}, "overlay": {"kind": "buy_hold"}}
    r = be.compare_blocking(_book(sleeves=[sleeve]), cand, 30, fetch=fetch)
    assert r["ok"] and r["sleeve_pct"] == pytest.approx(7.0)    # 100 - 3 floor - 90


def test_a_held_strategy_is_compared_with_the_book_without_it():
    fetch = _fetcher({"CORE": _walk(N, 0.0005, 0.01, 1), "S": _walk(N, 0.0008, 0.01, 2),
                      "SPY": _walk(N, 0.0004, 0.01, 4)})
    spec = {"id": "s1", "weights": {"S": 1.0}, "overlay": {"kind": "buy_hold"}}
    r = be.compare_blocking(_book(sleeves=[{"id": "s1", "spec": spec, "current_pct": 20.0}]),
                            spec, 20, fetch=fetch)
    assert r["ok"] and r["already_held"]
    assert r["with"]["cagr_pct"] != r["without"]["cagr_pct"]


def test_a_missing_ticker_is_said_not_scored():
    fetch = _fetcher({"CORE": _walk(N, 0.0005, 0.01, 1), "SPY": _walk(N, 0.0004, 0.01, 4)})
    r = be.compare_blocking(_book(), {"id": "x", "weights": {"NOPE": 1.0},
                                      "overlay": {"kind": "buy_hold"}}, 20, fetch=fetch)
    assert r["ok"] is False and "NOPE" in r["detail"]


def test_a_proxy_rule_enters_the_book_as_its_proxy():
    assert be._engine_spec({"id": "m", "weights": {}, "proxy": {"ticker": "QVAL"},
                            "overlay": {"kind": "buy_hold"}})["weights"] == {"QVAL": 1.0}


def test_a_late_listing_does_not_cut_the_blend():
    from app.engines import blend
    d = _days(N)
    young = [(x, 100.0 + k) for k, x in enumerate(d[600:])]
    series = {"CORE": list(zip(d, map(float, _walk(N, 0.0005, 0.01, 1)))), "Y": young,
              "Z": list(zip(d, map(float, _walk(N, 0.0006, 0.01, 2))))}
    sel = {"id": "s", "weights": {}, "base": {"CORE": 1.0},
           "overlay": {"kind": "rank_select", "universe": ["Y", "Z"], "score": "ret",
                       "lookback_days": 21, "top_n": 1}}
    core = {"id": "core", "weights": {"CORE": 1.0}, "overlay": {"kind": "buy_hold"}}
    m = blend.measure_blend(series, [{"id": "core", "spec": core, "weight": 0.5},
                                     {"id": "s", "spec": sel, "weight": 0.5}], detail=False)
    assert m["ok"] and m["observations"] == N, m.get("observations")


def test_the_candidates_are_the_ones_the_catalogue_did_not_take():
    ids = {c["id"] for c in sc.CANDIDATES}
    assert len(ids) == 15                     # round 1's ten, round 2's R1-R5 (PR 130)
    assert not ids & set(cat.ids()), "a candidate is also in the catalogue"
    assert {c["code"] for c in sc.CANDIDATES} == {"M1", "M2", "M3", "MH1", "MH2", "H1", "H2",
                                                  "H3", "VH1", "VH2", "R1", "R2", "R3", "R4", "R5"}
    assert sc.spec("btm_magic_formula")["proxy"] == {"ticker": "QVAL"}


def test_both_kinds_resolve():
    assert be._lookup("btm_qld_trend")[0]["weights"] == {"QLD": 1.0}
    assert be._lookup("btm_sector_leaders")[1]["code"] == "H3"
    assert be._lookup("nope") == (None, None)


def test_the_routes_exist_and_the_literal_one_comes_first():
    import app.main as m
    paths = [r.path for r in m.app.routes if hasattr(r, "path")]
    lit = paths.index("/api/v1/strategies/candidates/book-effect")
    var = paths.index("/api/v1/strategies/{strategy_id}/book-effect")
    assert lit < var


def test_the_card_fills_it_in_after_render():
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "function fillBookEffects" in html and "/book-effect" in html
