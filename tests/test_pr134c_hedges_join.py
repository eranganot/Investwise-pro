"""PR 134c (D100): the three hedges the owner chose join the catalogue; a universe
member with no price history no longer aborts round 2.

Owner, 2026-10-15/16: of the eight that passed option 3 (funded from the leveraged
sleeve), "only the best 3" -- DBMF, 2x Gold Trend, 2x Trend Risk Parity. Round 2's
S1-S3 never ran: the provider has no history for MMC.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import date, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.engines import strategy_backtest as bt  # noqa: E402
from app.services import diversifier_candidates as dc  # noqa: E402
from app.services import round2 as r2  # noqa: E402
from app.services import sleeve_diversity as div  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402

HEDGES = ["div_dbmf", "div_2x_gold_trend", "div_2x_trend_risk_parity"]


def _series(returns, start=100.0):
    px, out, d0 = start, [], date(2016, 1, 4)
    for i, r in enumerate(returns):
        px *= 1 + r
        out.append(((d0 + timedelta(days=i)).isoformat(), px))
    return out


def _fetch_missing(*gone):
    def fetch(tickers, **kw):
        rng = np.random.default_rng(1)
        return ({t: _series(rng.normal(0.0003, 0.01, 900)) for t in tickers if t not in gone},
                [t for t in tickers if t in gone])
    return fetch


def test_the_three_chosen_hedges_are_in_the_catalogue():
    # PR 134d added the other option-3 passes (owner: "add the passed strategies");
    # which candidates may NOT be there is pinned in test_pr134d_more_hedges.
    ids = cat.active_ids()
    assert all(h in ids for h in HEDGES)


@pytest.mark.parametrize("sid", HEDGES)
def test_each_hedge_is_exactly_the_spec_that_was_measured(sid):
    measured = dc.spec(sid)
    live = cat._spec(cat.get(sid))
    for k in ("weights", "overlay"):
        assert live[k] == measured[k], (sid, k)
    assert (live.get("base") or {}) == (measured.get("base") or {})


@pytest.mark.parametrize("sid", HEDGES)
def test_each_hedge_is_labelled_and_tiered(sid):
    card = next(c for c in cat.as_plan_cards() if c["id"] == sid)
    assert "Hedge for a leveraged sleeve" in card["badges"]
    assert card["risk_label"] in ("Medium", "Medium-high", "High", "Very high")
    assert card["rule"] and not card["rule"].startswith("Rule:")
    if sid == "div_dbmf":
        assert any("Short history" in b for b in card["badges"])


def test_the_hedges_are_a_different_bet_from_the_owners_nasdaq_sleeve():
    for sid in HEDGES:
        ov, _ = div.overlap_pct(cat._spec(cat.get(sid)), cat._spec(cat.get("btm_vol_target_tqqq")))
        assert ov < div.OVERLAP_ALERT_PCT, sid


def test_a_universe_member_with_no_history_is_dropped_and_reported():
    sp, gone = r2.measurable_spec("S1", _fetch_missing("MMC"))
    assert gone == ["MMC"] and "MMC" not in sp["overlay"]["universe"]
    assert len(sp["overlay"]["universe"]) == 114
    assert "MMC" in r2.spec("S1")["overlay"]["universe"], "the registered spec itself is unchanged"


def test_option_3_runs_an_s_candidate_with_a_missing_member():
    book = {"floor": 0.01, "bench_tk": "SPY",
            "core": {"id": "core", "weights": {"VTI": 0.6, "BND": 0.4}, "overlay": {"kind": "buy_hold"}},
            "sleeves": [{"id": r2.LEVERAGED_SLEEVE, "spec": cat._spec(cat.get(r2.LEVERAGED_SLEEVE)),
                         "current_pct": 40.0}]}
    r = r2.evaluate_funding_blocking("S1", book, scale=1, ratio_gain=0.02, drop_tol=2.0,
                                     fetch=_fetch_missing("MMC"))
    assert r["ok"], r
    assert r["universe_dropped"] == ["MMC"]


def test_explore_shows_badges():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    assert "s.badges" in html


def test_the_catalogue_endpoint_serves_the_hedges():
    with TestClient(m.app) as c:
        j = c.get("/api/v1/strategies").json()
    flat = [s for g in (j.get("by_goal") or {}).values() for s in g]
    got = {s["id"]: s for s in flat}
    for sid in HEDGES:
        assert sid in got and got[sid]["badges"], sid
    assert bt.tickers_needed(cat._spec(cat.get("div_2x_trend_risk_parity")))
