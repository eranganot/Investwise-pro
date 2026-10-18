"""PR 134d (D101): every other option-3 pass joins the catalogue (owner, 2026-10-18:
"add the passed strategies"); the hot-stock one is Explore only.

Passed on 2026-10-18, taken out of Volatility-Targeted Leverage: D1 (x1, by exactly the
bar -- it failed on 10-16), D2, D3, D4, D7, D9, S2. Already in: D8, D11, D12 (PR 134c).
Failed: D5, D6, D10, D13, S1, S3.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import diversifier_candidates as dc  # noqa: E402
from app.services import round2 as r2  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402

ADDED = ["div_gtaa5", "div_trend_risk_parity", "div_gold_trend", "div_treasury_trend", "div_kmlm",
         "div_qqq_trend_short"]
FAILED = ["div_intl_trend", "div_gem", "div_spy_trend_short", "div_3x_treasury_trend",
          "r2_momentum_10", "r2_52w_high_leaders"]


def test_the_passes_are_in_and_the_failures_are_not():
    ids = cat.active_ids()
    for i in ADDED + ["r2_quality_momentum_5", "div_dbmf", "div_2x_gold_trend", "div_2x_trend_risk_parity"]:
        assert i in ids, i
    for i in FAILED:
        assert i not in ids, i


@pytest.mark.parametrize("sid", ADDED)
def test_each_is_exactly_the_measured_spec(sid):
    measured, live = dc.spec(sid), cat._spec(cat.get(sid))
    for k in ("weights", "overlay"):
        assert live[k] == measured[k], (sid, k)
    assert (live.get("base") or {}) == (measured.get("base") or {})


def test_s2_is_the_measured_spec_less_the_name_with_no_history():
    measured, live = r2.spec("S2"), cat._spec(cat.get("r2_quality_momentum_5"))
    assert live["overlay"]["universe"] == [t for t in measured["overlay"]["universe"] if t != "MMC"]
    for k in ("score", "top_n", "buffer", "cadence_days", "market_gate"):
        assert live["overlay"][k] == measured["overlay"][k], k
    assert live["base"] == {"BIL": 1.0}


@pytest.mark.parametrize("sid", ADDED + ["r2_quality_momentum_5"])
def test_each_is_badged_tiered_and_explained(sid):
    card = next(c for c in cat.as_plan_cards() if c["id"] == sid)
    assert "Hedge for a leveraged sleeve" in card["badges"]
    assert card["risk_label"] and card["rule"] and not card["rule"].startswith("Rule:")
    assert "points a year" in card["description"], "says what it costs the book"


def test_the_borderline_pass_says_so():
    card = next(c for c in cat.as_plan_cards() if c["id"] == "div_gtaa5")
    assert any("Borderline" in b for b in card["badges"])


def test_the_stock_strategy_is_explore_only_and_refused_as_a_new_sleeve():
    card = next(c for c in cat.as_plan_cards() if c["id"] == "r2_quality_momentum_5")
    assert card["explore_only"] is True
    with TestClient(m.app) as c:
        r = c.post("/api/v1/strategies/r2_quality_momentum_5/apply?sleeve_pct=15").json()
    assert r["ok"] is False and r["error"] == "explore only", r
    assert all(not x["explore_only"] for x in cat.as_plan_cards() if x["id"] != "r2_quality_momentum_5")


def test_a_fund_hedge_can_be_added_as_a_sleeve():
    with TestClient(m.app) as c:
        r = c.post("/api/v1/strategies/div_treasury_trend/apply?sleeve_pct=15").json()
    assert r.get("ok"), r
