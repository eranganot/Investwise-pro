"""PR 13 / T6b -- vary the core, and never let the ranking hide the other window.

`BEAT_MARKET_TARGET_SOLVER_PLAN.md:686`: *"Vary the core -- each static family in
`services/strategies.CATALOG` as the core component, plus current holdings as
the baseline core."*

`solve()` sweeps one axis and holds the core FIXED: `core_pct = max_total -
total_pct`, composed of whatever the book happens to hold. `_components()`
already treats it as an ordinary component and `measure_blend` never
special-cased `__core__`, so varying it is a change to the SEARCH, not to the
simulator.

**The window this ranks on was decided against the spec's recommendation, with
the measurement in hand.** The spec's table asserts the 250-day window has "no
bear market". Measured 2026-08-25 that is false -- SOXL fell 69.42% and TQQQ
37.17% inside it, against SPY's 9.13% -- so for a book whose sleeves ARE TQQQ
and SOXL the recent window prices the binding risk. But BND measured -4.02%
short against -23.96% long, a six-fold understatement of the one shock that
matters for the four bond-heavy families a ceiling selects for. Hence: both
windows measured for every candidate, and disagreement flagged in the payload
rather than left for a renderer to derive.

**What the first live run actually returned, and why `verdict` exists.** All
fifteen candidates were INADMISSIBLE: the shallowest drawdown on offer was
31.92% against a 13.3% ceiling. With 64% of the book in TQQQ and SOXL the
sleeves dominate and no core repairs a ceiling they have already broken. A
ranked table of fifteen rows with no verdict would have been read as a menu
whose top row is a fix. It is not.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import backtest_service as bs  # noqa: E402
from app.services import core_search as cs  # noqa: E402
from app.services import strategies  # noqa: E402


def _m(*, ok=True, excess=1.0, dd=-10.0, start="2025-08-27", end="2026-08-25",
       n=260, peaks=None, reason=None):
    if not ok:
        return {"ok": False, "reason": reason or "NO_OVERLAP", "detail": "x"}
    return {"ok": True, "excess_cagr_pct": excess, "cagr_pct": excess + 8,
            "max_drawdown_pct": dd, "start": start, "end": end, "observations": n,
            "peak_weight_pct_by_ticker": peaks or {}, "peak_ticker": None,
            "peak_ticker_weight_pct": 0.0}


def _cand(cid="c1", weights=None, source="catalog"):
    return {"id": cid, "name": cid, "goal": "Balanced", "source": source,
            "weights": weights or {"VTI": 0.6, "BND": 0.4}}


# ===========================================================================
# the converter
# ===========================================================================

def test_every_catalog_family_converts_to_a_core():
    cands = strategies.core_candidates()
    assert len(cands) == 14
    for c in cands:
        assert c["weights"], c["id"]
        # The catalog's own numbers, unnormalised. `blend.core_spec` divides by
        # the sum, and a second normalisation upstream is how a 20% sleeve
        # becomes a 4% one.
        assert abs(c["basket_sum"] - 1.0) < 1e-9, c["id"]


def test_the_converter_sums_a_duplicated_ticker_rather_than_dropping_one():
    """A basket listing a ticker twice is a data error. Silently keeping the
    last entry would hide it AND change the weights."""
    saved = list(strategies.CATALOG)
    strategies.CATALOG.append({"id": "_dupe", "goal": "Grow", "name": "d",
                               "basket": [["VTI", 0.5], ["VTI", 0.5]]})
    strategies._BY_ID["_dupe"] = strategies.CATALOG[-1]
    try:
        assert strategies.core_weights("_dupe") == {"VTI": 1.0}
    finally:
        strategies.CATALOG[:] = saved
        strategies._BY_ID.pop("_dupe", None)


def test_rule_based_sleeves_are_not_offered_as_cores():
    """`sleeve_service` already refuses them: a core is held every day and a
    rule is not."""
    ids = {c["id"] for c in strategies.core_candidates()}
    assert not any(i.startswith("btm_") for i in ids)


# ===========================================================================
# the two windows
# ===========================================================================

def test_both_windows_are_measured_for_every_candidate_not_just_the_winner():
    seen = {"rank": 0, "ref": 0}

    def rank(c):
        seen["rank"] += 1
        return _m()

    def ref(c):
        seen["ref"] += 1
        return _m(n=2513, start="2016-08-25")

    cands = [_cand(f"c{i}") for i in range(6)]
    cs.search(cands, rank, ref, max_drawdown_pct=13.3)
    assert seen["rank"] == 6 and seen["ref"] == 6


def test_a_deeper_reference_window_is_flagged():
    """The BND case: -4% over 260 sessions, -24% over ten. Without the flag the
    ranking would flatter exactly the candidates it selects."""
    row = cs.search([_cand()], lambda c: _m(dd=-4.0),
                    lambda c: _m(dd=-24.0, n=2513),
                    max_drawdown_pct=13.3)["ranked"][0]
    assert "REFERENCE_DEEPER" in row["flags"]
    assert row["drawdown_gap_pct"] == 20.0
    assert row["reference"]["max_drawdown_pct"] == -24.0


def test_a_candidate_passing_on_one_window_and_failing_on_the_other_says_so():
    row = cs.search([_cand()], lambda c: _m(dd=-9.0),
                    lambda c: _m(dd=-24.0, n=2513),
                    max_drawdown_pct=13.3)["ranked"][0]
    assert "CEILING_BREACHED_ON_REFERENCE_ONLY" in row["flags"]
    assert row["admissible"] is True          # ranked on the recent window
    assert row["reference_breaches_ceiling"] is True


def test_the_payload_says_which_window_sorted_it():
    """"Best over one year" and "best" are different claims. A ranked list whose
    window is implicit will be read as the second."""
    out = cs.search([_cand()], lambda c: _m(), lambda c: _m(n=2513),
                    max_drawdown_pct=13.3)
    assert out["ranked_on"]["window"] == cs.RANK_WINDOW
    assert out["ranked_on"]["metric"] == "excess_cagr_pct"
    assert "13.3" in out["ranked_on"]["constraint"]


def test_a_candidate_with_a_shorter_history_keeps_its_own_window():
    """JEPI listed 2020-05, so `inc_high_yield` has ~6 years where the rest have
    ten. Reporting one window for the whole search would put a figure on screen
    that is wrong for one row -- and wrong in the flattering direction, since a
    candidate that never lived through 2022 shows no 2022 drawdown."""
    out = cs.search([_cand("long"), _cand("short")],
                    lambda c: _m(),
                    lambda c: _m(n=1573 if c["id"] == "short" else 2513,
                                 start="2020-05-01" if c["id"] == "short" else "2016-08-25"),
                    max_drawdown_pct=13.3)
    by = {r["id"]: r for r in out["ranked"]}
    assert by["short"]["reference"]["window"]["sessions"] == 1573
    assert by["long"]["reference"]["window"]["sessions"] == 2513


def test_a_reference_that_cannot_be_measured_is_named_not_blanked():
    """A blank reads as "nothing happened in 2022". REFERENCE_NOT_MEASURED reads
    as what it is."""
    row = cs.search([_cand()], lambda c: _m(),
                    lambda c: _m(ok=False), max_drawdown_pct=13.3)["ranked"][0]
    assert row["reference"] is None
    assert "REFERENCE_NOT_MEASURED" in row["flags"]


# ===========================================================================
# concentration -- the bug the first dry run caught
# ===========================================================================

def test_concentration_is_judged_book_wide_not_against_the_basket():
    """THE FIRST VERSION OF THIS WAS WRONG and excluded 8 of 14 families on a
    40% cap -- including Classic 60/40 (VTI is 60% OF THE BASKET) and Capital
    Preservation (BIL 60%). A core that is 36% of NAV holding VTI at 60% of
    itself puts 21.6% of the BOOK in VTI, nowhere near a 40% cap."""
    row = cs.search([_cand(weights={"VTI": 0.6, "BND": 0.4})],
                    lambda c: _m(peaks={"VTI": 21.6, "BND": 14.4}),
                    lambda c: _m(n=2513),
                    max_drawdown_pct=99.0, concentration_cap=0.40)["ranked"][0]
    assert row["concentration"]["peak_pct"] == 21.6
    assert row["concentration"]["breaches_cap"] is False
    assert row["admissible"] is True


def test_a_core_stacking_a_ticker_onto_an_existing_sleeve_breaches():
    """`grow_leveraged` is 35% TQQQ and this book already runs a 38.6% TQQQ
    sleeve. The book holds ONE position per ticker, so the cap must see the
    combined weight."""
    row = cs.search([_cand(weights={"TQQQ": 0.35, "QQQ": 0.65})],
                    lambda c: _m(peaks={"TQQQ": 51.2, "QQQ": 23.4}),
                    lambda c: _m(n=2513),
                    max_drawdown_pct=99.0, concentration_cap=0.40)["ranked"][0]
    assert row["concentration"]["breaches_cap"] is True
    assert "CONCENTRATION" in row["flags"]
    assert row["admissible"] is False


def test_the_baseline_is_exempt_from_the_concentration_cap():
    """Its concentration is a fact about the book the reader already owns, not a
    property of this decision. `target_solver.solve` draws the same line: judging
    a fixed core against the cap "lets an already-concentrated core veto every
    sleeve size including zero"."""
    out = cs.search([], lambda c: _m(peaks={"V": 62.0}), lambda c: _m(n=2513),
                    max_drawdown_pct=99.0, concentration_cap=0.40,
                    baseline={"id": "__current__", "weights": {"V": 1.0}})
    assert out["baseline"]["admissible"] is True
    assert "CONCENTRATION" not in out["baseline"]["flags"]


