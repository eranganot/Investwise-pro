"""PR 164 -- accepting one band card may never fire another (D147).

MEASURED ON PRODUCTION, 2026-11-10 17:27 -> 2026-11-11 17:40 (trade ledger, read-only):

    rule_c4c331da (VXUS band, target 10.8% +-3)  Sell VTI  -> Buy VXUS   x4
    rule_41053012 (VTI band,  target 21.7% +-3)  Sell VXUS -> Buy VTI    x4

16 legs, 10,407 ILS sold, 11.67 ILS estimated tax, and the book back where it
started. Each VXUS card sold ~1,340 of VTI -- the least-taxed equity (+0.8%) --
which took VTI from 21.8% to ~15%, under its 18.7% floor; the VTI card then sold
VXUS (+0.3%) and took it back under 7.8%. Meanwhile XLE and XLF, 6.3% of the
book and in no part of the plan, were never touched: the ignored "Diversify
across regions" card was still built every render and had reserved them in the
shared FundingLedger.

WHY SILENT. Every card was true on its own terms -- "does not move your Equities
weight, swaps which equities you hold". `test_card_claims` measures class mix;
nothing measured what an accept did to the OTHER rules.

The book below is production at 2026-11-11 ~18:56 UTC (`/api/v1/portfolio`),
valued in ILS, gains from the stored basis at the day's USD/ILS 3.172.
"""
from __future__ import annotations

import inspect

from app.services import advanced_rules as ar
from app.services import funding_service as fs

# ticker, value ILS, gain %, class
BOOK = [("BIL", 6721.76, 0.1, "Fixed Income"), ("VTI", 4317.52, 0.8, "Equities"),
        ("XLE", 773.00, 3.9, "Equities"), ("XLF", 467.66, 0.6, "Equities"),
        ("SMH", 3803.66, 6.6, "Equities"), ("QQQ", 2703.09, 4.5, "Equities"),
        ("VXUS", 804.81, 0.3, "Equities")]
CASH = 252.5
TARGET = {"Fixed Income": 0.35, "Equities": 0.6424, "Cash": 0.0076}
# Plan weight per name (sleeves BIL 35 / SMH 10 + core 54.24% of VTI 40, QQQ 25, VXUS 20, SMH 15).
PLAN = {"BIL": 35.0, "SMH": 18.136, "VTI": 21.696, "QQQ": 13.56, "VXUS": 10.848}
BANDED = ("VTI", "QQQ", "VXUS")          # the core names with a +-3 band rule
SLEEVES = {"BIL", "SMH"}                 # PR 163's exclusion
GEO_ID = "rec_54cf60"                    # _rid("divrisk", "geo")


class _Row:
    def __init__(self, ticker, value_ils, gain_pct, asset_class):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0 / (1.0 + gain_pct / 100.0)
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


class _Rule:
    rule_type = "rebalance_band"
    level = 3.0
    active = True
    last_triggered_at = None

    def __init__(self, ticker):
        self.ticker = ticker
        self.id = f"{abs(hash(ticker)) % 10**8:08d}-0000-0000-0000-000000000000"


RULES = [_Rule(t) for t in BANDED]


def _book():
    return {t: [v, g, c] for t, v, g, c in BOOK}


def _nav(book):
    return sum(v for v, _, _ in book.values()) + CASH


def _rows(book):
    return [_Row(t, v, g, c) for t, (v, g, c) in book.items() if v >= 1.0]


def _idx(book):
    nav = _nav(book)
    return {t: {"ticker": t, "market": "NYSE", "weight_pct": v / nav * 100.0, "nav": nav,
                "plan_target_pct": PLAN.get(t), "meta": {"asset_class": c}}
            for t, (v, _, c) in book.items()}


def _band_cards(book, *, plan_aware=True, ledger=None):
    nav = _nav(book)
    rows = _rows(book)
    snap = {"nav": nav, "exposure_ticker": {t: v / nav for t, (v, _, _) in book.items()}}
    idx = _idx(book)
    targets, floors = ar.name_plan(idx, RULES) if plan_aware else (None, None)
    ledger = ledger if ledger is not None else fs.FundingLedger()
    out = []
    for r in RULES:
        c = ar._band_buy_card(r, idx[r.ticker], rows=rows, snap=snap, plan=None,
                              objective="Grow", cap=0.40, cash=CASH, book_target=TARGET,
                              name_caps={}, ledger=ledger, exclude=SLEEVES,
                              name_targets=targets, name_floors=floors)
        if c and (c.get("apply") or {}).get("kind") == "buy_funded":
            out.append(c)
    return out


def _accept(book, card):
    spec = card["apply"]
    book[spec["ticker"]][0] += float(spec["amount_ils"])
    for s in spec.get("sells", []):
        book[s["ticker"]][0] -= float(s["value_ils"])


def _below_band(book):
    nav = _nav(book)
    return {t for t in BANDED if PLAN[t] - book[t][0] / nav * 100.0 > 3.0}


def _sold(card):
    return {s["ticker"] for s in card["apply"].get("sells", [])}


