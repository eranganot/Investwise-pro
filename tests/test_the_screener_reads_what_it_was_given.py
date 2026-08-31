"""PR 27 - the screener can see, but three of the numbers it saw were not what
it thought they were.

PR 25 got the equity screener answering again after months of silence. Verified
live on 2026-08-31: 58 of 58 candidates ranked, five equity buy ideas where
there had been none. What that exposed is that the ENGINE consuming those
numbers had never been exercised on real data at scale, and it makes the same
class of mistake PR 25 fixed one layer down -- a missing answer and a bad
answer rendering identically.

Every test here was written against a defect MEASURED on the live top 25, and
every one of them fails on the code as it stood before this file existed.

    BRK-B  #7   pb 0.00096701446   value 98.2   "Trades near book value (P/B 0.0)"
    MCD    #11  pb/roe/debt_to_equity all null  quality 78.3 -- the highest of the four
    JPM    #2   debt_to_equity null             quality 61.0
    BAC    #5   debt_to_equity null
"""
from __future__ import annotations

from app.engines.screener_engine import ScreenerEngine
from app.schemas.screener import Fundamentals

# The helper-level imports live INSIDE the tests that need them, deliberately.
# These tests have to be runnable against the code as it was, to prove they
# fail on it; a module-level import of a helper this PR introduces would turn
# every failure in the file into one collection error and prove nothing.


def _f(ticker: str, **kw) -> Fundamentals:
    """A deliberately unremarkable name. Every test below varies ONE field, so
    whatever moves in the ranking moved because of that field."""
    base = dict(ticker=ticker, name=ticker, sector="Test", pe=15.0, pb=2.0,
                earnings_growth_pct=30.0, revenue_growth_pct=20.0,
                profit_margin_pct=30.0, roe_pct=17.0,
                debt_to_equity=100.0, dividend_yield_pct=2.0)
    base.update(kw)
    return Fundamentals(**base)


def _rank(*funds: Fundamentals):
    picks = ScreenerEngine().rank_equities(
        [{"meta": {"ticker": f.ticker}, "fundamentals": f} for f in funds])
    return {p.ticker: p for p in picks}


# ---------------------------------------------------------------------------
# 1. A missing factor had two opposite meanings depending on its pillar.
# ---------------------------------------------------------------------------

def test_mean_still_drops_none_because_commodities_and_bonds_depend_on_it():
    """The old helper is NOT changed -- `rank_commodities` and `rank_bonds`
    both rely on it and neither has the pillar problem. Pinned so a later
    cleanup does not "simplify" the two into one and silently move the bond
    rankings."""
    from app.engines.screener_engine import _mean

    assert _mean([50.0, None]) == 50.0
    assert _mean([]) == 0.0


def test_a_missing_factor_scores_neutral_instead_of_being_dropped_or_zeroed():
    """The two behaviours that used to depend only on how many factors shared
    the pillar. Both are now the same number, and it is the middle."""
    from app.engines.screener_engine import _NEUTRAL, _pillar

    assert _pillar([50.0, None]) == _NEUTRAL, "a gap in a multi-factor pillar was DROPPED"
    assert _pillar([None]) == _NEUTRAL, "a gap in a single-factor pillar collapsed to the WORST"
    assert _pillar([100.0, None]) == 75.0, "the present factor must still count in full"