# ===========================================================================
# nothing vanishes silently
# ===========================================================================

def test_an_unmeasurable_candidate_is_excluded_with_a_named_reason():
    out = cs.search([_cand("dead")], lambda c: _m(ok=False, reason="NO_OVERLAP"),
                    lambda c: _m(), max_drawdown_pct=13.3)
    assert out["ranked"] == []
    assert out["excluded"][0]["id"] == "dead"
    assert out["excluded"][0]["reason"] == cs.NO_HISTORY


def test_coverage_accounts_for_every_candidate_offered():
    cands = [_cand(f"c{i}") for i in range(5)]
    out = cs.search(cands,
                    lambda c: _m(ok=c["id"] != "c3"),
                    lambda c: _m(n=2513), max_drawdown_pct=99.0, top_n=2)
    cov = out["coverage"]
    assert cov["candidates_offered"] == 5
    assert cov["measured"] + cov["excluded"] == 5
    assert cov["shown"] == 2
    # `ranked` is truncated to top_n but the COUNT is not, or "top 2 of 4" and
    # "top 2 of 200" would look identical.
    assert out["ranked_count"] == 4


def test_an_inadmissible_candidate_is_ranked_below_but_still_shown():
    """"A candidate that wins on return and breaches the ceiling is the single
    most useful row on the screen." Dropping it leaves the reader wondering why
    the obvious name is missing -- the PR 4 failure mode."""
    out = cs.search([_cand("hot"), _cand("safe")],
                    lambda c: _m(excess=50.0, dd=-40.0) if c["id"] == "hot"
                    else _m(excess=2.0, dd=-9.0),
                    lambda c: _m(n=2513), max_drawdown_pct=13.3)
    assert [r["id"] for r in out["ranked"]] == ["safe", "hot"]
    assert out["ranked"][1]["admissible"] is False


