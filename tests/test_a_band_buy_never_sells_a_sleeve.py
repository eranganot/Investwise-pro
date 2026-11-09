"""C36 -- a band top-up may not fund itself by selling a sleeve or a core-basket name.

MEASURED ON PRODUCTION, 2026-11-06 (trade ledger, read-only):

    06:32:28  Sell 3.1457 BIL  912.19 ILS  buy_funded  card rule_dfe351f1  (the QQQ band)
              Buy  0.4445 QQQ  912.11 ILS
    06:47     BIL band: "BIL is 30.5% -- below your 35.0%"  -> bought back 433 + 81 from cash
    16:02     QQQ trimmed 750 ILS

BIL is the 2x-gold sleeve's stand-in and sat at its own 35% target. The book
rebuilt from the ledger at 06:32 (BIL 6,725, VTI 3,696, DBMF 2,858, QQQ 2,345,
VXUS 544, SMH 405, cash 2,483 ILS) reproduces the card exactly when the cash is
already committed by an earlier card in the build: sell 916 BIL, buy 916 QQQ.
With the cash free it pays from cash and sells nothing; on the 06:17 book
(before the SMH/VXUS trims) there is no card at all.

WHY IT COULD. Nothing was over target by a minimum trade (Fixed Income 35.3%
vs 35%), so `plan_funding` fell back to "fund from wherever", and the band card
(`extra_cards` -> `_band_buy_card`) was the one funded producer built with no
`exclude` at all. Every other funded card gets `_funding_exclusions` (sleeves +
core basket). The band card gets the SLEEVES only: a band is the per-name
rebalance inside the core, so an over-band core name must stay able to fund an
under-band one (tests/test_advanced_rules.py pins that: BND funds VTI).
"""
from __future__ import annotations

import inspect

from app.services import advanced_rules as ar
from app.services import funding_service as fs


class _Row:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


BOOK = [("BIL", 6725.0, "Fixed Income"), ("VTI", 3696.0, "Equities"), ("DBMF", 2858.0, "Equities"),
        ("QQQ", 2345.0, "Equities"), ("VXUS", 544.0, "Equities"), ("SMH", 405.0, "Equities"),
        ("CASH", 2483.0, "Cash")]
NAV = sum(v for _, v, _ in BOOK)
TARGET = {"Equities": 0.6423, "Fixed Income": 0.35, "Cash": 0.0077}
# The book's sleeve tickers (planned_sleeve_targets on production that morning).
EXCLUDE = {"QQQ", "BIL", "DBMF"}
CORE = {"VTI", "VXUS", "SMH"}


class _Rule:
    id = "dfe351f1-0000-0000-0000-000000000000"
    ticker = "QQQ"
    rule_type = "rebalance_band"
    level = 3.0
    last_triggered_at = None


def _card(exclude):
    rows = [_Row(t, v, c) for t, v, c in BOOK]
    snap = {"nav": NAV, "exposure_ticker": {t: v / NAV for t, v, _ in BOOK if t != "CASH"}}
    pos = {"ticker": "QQQ", "market": "NASDAQ", "weight_pct": 2345.0 / NAV * 100,
           "plan_target_pct": 38.557, "nav": NAV, "meta": {"asset_class": "Equities"}}
    # The cash above the floor is already spoken for by an earlier card in the build.
    ledger = fs.FundingLedger(cash_ils=2483.0)
    return ar._band_buy_card(_Rule(), pos, rows=rows, snap=snap, plan=None, objective="Grow",
                             cap=0.35, cash=2483.0, book_target=TARGET, name_caps={},
                             ledger=ledger, exclude=exclude)


def _sold(card):
    return {str(s.get("ticker")).upper() for s in ((card or {}).get("apply") or {}).get("sells", [])}


def test_the_band_card_sells_no_sleeve():
    """With the sleeves excluded the 11-06 card cannot sell BIL. It is funded from
    the oversized core instead (VTI was 19.4% against a 5.7% band) -- the same
    money the sleeve funding moved 30 seconds later."""
    card = _card(EXCLUDE)
    sold = _sold(card)
    assert not (sold & EXCLUDE), sold
    assert sold and sold <= CORE, (sold, card.get("apply"))


def test_without_the_exclusions_it_is_the_11_06_trade():
    """The reproduction itself: the fallback sells BIL, a sleeve at its target."""
    assert "BIL" in _sold(_card(None))


def test_extra_cards_takes_the_exclusions_and_recommendations_passes_them():
    """The wiring: `extra_cards` accepts `exclude`, and the one call site hands it
    the book's sleeve tickers."""
    assert "exclude" in inspect.signature(ar.extra_cards).parameters
    from app.services import recommendations as rec
    src = inspect.getsource(rec)
    i = src.index("from app.services.advanced_rules import extra_cards")
    call = src[i:i + 600]
    assert "exclude=_band_exclude" in call, call
    assert "planned_sleeve_targets" in src and "_band_exclude" in src