def test_an_unmeasured_leverage_cannot_produce_a_perfect_quality_score():
    """JPM (#2 live) and BAC (#5 live) both rank with `debt_to_equity: null`.

    THIS IS THE SHAPE THAT MATTERS, AND TWO EASIER TESTS DO NOT CATCH IT.
    Both were written first, run against the old engine, and went GREEN:

      * three names varying only in leverage -- the old order is already
        correct, because with the other factors tied the dropped slot lands on
        50 by arithmetic accident;
      * the same idea with the unmeasured name TIED at the top on margins and
        ROE -- `_pct_ranks` gives tied values the MEAN of the ranks they span,
        so the "perfect" score is 83.3 and the assertion passes on both.

    Dropping a factor does not score it 50. It scores it at *the mean of
    whatever else is in the pillar*, which is why the defect only shows when
    the surviving factors are top-of-cohort and UNTIED. Then the old pillar
    returned mean(100, 100) = a flat 100 -- a perfect quality score for a
    balance sheet nobody looked at, beating a name that was measured on all
    three and had the best leverage in the set.
    """
    r = _rank(_f("UNKNOWN_DE", profit_margin_pct=61.0, roe_pct=46.0, debt_to_equity=None),
              _f("MEASURED_ALL3", profit_margin_pct=60.0, roe_pct=45.0, debt_to_equity=5.0),
              _f("MIDDLING", profit_margin_pct=20.0, roe_pct=12.0, debt_to_equity=90.0),
              _f("LEVERED", profit_margin_pct=10.0, roe_pct=8.0, debt_to_equity=300.0))

    assert r["UNKNOWN_DE"].factor_scores.quality < 100.0, (
        "a balance sheet nobody measured produced a PERFECT quality score "
        f"({r['UNKNOWN_DE'].factor_scores.quality})")
    # It may still lead -- it is genuinely better on the two factors that were
    # measured -- but the unmeasured slot must cost it the top of the range.
    assert r["UNKNOWN_DE"].factor_scores.quality == 83.3
    assert r["MEASURED_ALL3"].factor_scores.quality == 77.8
    # ...and the gap must not swing the other way into a penalty.
    assert r["UNKNOWN_DE"].factor_scores.quality > r["LEVERED"].factor_scores.quality


def test_an_unknown_dividend_is_not_scored_as_the_worst_payer_in_the_cohort():
    """`income` is a one-factor pillar, so `_mean([None])` returned 0.0 -- the
    same score as the cohort's worst actual payer. BRK-B carried exactly that
    on the live list: `income: 0.0`."""
    r = _rank(_f("PAYS_LITTLE", dividend_yield_pct=0.1),
              _f("UNKNOWN_DY", dividend_yield_pct=None),
              _f("PAYS_A_LOT", dividend_yield_pct=6.0))

    assert r["UNKNOWN_DY"].factor_scores.income != 0.0, (
        "'Yahoo did not say' was rendered identically to 'worst payer here'")
    assert r["UNKNOWN_DY"].factor_scores.income > r["PAYS_LITTLE"].factor_scores.income
    assert r["UNKNOWN_DY"].factor_scores.income < r["PAYS_A_LOT"].factor_scores.income


# ---------------------------------------------------------------------------
# 2. An implausible P/B was ranked as a bargain, and the card said so.
# ---------------------------------------------------------------------------

def test_safe_pb_rejects_the_number_that_ranked_brk_b_seventh():
    """0.00096701446 is Yahoo dividing BRK-B's class-B PRICE by class-A BOOK
    VALUE PER SHARE. The real ratio is about 1.6 -- off by ~1,650x."""
    from app.engines.screener_engine import _safe_pb

    assert _safe_pb(0.00096701446) is None
    # The minor-unit case from PR 25's measurements, which runs the other way:
    # TEVA 5.48 against TEVA.TA 1657.9. Rejecting it here is what stops the Tel
    # Aviv work from importing a 300x error.
    assert _safe_pb(1657.9) is None
    assert _safe_pb(None) is None
    assert _safe_pb(0.0) is None, "negative or zero book equity is not a rankable ratio"
    assert _safe_pb(-3.0) is None
    # Ordinary ratios are untouched, including genuinely expensive ones --
    # `_flags` has the opinion about those, not this guard.
    assert _safe_pb(2.0) == 2.0
    assert _safe_pb(0.3) == 0.3
    assert _safe_pb(43.4375) == 43.4375


def test_an_impossible_pb_does_not_win_the_value_pillar():
    """The whole point. Given a cohort where one name's P/B is broken low, that
    name used to take the best P/B percentile in the set."""
    r = _rank(_f("BROKEN", pb=0.00096701446),
              _f("CHEAP", pb=0.8),
              _f("RICH", pb=30.0))

    assert r["CHEAP"].factor_scores.value > r["BROKEN"].factor_scores.value, (
        "a genuinely cheap name must beat one whose P/B is a unit error")
    assert "partial-data" in r["BROKEN"].flags
    assert any(g.startswith("pb: implausible") for g in r["BROKEN"].data_gaps)


