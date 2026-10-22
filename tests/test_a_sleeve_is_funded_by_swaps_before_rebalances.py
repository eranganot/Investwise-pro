"""PR 138 -- a sleeve top-up sells inside the class it buys before it sells outside it.

Live 2026-10-22: the TQQQ sleeve was 7.3 points short and Plan said "Fund all sleeves".
The funder sold GOOGL (Equities) and then UGA (Commodities, already at target), because
fit-and-tax ranking cannot tell the two apart on a book sitting on its class targets. Its
own drift gate then refused the whole plan, so there was nothing to accept -- while XOM,
an Equities holding, was never touched. A swap moves no weight, so every swap is taken first.
"""
from app.services import funding_service as f


class _Pos:
    def __init__(self, ticker, qty, price, basis, asset_class):
        self.ticker, self.market = ticker, "TASE"
        self.quantity, self.current_price, self.cost_basis = qty, price, basis
        self.meta = {"asset_class": asset_class}


def _book():
    return [_Pos("TQQQ", 1, 7000, 7000, "Equities"), _Pos("SOXL", 1, 5000, 5000, "Equities"),
            _Pos("VTI", 1, 4403, 4403, "Equities"),
            _Pos("GOOGL", 1, 1310, 1331, "Equities"),            # a small loss
            _Pos("XOM", 1, 887, 700, "Equities"),                # a gain: taxed, ranks lower
            _Pos("UGA", 1, 950, 969, "Commodities"),             # a small loss
            _Pos("GLD", 1, 1150, 1150, "Commodities")]


_TARGET = {"Equities": 0.886, "Commodities": 0.10, "Cash": 0.014}
_EXCL = {"TQQQ", "SOXL", "VTI"}


def _plan(**kw):
    return f.plan_funding(_book(), {"nav": 21000, "exposure_ticker": {}}, None, "Grow", 0.40, 2000,
                          cash_ils=300, exclude=_EXCL, buying_class="Equities",
                          book_target=_TARGET, **kw)


def test_without_swap_first_a_commodity_loser_is_sold_to_fund_an_equity_sleeve():
    classes = {s["asset_class"] for s in _plan()["sells"]}
    assert "Commodities" in classes, "the bug this PR fixes no longer reproduces; revisit the test"


def test_with_swap_first_only_the_class_being_bought_is_sold():
    fund = _plan(swap_first=True)
    assert {s["asset_class"] for s in fund["sells"]} == {"Equities"}
    assert "UGA" not in {s["ticker"] for s in fund["sells"]}
    assert "XOM" in {s["ticker"] for s in fund["sells"]}


def test_swap_first_does_nothing_without_a_class_to_buy():
    a = f.plan_funding(_book(), {"nav": 21000, "exposure_ticker": {}}, None, "Grow", 0.40, 2000,
                       cash_ils=300, exclude=_EXCL, book_target=_TARGET)
    b = f.plan_funding(_book(), {"nav": 21000, "exposure_ticker": {}}, None, "Grow", 0.40, 2000,
                       cash_ils=300, exclude=_EXCL, book_target=_TARGET, swap_first=True)
    assert [s["ticker"] for s in a["sells"]] == [s["ticker"] for s in b["sells"]]


def test_the_two_sleeve_funders_ask_for_swap_first():
    import inspect
    from app.services import strategy_service as ss
    assert inspect.getsource(ss._fund_sleeve).count("swap_first=True") == 1
    assert inspect.getsource(ss).count("swap_first=True") == 2
