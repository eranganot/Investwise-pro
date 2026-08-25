"""A card may not tell you to buy something that stopped trading in 2023.

Eran: *"I'm always getting the COW recommendation, though the COW price is
irrelevant and not updating for more than 6 months."*

Both halves of that were true and the second explains the first. COW is the
iPath Livestock ETN, redeemed by Barclays; Yahoo answers for it with an empty
series -- not an error, just nothing. And it won the buy card EVERY scan, which
was not luck:

  * `rank_commodities` scores 2:1 trend against cost.
  * `_trend_pct` returns None when there is no history.
  * `_mean` DROPS the missing components instead of penalising them.

So a candidate with no price data is scored on its expense ratio alone. COW's
0.45% is among the cheapest in the catalogue, so a dead ETN beat every live
commodity that happened to be falling -- every time. Absence of evidence was
scoring as strength.

Checked against Yahoo on 2026-08-25: 25 of the 29 catalogue entries returned 251
daily bars ending on the previous close. JJN, JO, NIB and COW returned none.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #
def test_a_the_delisted_etns_are_out_of_the_catalogue():
    from app.services.commodities import CATALOG, DELISTED

    tickers = {c["ticker"] for c in CATALOG}
    for dead in ("COW", "NIB", "JO", "JJN"):
        assert dead not in tickers, f"{dead} is delisted and still recommendable"
        assert dead in DELISTED, f"{dead} was removed without recording why"


def test_b_delisted_and_catalogue_can_never_overlap():
    """The guard that makes re-adding one a test failure rather than a surprise.

    Someone restoring "Cocoa" to the agriculture list a year from now has no way
    to know NIB is dead. This is how they find out.
    """
    from app.services.commodities import CATALOG, DELISTED

    overlap = {c["ticker"] for c in CATALOG} & set(DELISTED)
    assert not overlap, f"these are listed as delisted AND offered for purchase: {overlap}"


def test_c_every_delisted_entry_says_why():
    from app.services.commodities import DELISTED

    for tk, reason in DELISTED.items():
        assert len(reason) > 20, f"{tk} needs a reason, not a label"


def test_d_the_catalogue_still_covers_every_category():
    """Removing four tickers must not silently empty a category.

    JJN was the only Nickel entry and JO the only Coffee one -- so this is a
    real reduction in coverage, not just tidying, and the categories they
    belonged to have to survive on their remaining members.
    """
    from app.services.commodities import CATALOG, CATEGORY_ORDER

    by_cat = {}
    for c in CATALOG:
        by_cat.setdefault(c["category"], []).append(c["ticker"])
    for cat in CATEGORY_ORDER:
        assert by_cat.get(cat), f"category {cat} has no investable entries left"
    # The broad baskets are what a user actually wants for a small sleeve, and
    # they are all alive.
    assert {"DBC", "PDBC", "DBA"} <= {c["ticker"] for c in CATALOG}


# --------------------------------------------------------------------------- #
# the mechanism, so the NEXT one to die is caught without anyone noticing
# --------------------------------------------------------------------------- #
def test_e_a_candidate_with_no_history_is_dropped_not_scored(monkeypatch):
    """THE ACTUAL BUG, independent of which tickers are dead today.

    Scored on the factors that happen to be present, a data-less candidate is
    not comparable to one scored on all of them -- and here it was strictly
    advantaged, because the factor it was missing was the one worth two thirds.
    """
    from app.agents.screener_agent import OpportunityAgent

    agent = OpportunityAgent()
    monkeypatch.setattr(
        "app.services.universe.commodity_candidates",
        lambda: [
            # cheap and dead: this used to win
            {"ticker": "DEAD", "name": "Dead ETN", "market": "NYSE",
             "asset_class": "Commodities", "expense_ratio_pct": 0.10,
             "category": "Agriculture"},
            # dearer, alive, and falling
            {"ticker": "ALIVE", "name": "Live fund", "market": "NYSE",
             "asset_class": "Commodities", "expense_ratio_pct": 0.90,
             "category": "Agriculture"},
        ])

    def _hist(ticker, days=130):
        if ticker == "DEAD":
            raise ValueError(f"insufficient history for '{ticker}'")
        return [("2026-01-01", 100.0), ("2026-08-01", 90.0)]   # down 10%

    monkeypatch.setattr("app.agents.screener_agent.guarded_history", _hist)

    picks = agent.screen_commodities(top_n=5)
    tickers = [p.ticker for p in picks]
    assert "DEAD" not in tickers, "a candidate with no prices was still ranked"
    assert tickers == ["ALIVE"], f"expected only the live candidate, got {tickers}"


def test_f_the_card_abstains_when_nothing_can_be_ranked(monkeypatch):
    """No screen, no card.

    This fell back to a hardcoded "DBC" whenever the screener came back empty,
    so a card headed "Add a commodities sleeve" could name a ticker chosen by no
    process at all -- while reading exactly like the screened recommendation it
    usually is. Now that unpriceable candidates are dropped, an empty result
    means nothing could be evaluated, and the honest response is silence.
    """
    from app.services import recommendations as rec

    class _Agent:
        def screen_commodities(self, top_n=3):
            return []

    monkeypatch.setattr("app.agents.screener_agent.OpportunityAgent", lambda: _Agent())

    class _Pos:
        ticker, market, quantity, current_price, cost_basis = "V", "NASDAQ", 10, 400.0, 340.0
        meta = {"asset_class": "Equities"}

    out = rec._commodity_recs([_Pos()], {}, "Grow", cash_ils=5000.0)
    assert out == [], "a commodities card was emitted with nothing behind it"


def test_g_a_live_screen_still_produces_a_card(monkeypatch):
    """The other half: abstaining must not become the normal case."""
    from app.services import recommendations as rec

    class _Pick:
        ticker, name = "DBC", "Broad commodities (DBC)"

    class _Agent:
        def screen_commodities(self, top_n=3):
            return [_Pick()]

    monkeypatch.setattr("app.agents.screener_agent.OpportunityAgent", lambda: _Agent())

    class _Pos:
        ticker, market, quantity, current_price, cost_basis = "V", "NASDAQ", 10, 400.0, 340.0
        meta = {"asset_class": "Equities"}

    out = rec._commodity_recs([_Pos()], {}, "Grow", cash_ils=5000.0)
    assert len(out) == 1
    assert "DBC" in out[0]["title"]


# --------------------------------------------------------------------------- #
# holding one is not the same as being sold one
# --------------------------------------------------------------------------- #
def test_h_a_legacy_holding_in_a_dead_etn_is_still_classified_correctly():
    """Removing it from the catalogue must not reclassify anyone's position.

    "Do not recommend this" and "this is not a commodity" are different claims.
    If a legacy COW position exists, it is still a commodity for allocation and
    risk purposes -- reclassifying it as an equity would misstate the mix and,
    per strategy_profile's own note, risk-score a commodity as a 32%-volatility
    single name.
    """
    from app.services.allocation_mix import classify

    for tk in ("COW", "NIB", "JO"):
        assert classify(tk, "NYSE") == "Commodities", f"{tk} would be misfiled if held"


@pytest.mark.parametrize("tk", ["DBC", "GLD", "USO", "PDBC", "DBA"])
def test_i_the_survivors_are_the_ones_a_sleeve_would_actually_use(tk):
    from app.services.commodities import CATALOG

    assert tk in {c["ticker"] for c in CATALOG}