def test_the_card_never_asserts_a_book_value_it_refused_to_score():
    """`_reasons` read the RAW pb while the score read the sanitised one, so
    the engine rejected the number and the sentence quoted it anyway:

        reasons: ["Trades near book value (P/B 0.0)", ...]

    A caption that vouches for a number tells the reader the checking has
    already been done. PR 22 recorded that exact failure on the history chart.
    """
    r = _rank(_f("BROKEN", pb=0.00096701446, pe=8.0),
              _f("MID", pb=5.0), _f("RICH", pb=30.0))

    assert not any("book value" in s for s in r["BROKEN"].reasons), (
        f"the card still quotes the rejected P/B: {r['BROKEN'].reasons}")
    # The raw number stays in `metrics`: it is the audit trail, and a silently
    # nulled field cannot be diagnosed.
    assert r["BROKEN"].metrics["pb"] == 0.00096701446


def test_a_rejected_pb_cannot_call_a_name_expensive_either():
    """`_flags` also read the raw value. A P/B the engine will not rank must
    not be allowed to convict the name of being 'hype-priced' -- the number
    says nothing in either direction."""
    r = _rank(_f("BROKEN_HIGH", pb=9_000.0), _f("A", pb=2.0), _f("B", pb=3.0))
    assert "hype-priced" not in r["BROKEN_HIGH"].flags


# ---------------------------------------------------------------------------
# 3. The gaps are disclosed rather than inferred from a short reasons list.
# ---------------------------------------------------------------------------

def test_data_gaps_names_every_factor_that_did_not_contribute():
    """MCD ranked #11 live with P/B, ROE and debt-to-equity all null -- value
    computed from P/E alone, quality from profit margin alone -- and scored the
    highest quality of the names checked. The only trace was that its reasons
    list was shorter than its neighbours', which is a coincidence, not a
    disclosure."""
    from app.engines.screener_engine import _data_gaps, _safe_pb

    mcd = _f("MCD", pb=None, roe_pct=None, debt_to_equity=None)
    gaps = _data_gaps(mcd, _safe_pb(mcd.pb))

    assert gaps == ["pb: not reported",
                    "roe_pct: not reported",
                    "debt_to_equity: not reported"]

    r = _rank(mcd, _f("FULL_A"), _f("FULL_B"))
    assert "partial-data" in r["MCD"].flags
    assert r["MCD"].data_gaps == gaps
    assert r["FULL_A"].data_gaps == []
    assert "partial-data" not in r["FULL_A"].flags


def test_a_gap_is_labelled_not_penalised():
    """The label must not become a second, hidden penalty -- docking a name for
    its provider's silence is the mirror image of the bug being fixed."""
    r = _rank(_f("GAPPED", debt_to_equity=None), _f("A"), _f("B"))
    # Same composite as a name whose leverage happens to sit mid-cohort.
    assert r["GAPPED"].score == r["A"].score == r["B"].score


# ---------------------------------------------------------------------------
# 4. A non-payer answers zero. The adapter filed that answer as "unknown".
# ---------------------------------------------------------------------------

_QS_NON_PAYER = """{"quoteSummary": {"result": [{
  "summaryProfile": {"longName": "Berkshire Hathaway Inc.", "sector": "Financial Services"},
  "summaryDetail": {"trailingPE": {"raw": 12.7},
                    "dividendYield": {},
                    "trailingAnnualDividendYield": {"raw": 0.0, "fmt": "0.00%"}},
  "defaultKeyStatistics": {"priceToBook": {"raw": 1.61}},
  "financialData": {"profitMargins": {"raw": 0.223}, "debtToEquity": {"raw": 17.14}}
}], "error": null}}"""

_QS_SILENT = """{"quoteSummary": {"result": [{
  "summaryProfile": {"longName": "Quiet Co", "sector": "Test"},
  "summaryDetail": {"trailingPE": {"raw": 12.7}},
  "defaultKeyStatistics": {},
  "financialData": {}
}], "error": null}}"""


def _fundamentals_from(monkeypatch, payload: str):
    from app.providers import live

    monkeypatch.setattr(live.YahooMarketDataProvider, "_crumb", "stub-crumb")
    monkeypatch.setattr(live.YahooMarketDataProvider, "_opener", object())
    monkeypatch.setattr(live, "_http_text",
                        lambda url, timeout=10.0, **kw: payload)
    return live.YahooMarketDataProvider().get_fundamentals("BRK-B")