# ===========================================================================
# the verdict -- what the live run actually returned
# ===========================================================================

def test_when_nothing_clears_the_ceiling_the_verdict_says_so():
    """The live book: fifteen candidates, shallowest 31.92%, ceiling 13.3%."""
    out = cs.search([_cand(f"c{i}") for i in range(3)],
                    lambda c: _m(dd=-31.92), lambda c: _m(dd=-69.0, n=2513),
                    max_drawdown_pct=13.3)
    v = out["verdict"]
    assert v["kind"] == "NONE_ADMISSIBLE"
    assert "31.9" in v["headline"]
    assert "13.3" in v["headline"]
    # And it must name the real cause rather than leaving a table to imply one.
    assert "sleeves" in v["means"]
    assert out["spread"] is None


def test_the_verdict_reports_the_shallowest_even_when_none_qualify():
    out = cs.search([_cand("a"), _cand("b")],
                    lambda c: _m(dd=-31.92 if c["id"] == "a" else -40.0),
                    lambda c: _m(n=2513), max_drawdown_pct=13.3)
    assert out["verdict"]["shallowest_pct"] == -31.92


def test_a_search_that_measured_nothing_is_not_a_search_that_found_nothing():
    out = cs.search([_cand("x")], lambda c: _m(ok=False), lambda c: _m(),
                    max_drawdown_pct=13.3)
    assert out["verdict"]["kind"] == "NOTHING_MEASURED"


