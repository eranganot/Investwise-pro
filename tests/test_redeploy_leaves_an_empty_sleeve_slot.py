"""PR 155: the cash card does not fill a sleeve's slot with something else.

Reproduced 2026-11-02 on the live book (values from /api/v1/portfolio, class
targets from /api/v1/mix, managed sleeve targets from book_target): the 2x Gold
Trend sleeve was 35% of the plan, its signal was out of gold so its leg was
BIL, and nothing held it. BIL is Fixed Income, so the book target said "35%
Fixed Income" and the cash card proposed ₪5,008 of BND -- a screener pick in
the sleeve's place -- labelled "Core · 30% of book".

Funding a sleeve is "Fund this sleeve"; the cash card keeps that slot's cash.
A stop-held sleeve (TQQQ, D127) is NOT a slot: its share is the core's.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import recommendations as R  # noqa: E402
from app.services import redeploy_research as rr  # noqa: E402
from app.services.card_scope import scope_for  # noqa: E402


class _Row:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


class _Plan:
    cash_target_pct = None
    cash_target_ils = 150.0
    cash_redeploy_at_ils = 1500.0
    currency = "ILS"
    objective = "Grow"
    risk_tolerance = "Medium"


TARGET = {"Equities": 0.6423, "Fixed Income": 0.35, "Cash": 0.0077}
PLANNED = {"BIL": 0.35, "TQQQ": 0.35}      # all_sleeve_targets: holds not applied
MANAGED = {"BIL": 0.35}                    # book_target: TQQQ is stop-held


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_buy_ideas", lambda *a, **k: [{"ticker": "BND", "score": 90}])
    monkeypatch.setattr(rr, "live_scores", lambda *a, **k: {})
    monkeypatch.setattr(rr, "live_trend", lambda tk: {"state": "mixed"})


def _book(cash):
    rows = [_Row("VTI", 5767.83), _Row("QQQ", 3605.74), _Row("VXUS", 2885.6),
            _Row("SMH", 2161.37), _Row("CASH", cash, "Cash")]
    return rows, sum(r.quantity for r in rows)


def _cards(cash, slots=MANAGED):
    rows, nav = _book(cash)
    return R._redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, _Plan(), "Grow",
                                 0.4, cash, TARGET, None, PLANNED, None, 0.0, [],
                                 sleeve_slots=slots)


def _legs(card):
    return {x["ticker"]: x["amount_ils"] for x in card["apply"]["legs"]}


def test_the_live_book_gets_no_bond_card_for_the_gold_sleeves_slot():
    assert _cards(5158.45) == []


def test_without_slots_the_old_card_is_what_it_was():
    # The control: the defect, reproduced, so the test above is not vacuous.
    out = _cards(5158.45, slots=None)
    assert _legs(out[0]) == {"BND": pytest.approx(5008.45)}


def test_cash_beyond_the_slot_still_fills_the_core_and_says_what_it_kept():
    out = _cards(12000.0)
    assert out, "the core is under its 64% target -- that gap is real"
    legs = _legs(out[0])
    assert "BND" not in legs and "BIL" not in legs
    assert set(legs) <= {"VTI", "QQQ", "VXUS", "SMH"}
    kept = out[0]["meta"]["sleeve_slot_kept_ils"]
    nav = _book(12000.0)[1]
    assert kept == pytest.approx(0.35 * nav, abs=1)
    assert out[0]["meta"]["sleeve_slot_tickers"] == ["BIL"]
    assert any("BIL sleeve" in h and "Fund this sleeve" in h for h in out[0]["how"])


def test_a_stop_held_sleeve_keeps_nothing_back():
    # TQQQ is in the planned targets but not the managed ones: the core gap it
    # funds (D127) is bought, not parked for a leg no buyer may buy.
    out = _cards(12000.0, slots={})
    assert out
    assert out[0]["meta"]["sleeve_slot_kept_ils"] == 0


def test_a_core_card_quotes_the_core_it_is_managed_to():
    sc = scope_for({"id": "x", "apply": {"legs": [{"ticker": "VTI"}]}},
                   ticker_owner={"BIL": "div_2x_gold_trend"},
                   sleeve_names={"div_2x_gold_trend": "2x Gold Trend"},
                   sleeve_pcts={"div_2x_gold_trend": 35.0}, core_id="grow_diversified",
                   core_name="Diversified Global Growth", core_pct=64.2)
    assert sc["label"] == "Core · Diversified Global Growth · 64.2% of book"