def test_a_company_that_pays_nothing_reports_zero_not_unknown(monkeypatch):
    """MEASURED against Yahoo on 2026-08-31, for BRK-B and AMZN:

        dividendYield                {}
        trailingAnnualDividendYield  {"raw": 0.0}

    Yahoo drops `dividendYield` for a non-payer and still says, on the same
    object, that the trailing yield is zero. Reading only the first field filed
    a measured zero as "we do not know" -- the conflation PR 25 removed at the
    transport layer, surviving in the field mapping.

    It produced the right ranking by luck: `_mean([None])` collapsed to 0.0 and
    0.0 was the truth. `_pillar` correctly scores an unmeasured factor neutral,
    which would have handed every non-payer an income score it has not earned.
    The fix has to land in the same PR as `_pillar`, not after it.
    """
    f = _fundamentals_from(monkeypatch, _QS_NON_PAYER)
    assert f.dividend_yield_pct == 0.0, (
        "a measured zero yield is still being filed as unknown")


def test_none_still_means_yahoo_did_not_say(monkeypatch):
    """The other half. The fallback must not manufacture a zero out of silence
    -- that would be the same bug pointing the other way."""
    f = _fundamentals_from(monkeypatch, _QS_SILENT)
    assert f.dividend_yield_pct is None
    assert f.debt_to_equity is None
    assert f.pb is None


# ---------------------------------------------------------------------------
# 5. The coverage pin has to survive the scan it exists to explain.
# ---------------------------------------------------------------------------

def test_the_coverage_pin_survives_a_scan_that_ranked_nothing():
    """PR 25's own smoke script records this as a known defect:

        "PR 24 attached the coverage pin only to equity ideas, so it
         disappears in exactly the case worth diagnosing."

    Reproduced against PR 25's bug shape -- the equity leg returns nothing, the
    agent holds {"requested": 58, "ranked": 0}, and the payload said nothing at
    all. `buy_ideas_ranked_for` fell through to `audit_block`, which has no
    `coverage` key.
    """
    from unittest.mock import patch

    from app.services.recommendations import _buy_ideas

    class DeadEquityLeg:
        last_coverage = {"requested": 58, "ranked": 0,
                         "unreachable": [], "no_data": ["AAPL", "MSFT"]}

        def screen_equities(self, weights=None, top_n=5):
            return []

        def screen_commodities(self, top_n=3):
            return []

        def screen_bonds(self, top_n=3, currency="USD"):
            return []

    pin_out: dict = {}
    with patch("app.agents.screener_agent.OpportunityAgent", DeadEquityLeg):
        ideas = _buy_ideas(None, "ILS", "Grow", pin_out)

    assert ideas == [], "the fixture is meant to produce no ideas at all"
    coverage = (pin_out.get("ranked_for") or {}).get("coverage")
    assert coverage is not None, "the coverage vanished with the ideas"
    assert coverage["requested"] == 58
    assert coverage["ranked"] == 0
    assert coverage["no_data"] == ["AAPL", "MSFT"]


def test_the_pin_is_still_published_when_the_scan_worked():
    """The normal path must keep carrying it too, and the two must agree --
    a pin that only appears on failure is its own trap."""
    from unittest.mock import patch

    from app.services.recommendations import _buy_ideas

    class OneEquity:
        last_coverage = {"requested": 58, "ranked": 58, "unreachable": [], "no_data": []}

        def screen_equities(self, weights=None, top_n=5):
            from app.schemas.screener import FactorScores, ScreenPick
            return [ScreenPick(ticker="CVX", name="Chevron", market="NYSE", kind="stock",
                               asset_class="Equities", sector="Energy", score=74.9,
                               factor_scores=FactorScores(), metrics={}, reasons=[])]

        def screen_commodities(self, top_n=3):
            return []

        def screen_bonds(self, top_n=3, currency="USD"):
            return []

    pin_out: dict = {}
    with patch("app.agents.screener_agent.OpportunityAgent", OneEquity):
        ideas = _buy_ideas(None, "ILS", "Grow", pin_out)

    assert ideas[0]["ranked_for"]["coverage"]["ranked"] == 58
    assert pin_out["ranked_for"] == ideas[0]["ranked_for"]