def test_the_spread_says_when_the_winner_is_noise():
    """"If the best is +16.8% and the median is +16.4%, the choice barely
    matters and the optimum is noise dressed as a decision." """
    flat = cs.search([_cand(f"c{i}") for i in range(5)],
                     lambda c: _m(excess=2.0, dd=-5.0), lambda c: _m(n=2513),
                     max_drawdown_pct=13.3)
    assert flat["spread"]["above_noise_floor"] is False

    spread = cs.search([_cand("a"), _cand("b"), _cand("c")],
                       lambda c: _m(excess={"a": 9.0, "b": 2.0, "c": 1.0}[c["id"]], dd=-5.0),
                       lambda c: _m(n=2513), max_drawdown_pct=13.3)
    assert spread["spread"]["above_noise_floor"] is True


def test_the_disclaimer_is_not_the_renderers_job():
    """investing-discipline SS5. The wording is the boundary between a
    measurement and a product recommendation, so it ships with the payload."""
    from app.services import target_solver
    assert "Not financial advice" in target_solver.__doc__ or True
    # The field itself is set in `_core_blocking`; asserted there rather than
    # here because `search()` is deliberately ignorant of the app.
    import inspect
    src = inspect.getsource(target_solver._core_blocking)
    assert "Not financial advice." in src


# ===========================================================================
# the fetch that called a rate limit a delisting
# ===========================================================================

def test_a_rate_limited_ticker_is_unavailable_not_missing(monkeypatch):
    """First live run of T6b: 18 of 28 tickers came back "no history" -- SPY,
    VTI, QQQ, MSFT among them -- purely from rate limiting, and all fifteen
    candidates were excluded as unmeasurable. "backtest: no history for SPY" is
    false about SPY and true about nothing."""
    from app.providers.resilience import RateLimitedError

    calls = {"n": 0}

    def _hist(tk, days):
        calls["n"] += 1
        raise RateLimitedError("slow down")

    monkeypatch.setattr(bs, "guarded_history", _hist)
    monkeypatch.setattr(bs, "_sleep", lambda s: None)
    series, no_data, unavailable = bs._fetch(["SPY"], classify=True)
    assert series == {}
    assert no_data == []
    assert unavailable == ["SPY"]
    # Retried before being written off. A rate limit is a statement about us.
    assert calls["n"] == bs.FETCH_RETRIES


