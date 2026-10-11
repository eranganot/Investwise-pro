"""PR 62. Eran, 2026-09-06: *"the app can (and should) recommend me assets from
TASE"*, and then: *"let's start with the Israeli companies for now, add the
Israeli indexes to the backlog."*

**It could not, and the source said it could.** `universe.py` carried a section
headed `# ---- Tel-Aviv (TASE) names (Yahoo .TA suffix) ----` and a module
docstring promising "Tel-Aviv (TASE) names". Every one of its seven entries was
`"market": "NASDAQ"` or `"NYSE"` with no `.TA` suffix — the US ADRs of Israeli
companies. `equity_universe()` is the only thing the screener
(`screener_agent.py:146`) and the signal service (`signal_service.py:152`) rank,
so no shekel-denominated instrument could ever be proposed, on a book denominated
in shekels and taxed under Israeli CGT.

Everything else had been built for weeks: `MARKET_CCY["TASE"] = "ILS"`, the `.TA`
symbol path, the ILA/agorot minor-unit detection and the 100x row repair. Only
the candidate list was missing, which is the part that decides what can ever be
suggested.

**AND ADDING THEM FIRES A GUN V6 SAW LOADED.** `_apply_buy_funded` read
`float(q.price)` and the raw currency code, then `qty = budget / fx_rate(ccy) /
price`. For LUMI.TA that is 7916 agorot treated as 79.16 shekels' worth of price
and an FX code — "ILA" — that no provider knows. V6's PR D called these writers
"a loaded gun, not a firing one… no CSV import is scheduled". Putting Tel Aviv in
the universe is what schedules it, so the writer is fixed in the same PR rather
than after the first wrong purchase.

`broker_sync._to_intake` is the same fault on the aggregator path, and it threw
away a `currency` field `BrokerPosition` has always had — including on the cost
basis, which makes every later CGT estimate wrong too.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import universe as u  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. the label now has something behind it
# --------------------------------------------------------------------------- #
def test_the_screener_can_actually_reach_tel_aviv():
    """`equity_universe()` is the only list the screener ranks."""
    eq = u.equity_universe()
    tase = [r for r in eq if r["market"] == "TASE"]
    assert tase, "the equity universe still contains no TASE listing"
    assert len(tase) >= 12, f"only {len(tase)} TASE names; a ranking needs a cohort"
    for r in tase:
        assert r["ticker"].endswith(".TA"), (
            f"{r['ticker']} is marked TASE but carries no .TA suffix — this is "
            f"exactly the shape the old list had")
        assert r["asset_class"] == "Equities"


def test_no_entry_claims_a_market_its_symbol_contradicts():
    """The defect in one assertion, for every row rather than the ones I fixed.

    The old list said TASE and meant NASDAQ. Nothing structural prevented that,
    so nothing structural prevents it coming back.
    """
    for r in u.full_universe():
        tk, mkt = r["ticker"], r["market"]
        if tk.endswith(".TA"):
            assert mkt == "TASE", f"{tk} trades in Tel Aviv but is filed under {mkt}"
        if mkt == "TASE":
            assert tk.endswith(".TA"), f"{tk} is filed under TASE but is not a TASE symbol"


def test_one_company_does_not_hold_two_slots():
    """TEVA, NICE and ESLT moved to Tel Aviv rather than being duplicated.

    Both listings are real and genuinely different instruments — different
    currency, different tax. But a ranking that carries both gives one company two
    chances at a slot, which is a thumb on the scale rather than a choice.
    """
    tickers = {r["ticker"] for r in u.full_universe()}
    for stem in ("TEVA", "NICE", "ESLT"):
        assert not (stem in tickers and f"{stem}.TA" in tickers), (
            f"{stem} appears twice in one ranking")
        assert f"{stem}.TA" in tickers, f"{stem}.TA is missing"


def test_the_israeli_adrs_that_stayed_are_still_there():
    """Removing the three did not quietly drop the other four."""
    tickers = {r["ticker"] for r in u.full_universe()}
    for tk in ("CHKP", "WIX", "MNDY", "GLOB"):
        assert tk in tickers, f"{tk} was lost"


def test_the_cohort_is_not_six_banks():
    """A ranking over one sector is a sector bet wearing a country's name."""
    names = {r["ticker"] for r in u.equity_universe() if r["market"] == "TASE"}
    # Named rather than counted by sector, because the sector comes from the
    # provider at scan time and this test must not need a network call.
    assert {"TSEM.TA", "NVMI.TA", "CAMT.TA"} & names, "no semiconductors"
    assert {"ICL.TA"} & names, "no materials"
    assert {"BEZQ.TA"} & names, "no telecom"
    assert {"AZRG.TA"} & names, "no real estate"
    assert {"ORA.TA"} & names, "no utilities"
    assert {"TEVA.TA"} & names, "no healthcare"


def test_there_is_no_tase_fund_pretending_to_be_one():
    """The honest half of the answer.

    Israeli index trackers trade under numeric symbols and the provider returns
    Not Found for every one tried; `TA35.TA` resolves as the INDEX, which cannot
    be bought. So this set contains no shekel-denominated ETF, and the class
    fallbacks stay US-listed. A row that claimed otherwise would be the same
    defect this PR is fixing, in the other direction.
    """
    for r in u.full_universe():
        if r["market"] == "TASE":
            assert r["kind"] == "stock", (
                f"{r['ticker']} is filed as {r['kind']}; no TASE fund is priceable")
    from app.services.recommendations import CLASS_ETF
    for tk in CLASS_ETF.values():
        assert not tk.endswith(".TA"), (
            f"{tk} is a class fallback the provider cannot price")


# --------------------------------------------------------------------------- #
# 2. the 100x trap, at the two boundaries that would now hit it
# --------------------------------------------------------------------------- #
def test_the_buy_that_funds_itself_normalises_the_price():
    """`qty = native / price` with an agorot price buys a hundredth of the shares.

    Asserted on the source because the alternative is a live quote in a unit test.
    The behaviour it guards is measured in the docstring above.
    """
    import inspect

    from app.services import recommendations

    src = inspect.getsource(recommendations._apply_buy_funded)
    assert "to_major_units(" in src, (
        "_apply_buy_funded reads the raw quote again; a .TA candidate would book "
        "at 100x its price")
    # `rindex` for the statement, because the comment above the fix quotes it —
    # a plain `index` matches the explanation instead of the code, which is the
    # small version of the mistake this whole file is about.
    i = src.index("to_major_units(")
    j = src.rindex("\n    qty = native / price")
    assert i < j, "the price is normalised after it is used"


def test_a_tase_holding_resolves_to_shekels():
    """The market alone is enough once the price is in the major unit."""
    from app.services.fx import price_currency

    assert price_currency("TASE") == "ILS"
    assert price_currency("NASDAQ") == "USD"
    # And a row written before the fix, still carrying ILA, does not spread it.
    assert price_currency("TASE", {"price_currency": "ILA"}) == "ILS"
