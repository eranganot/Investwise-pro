"""PR 10 -- the app could not buy a bond, on any book, ever.

`universe.full_universe()` returned 83 candidates: 64 Equities, 19 Commodities,
**zero Fixed Income** -- on an app whose objective presets have wanted 10-60%
bonds since the first commit:

    Grow      80 / 10 / 10        Preserve  30 / 60 / -
    Balanced  60 / 30 / 10        Income    40 / 50 / 10

So `_redeploy_cash_recs` searched `_buy_ideas` for a candidate whose class was
"Fixed Income", never found one, and recorded the money as `unplaced` with the
reason *"you hold no Fixed Income and no candidate was available"* -- accurately,
on every scan, for every book.

It became urgent when PR 9 fixed the rebalance card: the live core targets 40%
Fixed Income of a 36% core = 14.4% of the book (~3,054 ILS), and the app finally
asked for the right number while still being unable to name a single thing to
buy.

Every ticker in `fixed_income.CATALOG` was verified against the live Yahoo
provider on 2026-08-25: all 24 returned 251 daily bars ending on the previous
close, and the stated durations correlate with measured annualised volatility at
r = 0.94 -- which is the evidence that the catalogue metadata is sane, and the
reason it is still never quoted to the user AS a measurement.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.engines.screener_engine import ScreenerEngine, _pct_ranks  # noqa: E402
from app.services import fixed_income as fi  # noqa: E402
from app.services import universe  # noqa: E402
from app.services.allocation_mix import OBJ_TARGET, classify  # noqa: E402

ENGINE = ScreenerEngine()
ITEMS = [{"meta": c, "vol_pct": 3.0} for c in universe.bond_candidates()]


def _rank(**kw):
    return ENGINE.rank_bonds(ITEMS, **kw)


# --------------------------------------------------------------------------- #
# The gap itself
# --------------------------------------------------------------------------- #
def test_the_universe_can_answer_every_target_the_app_sets():
    """THE defect. Every objective wants bonds; the universe had none.

    Asserted against `OBJ_TARGET` rather than a hardcoded list, so adding an
    objective that wants an asset class the screener cannot source fails here
    rather than in a card that quietly reports `unplaced` forever.
    """
    have = {r["asset_class"] for r in universe.full_universe()}
    wanted = {cls for mix in OBJ_TARGET.values() for cls, w in mix.items()
              if w > 0 and cls.lower() != "cash"}
    missing = wanted - have
    assert not missing, f"objectives target {sorted(missing)} and the universe offers no candidate"


def test_there_are_actually_bonds_in_it():
    bonds = [r for r in universe.full_universe() if r["asset_class"] == "Fixed Income"]
    assert len(bonds) >= 20, f"only {len(bonds)} bond candidates"
    assert all(r["kind"] == "bond" for r in bonds)
    assert {"BND", "AGG", "SGOV", "VGIT"} <= {r["ticker"] for r in bonds}


def test_every_bond_in_the_catalogue_classifies_as_fixed_income():
    """The commodity bug, one asset class over.

    `classify` falls through to "Equities" for anything it does not recognise,
    so a bond ETF missing from `FIXED_INCOME_TICKERS` would be bought, held, and
    counted against the EQUITY target -- the leg looks filled while the gap it
    was meant to close is untouched. Eleven were missing: every Vanguard and
    Schwab ticker, which is to say all the cheap ones.
    """
    wrong = [t for t in fi.tickers() if classify(t, "NASDAQ") != "Fixed Income"]
    assert not wrong, f"these would be counted as Equities: {wrong}"


def test_the_equity_universe_did_not_swallow_the_bonds():
    eq = {r["ticker"] for r in universe.equity_universe()}
    assert not (eq & set(fi.tickers()))


# --------------------------------------------------------------------------- #
# Ranking: duration is a gate, not a weight
# --------------------------------------------------------------------------- #
def test_bonds_are_never_ranked_on_momentum():
    """`rank_commodities` scores 2:1 on trailing trend and copying it here was
    the obvious thing to do. It is backwards: a bond fund's trailing return is
    mostly "what rates did", so ranking on it buys the MOST duration right after
    the move that rewarded it -- maximum sensitivity, worst entry."""
    picks = _rank(want_duration=6.0, top_n=5)
    assert picks
    for p in picks:
        assert "not-momentum-ranked" in p.flags
        assert "trend_pct" not in p.metrics
        assert not any("month" in r.lower() and "up" in r.lower() for r in p.reasons)


@pytest.mark.parametrize("want,expected", [
    (6.0, {"BND", "AGG"}),          # a core's ballast -> the aggregate funds
    (2.0, {"VGSH"}),                # defensive -> short Treasury
    (0.3, {"SGOV"}),                # cash-like -> T-bills, not a 1.9y fund
    (15.0, {"VGLT"}),               # long -> the CHEAP long Treasury, not TLT
])
def test_the_mandate_decides_the_pick(want, expected):
    """Each of these was wrong at some point while this was being built.

    The 0.3-year case is the one that forced the redesign: with duration scored
    as a percentile against cost, SGOV -- a near-exact match -- lost to VGSH,
    which carries NINETEEN TIMES the rate sensitivity, on six basis points of
    fee. No weighting of two percentiles fixes that, because percentile ranks
    discard magnitude. Duration became a gate and cost ranks what survives it.
    """
    top = _rank(want_duration=want, top_n=1)
    assert top and top[0].ticker in expected, (
        f"{want}y mandate picked {top[0].ticker if top else None} "
        f"({top[0].metrics['duration_yrs'] if top else '?'}y), expected one of {expected}")


def test_a_mandate_nothing_matches_widens_and_SAYS_it_widened():
    """Returning nothing would leave the leg unplaced, which is the bug. Silently
    returning a 6-year fund for a 30-year mandate would be worse."""
    picks = _rank(want_duration=30.0, top_n=3)
    assert picks, "an unmatchable mandate returned nothing at all"
    assert all("outside-mandate-band" in p.flags for p in picks)
    assert any("check the duration" in r for r in picks[0].reasons)


def test_cheapest_wins_inside_the_band():
    """VGLT and TLT do the same job; VGLT costs 0.03% and TLT 0.15%."""
    picks = _rank(want_duration=15.0, top_n=2)
    assert picks[0].ticker == "VGLT"
    assert [p.ticker for p in picks][:2] == ["VGLT", "TLT"]


# --------------------------------------------------------------------------- #
# Credit is equity risk wearing a bond's name
# --------------------------------------------------------------------------- #
def test_high_yield_and_em_are_excluded_from_ballast_by_default():
    """A book buying bonds as ballast against a 64% leveraged-equity sleeve and
    getting handed HYG has bought more of what it was hedging. High yield fell
    WITH equities in 2008, 2020 and 2022."""
    # Measured at a duration where each WOULD otherwise qualify, so this tests
    # the credit gate and not the duration gate. HYG/USHY sit at ~3.3y and EMB
    # at ~7y; asking at 6y only would have let the duration band do the work and
    # the assertion would have passed without the credit rule existing at all.
    for want, names in ((3.3, {"HYG", "USHY"}), (7.0, {"EMB"})):
        default = {p.ticker for p in _rank(want_duration=want, top_n=99)}
        asked = {p.ticker for p in _rank(want_duration=want, allow_credit=True, top_n=99)}
        assert not (names & default), f"{names & default} offered as ballast at {want}y"
        assert names <= asked, f"{names - asked} unavailable even when credit was asked for"


def test_credit_is_offered_when_the_caller_actually_asks():
    picks = {p.ticker for p in _rank(want_duration=3.3, allow_credit=True, top_n=99)}
    assert "USHY" in picks
    hy = next(p for p in _rank(want_duration=3.3, allow_credit=True, top_n=99)
              if p.ticker == "USHY")
    assert any("not ballast" in r for r in hy.reasons), "the caveat did not reach the card"


# --------------------------------------------------------------------------- #
# The caveat that scores well on every visible number
# --------------------------------------------------------------------------- #
def test_us_municipals_are_not_offered_to_a_non_usd_book():
    """VTEB charges 0.03% at 6.5 years, so it beats most Treasuries on every
    factor this ranker measures. What it sells is exemption from US federal
    income tax, and that exemption is PRICED INTO THE YIELD -- a non-US holder
    takes the lower yield and gets nothing back. Ranking it first for an ILS
    book is a strictly worse recommendation that scores better."""
    ils = {p.ticker for p in _rank(want_duration=6.0, currency="ILS", top_n=99)}
    usd = {p.ticker for p in _rank(want_duration=6.0, currency="USD", top_n=99)}
    assert not ({"VTEB", "MUB"} & ils)
    assert {"VTEB", "MUB"} <= usd


def test_the_us_only_caveat_is_on_the_catalogue_entry_not_just_in_code():
    for t in ("VTEB", "MUB"):
        assert "non-US" in (fi.get(t) or {}).get("caveat", "")


# --------------------------------------------------------------------------- #
# The percentile helper this uncovered
# --------------------------------------------------------------------------- #
def test_ties_share_a_rank():
    """They did not, and it was not cosmetic: `sorted` breaks ties by list
    position, so eight funds all charging 0.03% were handed percentiles from 100
    down to 70 by their order in a data file. Shared with `rank_equities` and
    `rank_commodities`, so every screen the app has ever run had it."""
    assert _pct_ranks([0.03, 0.03, 0.05, 0.49], higher_is_better=False) == [83.3, 83.3, 33.3, 0.0]
    assert _pct_ranks([5, 5, 5], higher_is_better=True) == [50.0, 50.0, 50.0]


def test_reordering_the_catalogue_cannot_change_the_ranking():
    """The property the tie fix exists for, asserted directly."""
    forward = [(p.ticker, p.score) for p in ENGINE.rank_bonds(ITEMS, want_duration=6.0)]
    backward = ENGINE.rank_bonds(list(reversed(ITEMS)), want_duration=6.0)
    assert dict(forward) == {p.ticker: p.score for p in backward}


# --------------------------------------------------------------------------- #
# No data is not the same as no provider
# --------------------------------------------------------------------------- #
def test_a_transport_failure_does_not_delist_a_ticker():
    """PR 5b dropped any candidate whose history call raised, treating a
    rate-limited call as "this symbol is dead". Reproduced while building PR 10:
    ranking bonds three times in one process rate-limited the later calls,
    twenty of twenty-four candidates silently vanished, and all three rankings
    came back IDENTICAL and wrong -- including a 6-year fund recommended for a
    0.3-year mandate. The ranking changed with transport luck and nothing said
    so.

    The resilience tier already draws this line: a ValueError means the provider
    ANSWERED and the symbol is empty. Everything else is the transport.
    """
    from app.agents.screener_agent import OpportunityAgent
    from app.providers.resilience import CircuitOpenError

    ag = OpportunityAgent()
    calls = {}

    def _fake(ticker, days=252):
        calls[ticker] = calls.get(ticker, 0) + 1
        if ticker in ("BND", "AGG"):
            raise CircuitOpenError("history circuit open")
        if ticker == "TIP":
            raise ValueError(f"no history for '{ticker}'")
        return [("2026-08-24", 100.0 + i * 0.01) for i in range(260)]

    import app.agents.screener_agent as sa
    orig = sa.guarded_history
    sa.guarded_history = _fake
    try:
        picks = {p.ticker for p in ag.screen_bonds(top_n=99, want_duration=6.0)}
    finally:
        sa.guarded_history = orig

    assert {"BND", "AGG"} <= picks, "a circuit-open call deleted two live candidates"
    assert "TIP" not in picks, "the provider said TIP has no history and it was ranked anyway"
    for t in ("BND", "AGG"):
        p = next(x for x in ag.engine.rank_bonds(
            [{"meta": c, "vol_pct": None} for c in universe.bond_candidates()
             if c["ticker"] == t], want_duration=6.0))
        assert p.metrics["vol_pct"] is None, "vol is decorative; the pick survives without it"


def test_a_commodity_the_provider_cannot_reach_is_not_reported_as_dead():
    """Same fix, the screen it was originally broken in."""
    from app.agents.screener_agent import OpportunityAgent
    from app.providers.resilience import RateLimitedError

    ag = OpportunityAgent()

    def _fake(ticker, days=252):
        raise RateLimitedError("slow down")

    import app.agents.screener_agent as sa
    orig = sa.guarded_history
    sa.guarded_history = _fake
    try:
        assert ag.screen_commodities(top_n=3) == [], (
            "a fully rate-limited scan should rank nothing, not rank a lucky subset")
    finally:
        sa.guarded_history = orig


# --------------------------------------------------------------------------- #
# Catalogue hygiene, learned from four delisted ETNs
# --------------------------------------------------------------------------- #
def test_every_entry_carries_what_a_bond_decision_needs():
    for c in fi.CATALOG:
        assert c["duration_yrs"] is not None, f"{c['ticker']} has no duration"
        assert c["expense_ratio_pct"] is not None, f"{c['ticker']} has no expense ratio"
        assert c["credit"], f"{c['ticker']} does not say what credit it takes"
        assert c["category"], c["ticker"]


def test_nothing_delisted_is_in_the_catalogue():
    """`commodities.DELISTED` was created AFTER four dead ETNs had been
    recommended for months. This one exists from day one."""
    assert not (set(fi.DELISTED) & set(fi.tickers()))


def test_duration_buckets_separate_instruments_that_are_not_alike():
    assert fi.duration_bucket(0.1) == "cash-like"
    assert fi.duration_bucket(1.9) == "short"
    assert fi.duration_bucket(6.0) == "intermediate"
    assert fi.duration_bucket(16.5) == "long"