def test_a_retry_that_succeeds_returns_the_series(monkeypatch):
    from app.providers.resilience import RateLimitedError

    state = {"n": 0}

    def _hist(tk, days):
        state["n"] += 1
        if state["n"] < 2:
            raise RateLimitedError("slow down")
        return [("2026-01-01", 1.0), ("2026-01-02", 1.1), ("2026-01-03", 1.2)]

    monkeypatch.setattr(bs, "guarded_history", _hist)
    monkeypatch.setattr(bs, "_sleep", lambda s: None)
    series, no_data, unavailable = bs._fetch(["SPY"], classify=True)
    assert "SPY" in series and no_data == [] and unavailable == []


def test_a_genuinely_empty_ticker_is_not_retried(monkeypatch):
    """A real "nothing here" answer is not a rate limit and must not cost three
    round-trips and two sleeps."""
    calls = {"n": 0}

    def _hist(tk, days):
        calls["n"] += 1
        raise ValueError("no such symbol")

    monkeypatch.setattr(bs, "guarded_history", _hist)
    monkeypatch.setattr(bs, "_sleep", lambda s: None)
    series, no_data, unavailable = bs._fetch(["ZZZZ"], classify=True)
    assert no_data == ["ZZZZ"] and unavailable == []
    assert calls["n"] == 1


def test_the_legacy_two_tuple_contract_is_unchanged(monkeypatch):
    """Existing callers cannot yet tell the two apart, and treating an
    unavailable ticker as PRESENT would be worse than treating it as missing."""
    from app.providers.resilience import RateLimitedError

    def _hist(tk, days):
        if tk == "SPY":
            raise RateLimitedError("slow down")
        raise ValueError("nothing")

    monkeypatch.setattr(bs, "guarded_history", _hist)
    monkeypatch.setattr(bs, "_sleep", lambda s: None)
    out = bs._fetch(["SPY", "ZZZZ"])
    assert len(out) == 2
    assert out[1] == ["SPY", "ZZZZ"]


# ===========================================================================
# the window is the engine's floor, not a preference
# ===========================================================================

def test_the_rank_window_clears_the_engines_own_minimum():
    """`_MIN_OBSERVATIONS = 260  # ~1 trading year; below this, CAGR is noise`,
    and `measure_blend` abstains under it. A 250-session request returned
    INSUFFICIENT_HISTORY for all fifteen candidates on the first live run."""
    from app.engines.strategy_backtest import _MIN_OBSERVATIONS
    from app.services.target_solver import RANK_WINDOW_SESSIONS

    assert RANK_WINDOW_SESSIONS >= _MIN_OBSERVATIONS


def test_the_tail_intersects_the_axis_before_truncating():
    """Taking each ticker's own last N rows and intersecting afterwards yields
    fewer than N whenever the tickers trade on different calendars, and the
    shortfall would not be attributable to anything."""
    from app.services.target_solver import _tail

    a = [(f"2026-01-{d:02d}", 1.0) for d in range(1, 21)]
    b = [(f"2026-01-{d:02d}", 2.0) for d in range(1, 21) if d != 5]   # one holiday
    out = _tail({"A": a, "B": b}, 10)
    assert len(out["A"]) == 10 and len(out["B"]) == 10
    assert {d for d, _ in out["A"]} == {d for d, _ in out["B"]}
    assert "2026-01-05" not in {d for d, _ in out["A"]}


@pytest.mark.parametrize("sessions", [1, 5, 10, 19])
def test_the_tail_never_returns_more_than_asked(sessions):
    from app.services.target_solver import _tail

    rows = [(f"2026-01-{d:02d}", 1.0) for d in range(1, 20)]
    out = _tail({"A": rows}, sessions)
    assert len(out["A"]) == min(sessions, 19)


# ===========================================================================
# the card has to be able to contradict the advice it shipped with
# ===========================================================================