def test_only_vxus_is_below_its_band_today():
    """The fixture is the live book: VXUS 4.1% (floor 7.8), VTI 21.8, QQQ 13.6."""
    assert _below_band(_book()) == {"VXUS"}


def test_the_vxus_card_sells_what_the_plan_does_not_hold():
    """XLE and XLF go first, and nothing banded is touched."""
    [card] = _band_cards(_book())
    assert card["apply"]["ticker"] == "VXUS"
    assert _sold(card) == {"XLE", "XLF"}, card["apply"]["sells"]
    reasons = " ".join(s["reason"] for s in card["apply"]["sells"])
    assert "not part of your plan" in reasons, reasons


def test_accepting_band_cards_reaches_a_fixed_point():
    """THE INVARIANT. Accept every band card Today offers, rebuild, repeat: no
    accept may push another banded name below its band, no card may sell what
    the previous accept bought, and the sequence must end."""
    book = _book()
    bought_last: set[str] = set()
    for _round in range(6):
        cards = _band_cards(book)
        if not cards:
            break
        for card in cards:
            assert not (_sold(card) & bought_last), (card["apply"], bought_last)
            before = _below_band(book)
            _accept(book, card)
            newly = _below_band(book) - before
            assert not newly, f"accepting {card['id']} pushed {newly} below its band"
        bought_last = {c["apply"]["ticker"] for c in cards}
    else:
        raise AssertionError("band cards never settled")
    assert not _below_band(book)


def test_without_the_plan_it_is_the_11_11_loop():
    """The reproduction, so the invariant above is one the code can fail: without
    the name plan the VXUS card sells VTI, and accepting it puts VTI under its band."""
    book = _book()
    [card] = _band_cards(book, plan_aware=False)
    assert "VTI" in _sold(card), card["apply"]["sells"]
    _accept(book, card)
    assert "VTI" in _below_band(book)


def test_with_the_outsiders_spoken_for_no_banded_name_goes_below_its_floor():
    """If XLE and XLF ARE claimed by a card you can see, the band card may still
    use the room inside VTI's and QQQ's bands -- and not a shekel more."""
    book = _book()
    ledger = fs.FundingLedger(shares={"XLE": 773.0, "XLF": 467.66})
    [card] = _band_cards(book, ledger=ledger)
    assert not (_sold(card) & {"XLE", "XLF"})
    _accept(book, card)
    nav = _nav(book)
    for t in ("VTI", "QQQ"):
        assert book[t][0] / nav * 100.0 >= PLAN[t] - 3.0 - 0.01, (t, card["apply"]["sells"])


def test_a_muted_card_reserves_nothing():
    """FundingLedger.skip: a muted owner's commit is a no-op; anyone else's lands."""
    ledger = fs.FundingLedger(skip={GEO_ID})
    fund = {"from_cash_ils": 100.0, "sells": [{"ticker": "XLE", "shares": 3.86}]}
    ledger.commit(fund, owner=GEO_ID)
    assert ledger.shares == {} and ledger.cash_ils == 0.0
    ledger.commit(fund, owner="rec_other")
    ledger.commit(fund)
    assert ledger.shares == {"XLE": 7.72} and ledger.cash_ils == 200.0


def test_propose_funded_buy_resolves_a_callable_owner():
    book = _book()
    nav = _nav(book)
    ledger = fs.FundingLedger(skip={"rec_skipme"})
    buy = fs.propose_funded_buy(rows=_rows(book), snap={"nav": nav}, plan=None,
                                objective="Grow", cap=0.40, book_target=TARGET,
                                ticker="VXUS", buying_class="Equities", cash_ils=CASH,
                                requested_ils=1000.0, exclude=SLEEVES | set(BANDED),
                                allow_within_class_swap=True, ledger=ledger, name_caps={},
                                owner=lambda b: "rec_skipme")
    assert buy is not None and buy.fund["sells"]
    assert ledger.shares == {}


def test_name_plan_floors_are_the_bottom_of_each_band():
    targets, floors = ar.name_plan(_idx(_book()), RULES)
    assert abs(targets["VTI"] - 0.21696) < 1e-9 and abs(floors["VTI"] - 0.18696) < 1e-9
    # A planned name with no band is floored at its target; an off-plan name has neither.
    assert abs(floors["BIL"] - 0.35) < 1e-9 and "XLE" not in targets
    assert ar.name_plan({"VTI": {"plan_target_pct": None}}, RULES) == (None, None)


def test_the_build_seeds_the_skip_set_without_rule_cards():
    """Wiring: Today's ledger skips muted cards, but never a `rule_*` card -- a
    rule comes back on its next firing and must reserve what it spends."""
    from app.services import recommendations as rec
    src = inspect.getsource(rec.build_recommendations)
    i = src.index("_ledger = _fs.FundingLedger()")
    block = src[i:i + 1200]
    assert "_ledger.skip" in block and 'startswith("rule_")' in block, block
    for owner in ('owner=_rid("divrisk", "geo")', 'owner=_rid("warroom_buy", tk)'):
        assert owner in inspect.getsource(rec), owner