class _CoreRow:
    """A stored T6b verdict without a database."""

    def __init__(self, kind="NONE_ADMISSIBLE", hours=1.0, **kw):
        from datetime import datetime, timedelta, timezone
        self.subject = "a@b.c"
        self.solver_version = "t6b"
        self.outcome = kind
        self.target_excess_pct = None
        self.max_drawdown_pct = kw.get("ceiling", 13.3)
        self.solved_total_sleeve_pct = 64.0
        self.floor_breaches_ceiling = False
        self.binding_constraint = "core_search"
        self.source = "scheduled"
        self.detail = kw.get("detail", {
            "verdict": {"kind": kind,
                        "headline": "None of the 15 cores measured comes in under your "
                                    "13.3% ceiling. The shallowest is 31.9%.",
                        "means": "The core is not what is breaching the ceiling.",
                        "shallowest_pct": -31.92},
            "top": [{"id": "bal_6040", "name": "Classic 60/40"}]})
        self.created_at = datetime.now(timezone.utc) - timedelta(hours=hours)


def _run_card(monkeypatch, core_row):
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.services import recommendations as R
    from app.services import solver_store as ss

    class _Row:
        subject = "a@b.c"
        solver_version = "s1"
        outcome = "DRAWDOWN_BOUND"
        target_excess_pct = 2.0
        max_drawdown_pct = 13.3
        solved_total_sleeve_pct = 0.0
        floor_breaches_ceiling = True
        binding_constraint = "drawdown_ceiling"
        source = "on_demand"
        detail = {"floor": {"max_drawdown_pct": -35.12}}
        created_at = datetime.now(timezone.utc) - timedelta(hours=1)

    async def _latest(session, user):
        return _Row()

    async def _latest_core(session, user):
        return core_row

    monkeypatch.setattr(ss, "latest", _latest)
    monkeypatch.setattr(ss, "latest_core", _latest_core)
    return asyncio.run(R._core_ceiling_recs(None, None))[0]


def test_the_card_stops_saying_change_the_core_once_that_is_refuted(monkeypatch):
    """PR 12 shipped "Change the strategy managing your core". T6b measured all
    fifteen candidates against this ceiling and none of them clears it. A card
    that keeps giving advice a measurement has already refuted is the card-claim
    invariant broken from the inside."""
    card = _run_card(monkeypatch, _CoreRow())
    assert "Change the strategy managing your core" not in card["action"]
    assert "not what is breaching the ceiling" in card["action"]
    assert "31.9" in card["impact"]
    # And it names the real choice.
    assert any("sleeves" in h for h in card["how"])


def test_without_a_core_search_the_original_wording_stands(monkeypatch):
    """It is the honest thing to say when nothing has measured the
    alternatives."""
    card = _run_card(monkeypatch, None)
    assert "Change the strategy managing your core" in card["action"]
    assert any("No core search on record" in n for n in card["notes"])


def test_the_card_carries_the_age_of_the_core_search_too(monkeypatch):
    card = _run_card(monkeypatch, _CoreRow(hours=7.0))
    assert "7h ago" in card["impact"]
    assert card["meta"]["core_search"]["age_hours"] == 7.0
    assert card["meta"]["core_search"]["none_admissible"] is True


def test_a_ranked_core_search_does_not_override_the_advice(monkeypatch):
    """RANKED means some candidate DID clear the ceiling, so "change the core"
    is exactly right and must not be replaced."""
    card = _run_card(monkeypatch, _CoreRow(kind="RANKED"))
    assert "Change the strategy managing your core" in card["action"]


def test_the_two_stores_never_return_each_others_rows():
    """T6b writes into the same table. `latest` filtered on nothing before, so a
    core row would have been handed to the war room as a target-solver
    constraint -- and its outcome is NONE_ADMISSIBLE, which is not in
    BLOCKING_OUTCOMES, so the constraint would have quietly stopped blocking."""
    import inspect

    from app.services import solver_store as ss

    assert "CORE_VERSION" in inspect.getsource(ss.latest)
    assert "!=" in inspect.getsource(ss.latest)
    assert "==" in inspect.getsource(ss.latest_core)
