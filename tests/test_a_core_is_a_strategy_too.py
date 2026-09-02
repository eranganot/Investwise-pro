"""PR 37 -- the book is managed by two strategies and only one was measured.

Eran, 2026-09-02, reading the line PR 36 shipped:

    *"that's not true.... the sleeves have their strategy and the core has its
     own strategy. the portfolio is being managed by two different strategies
     (sleeves strategy and core strategy)"*

He is right, and this repo already agreed with him everywhere except the one
place it mattered. `core_search` ranks FOURTEEN candidate cores on measured
return, volatility and drawdown, through `blend.core_spec` +
`blend.measure_blend`. His core is `bal_6040`: a basket plus a rebalance
discipline, sized at 27% of the book.

But `backtest_service.refresh_all` only ever walked `strategy_catalog` -- the
RULE-BASED sleeves -- so no core had a stored measurement, and
`book_assumptions` never asked for the core row at all. Every unclaimed holding
went through the per-ticker instrument table, which is why his book read 79%
measured.

THE FALLBACK WAS NOT NEUTRAL. A per-ticker table has no covariance term, so
pricing a rebalanced 60/40 as the weighted average of a VTI row and a BND row
overstates its volatility by construction: the one thing rebalancing between two
weakly-correlated legs does is the one thing the table cannot express.

So the core is measured nightly, into the same `StrategyBacktest` table, gated
by the same `usable()`, with ONE extra gate a sleeve does not need:

    A SLEEVE'S TICKERS ARE ITS TICKERS. A CORE'S BASKET IS A TARGET.

A book can name `bal_6040` and hold something else entirely -- which is exactly
what Eran's book does, holding BND and V and no VTI at all. Pricing those
holdings from a measurement of a basket they are not is the same class of error
as PR 6's target-weighting, so the composition is checked first and a mismatch
abstains BY NAME, saying which ticker is wrong.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def _matches(held, weights, band=5.0):
    """Imported lazily so this file still COLLECTS against the previous commit."""
    from app.services.measured_assumptions import core_matches_basket
    return core_matches_basket(held, weights, band_pct=band)


SIXTY_FORTY = {"VTI": 0.6, "BND": 0.4}


# --------------------------------------------------------------------------- #
# the gate: is the book actually holding the basket it names?
# --------------------------------------------------------------------------- #
def test_a_book_holding_its_basket_matches():
    ok, ev = _matches({"VTI": 6000.0, "BND": 4000.0}, SIXTY_FORTY)
    assert ok is True, ev
    assert ev["missing"] == [] and ev["extra"] == []
    assert ev["worst_drift_pct"] == 0.0


def test_erans_book_does_not_match_and_the_reason_names_the_ticker():
    """His core region is BND and V. The basket is VTI and BND.

    This is the case that made the whole PR: a measurement exists, it is good,
    and it is not a measurement of these holdings.
    """
    ok, ev = _matches({"BND": 2853.0, "V": 954.0}, SIXTY_FORTY)
    assert ok is False
    assert "VTI" in ev["missing"], "the missing leg is not named"
    assert "V" in ev["extra"], "the holding that is not in the basket is not named"


def test_ordinary_drift_inside_the_band_still_matches():
    """A 62/38 book is holding a 60/40. Nagging about that is noise.

    The band is `recommendations.SLEEVE_DRIFT_BAND_PCT`, the app's existing
    answer to how far a thing may wander -- not a second private tolerance.
    """
    ok, ev = _matches({"VTI": 6200.0, "BND": 3800.0}, SIXTY_FORTY)
    assert ok is True, ev
    assert ev["worst_drift_pct"] == 2.0


def test_drift_past_the_band_does_not_match():
    ok, ev = _matches({"VTI": 7000.0, "BND": 3000.0}, SIXTY_FORTY)
    assert ok is False
    assert ev["worst_drift_pct"] == 10.0
    assert ev["missing"] == [] and ev["extra"] == []


def test_the_band_is_the_apps_existing_one_and_not_a_second_answer():
    from app.services.measured_assumptions import core_matches_basket
    from app.services.recommendations import SLEEVE_DRIFT_BAND_PCT

    assert SLEEVE_DRIFT_BAND_PCT == 5.0
    # exactly at the band is inside it
    ok, _ = core_matches_basket({"VTI": 6500.0, "BND": 3500.0}, SIXTY_FORTY,
                                band_pct=SLEEVE_DRIFT_BAND_PCT)
    assert ok is True


def test_an_empty_core_region_is_not_a_match():
    ok, ev = _matches({}, SIXTY_FORTY)
    assert ok is False
    assert ev.get("reason")


def test_a_core_with_no_basket_is_not_a_match():
    ok, ev = _matches({"VTI": 100.0}, {})
    assert ok is False
    assert ev.get("reason")


def test_the_evidence_carries_both_sides_so_a_reader_can_compare():
    """"You hold 75/25 and the basket is 60/40" beats "does not match"."""
    _, ev = _matches({"VTI": 7500.0, "BND": 2500.0}, SIXTY_FORTY)
    assert ev["held_pct"] == {"BND": 25.0, "VTI": 75.0}
    assert ev["basket_pct"] == {"BND": 40.0, "VTI": 60.0}


# --------------------------------------------------------------------------- #
# a fixed basket has no fitted parameter
# --------------------------------------------------------------------------- #
def test_a_core_reads_its_full_window_and_says_why():
    """`_pick_return` defaults to the out-of-sample fold because a RULE can be
    tuned until the past looks good. A fixed 60/40 has no parameter, so an
    out-of-sample split would measure the decade rather than the strategy.

    That is a reason, not a fallback, and it must not read as one.
    """
    from app.services.measured_assumptions import (
        RETURN_BASIS_OOS, _pick_core_return, _pick_return,
    )

    metrics = {"cagr_pct": 7.4, "volatility_pct": 9.8}
    # the rule path abstains: there is no fold in this row
    assert _pick_return(metrics, {}, RETURN_BASIS_OOS)[0] is None
    # the core path reads the full window, with no abstention
    got, why = _pick_core_return(metrics, {}, RETURN_BASIS_OOS)
    assert got == 7.4 and why is None


def test_the_static_basis_still_overrides_the_core():
    """Asking for the instrument table gets the instrument table."""
    from app.services.measured_assumptions import (
        RETURN_BASIS_STATIC, _pick_core_return,
    )

    got, why = _pick_core_return({"cagr_pct": 7.4}, {}, RETURN_BASIS_STATIC)
    assert got is None and why is None


def test_a_core_row_with_no_cagr_abstains_rather_than_inventing_one():
    from app.services.measured_assumptions import (
        NOT_MEASURED_NO_ROW, RETURN_BASIS_OOS, _pick_core_return,
    )

    got, why = _pick_core_return({"volatility_pct": 9.8}, {}, RETURN_BASIS_OOS)
    assert got is None and why == NOT_MEASURED_NO_ROW


# --------------------------------------------------------------------------- #
# the nightly job measures cores at all
# --------------------------------------------------------------------------- #
def test_the_catalog_of_cores_is_not_empty_and_holds_erans():
    """If this ever returns nothing, `measure_cores` silently measures nothing."""
    from app.services.strategies import core_candidates, core_weights

    ids = {c["id"] for c in core_candidates()}
    assert "bal_6040" in ids, "the core Eran runs is not a measurable candidate"
    assert core_weights("bal_6040"), "Classic 60/40 carries no basket"


def test_every_basket_is_measured_on_its_own_tickers_not_the_shared_feed(monkeypatch):
    """`align()` intersects the DATE AXIS across everything in the feed.

    One late-listing ticker in ONE basket, handed to every measurement through a
    shared feed, silently truncates all fourteen to its history and reports
    ten-year figures for none of them. `target_solver._core_blocking` documents
    that trap; this asserts `measure_cores` did not walk into it.
    """
    from app.services import backtest_service as B

    seen: list[set] = []

    def _fake_fetch(tickers, classify=False):
        series = {tk: [("2020-01-0%d" % (i + 1), 100.0 + i) for i in range(9)]
                  for tk in tickers}
        return (series, [], []) if classify else (series, [])

    def _fake_measure(series, components, **kw):
        seen.append(set(series))
        return {"ok": True, "cagr_pct": 5.0, "volatility_pct": 8.0,
                "observations": 900, "start": "2015-01-01", "end": "2025-01-01"}

    monkeypatch.setattr(B, "_fetch", _fake_fetch)
    from app.engines import blend as _blend
    monkeypatch.setattr(_blend, "measure_blend", _fake_measure)

    cands = [{"id": "a", "weights": {"VTI": 0.6, "BND": 0.4}},
             {"id": "b", "weights": {"QQQ": 1.0}}]
    B.measure_cores(cands)

    assert seen, "no basket was measured"
    assert {"VTI", "BND"} in seen, f"the 60/40 was not measured on its own feed: {seen}"
    assert {"QQQ"} in seen, f"the single-ticker core was not scoped: {seen}"
    for feed in seen:
        assert "SPY" not in feed, (
            "the benchmark is passed as `benchmark=`, never mixed into the feed - "
            "align() would intersect it into the date axis")


def test_a_dead_ticker_in_one_basket_does_not_kill_the_others(monkeypatch):
    """Thirteen good cores must not be lost to one missing symbol."""
    from app.services import backtest_service as B

    def _fake_fetch(tickers, classify=False):
        series = {tk: [("2020-01-0%d" % (i + 1), 100.0 + i) for i in range(9)]
                  for tk in tickers if tk != "DEAD"}
        return (series, ["DEAD"], []) if classify else (series, ["DEAD"])

    monkeypatch.setattr(B, "_fetch", _fake_fetch)
    from app.engines import blend as _blend
    monkeypatch.setattr(_blend, "measure_blend",
                        lambda *a, **k: {"ok": True, "cagr_pct": 5.0,
                                         "volatility_pct": 8.0, "observations": 900,
                                         "start": "2015-01-01", "end": "2025-01-01"})

    out = B.measure_cores([{"id": "good", "weights": {"VTI": 1.0}},
                           {"id": "bad", "weights": {"DEAD": 1.0}}])
    assert out["good"]["ok"] is True, "a healthy core was lost to another's dead ticker"
    assert out["bad"]["ok"] is False
    assert "DEAD" in out["bad"]["detail"], "the dead ticker is not named"


def test_a_rate_limited_ticker_is_not_reported_as_a_dead_one(monkeypatch):
    """"BND has no price history" is a fact about BND. "BND was rate-limited" is
    a fact about this run. A reader who cannot tell them apart goes off to check
    whether BND was delisted.
    """
    from app.services import backtest_service as B

    def _fake_fetch(tickers, classify=False):
        series = {tk: [("2020-01-0%d" % (i + 1), 100.0 + i) for i in range(9)]
                  for tk in tickers if tk != "SLOW"}
        return (series, [], ["SLOW"]) if classify else (series, ["SLOW"])

    monkeypatch.setattr(B, "_fetch", _fake_fetch)
    out = B.measure_cores([{"id": "x", "weights": {"SLOW": 1.0}}])
    assert out["x"]["ok"] is False
    assert out["x"]["reason"] == "PROVIDER_UNAVAILABLE"
    assert "not about the ticker" in out["x"]["detail"]


def test_a_measured_core_records_that_it_has_no_fold_to_test(monkeypatch):
    """An empty `robustness` reads as a failed measurement. This is not one."""
    from app.services import backtest_service as B

    def _fake_fetch(tickers, classify=False):
        series = {tk: [("2020-01-0%d" % (i + 1), 100.0 + i) for i in range(9)]
                  for tk in tickers}
        return (series, [], []) if classify else (series, [])

    monkeypatch.setattr(B, "_fetch", _fake_fetch)
    from app.engines import blend as _blend
    monkeypatch.setattr(_blend, "measure_blend",
                        lambda *a, **k: {"ok": True, "cagr_pct": 7.4,
                                         "volatility_pct": 9.8, "observations": 2400,
                                         "start": "2015-01-01", "end": "2025-01-01"})

    out = B.measure_cores([{"id": "bal_6040", "weights": SIXTY_FORTY}])["bal_6040"]
    assert out["ok"] is True
    assert out["is_core_basket"] is True
    assert out["benchmark_ticker"], "the benchmark it was measured against is not recorded"
    oos = out["robustness"]["out_of_sample"]
    assert oos["ok"] is False
    assert oos["reason"] == B.NO_FIT_TO_TEST
    assert "no fitted parameter" in oos["detail"]


def test_the_nightly_job_reports_sleeves_and_cores_separately(monkeypatch):
    """"every sleeve measured and every core failed" is a different morning
    from "everything measured", and one headline count cannot say which."""
    import inspect

    from app.services import backtest_service as B

    src = inspect.getsource(B.refresh_all)
    assert "refresh_cores" in src, "the nightly job still measures only sleeves"
    assert '"cores"' in src and '"sleeves"' in src, (
        "the two kinds of strategy are not counted apart")


# --------------------------------------------------------------------------- #
# the headline tells the three cases apart
# --------------------------------------------------------------------------- #
def _head(components, abstained, pct):
    from app.services.measured_assumptions import (
        BASIS_MIXED, RETURN_BASIS_FULL, _headline,
    )
    return _headline({"basis": BASIS_MIXED, "measured_pct_of_book": pct,
                      "components": components, "abstained": abstained},
                     RETURN_BASIS_FULL)


def test_a_measured_core_is_said_to_be_measured():
    from app.services.measured_assumptions import SOURCE_MEASURED

    text = _head([{"kind": "sleeve", "weight_pct": 60, "return_source": SOURCE_MEASURED},
                  {"kind": "core", "weight_pct": 37, "return_source": SOURCE_MEASURED}],
                 [], 97.0)
    assert "your core" in text.lower()
    assert "3%" in text, "the cash remainder is not named"
    assert "instrument table" not in text, (
        "nothing is falling back to the table, so it must not be mentioned")


def test_a_core_the_book_is_not_holding_says_which_ticker_is_wrong():
    """This is Eran's book, and the sentence has to be actionable."""
    from app.services.measured_assumptions import CORE_NOT_HELD, SOURCE_MEASURED

    text = _head(
        [{"kind": "sleeve", "weight_pct": 79, "return_source": SOURCE_MEASURED}],
        [{"kind": "core", "id": "bal_6040", "reason": CORE_NOT_HELD,
          "evidence": {"missing": ["VTI"], "extra": ["V"], "worst_drift_pct": 60.0}}],
        79.0)
    lower = text.lower()
    assert "vti" in lower, f"the missing leg is not named: {text}"
    assert "v is not in it" in lower, f"the stray holding is not named: {text}"
    assert "is measured" in lower, (
        "the core IS measured; the sentence must not imply otherwise")
    assert "21%" in text


def test_a_book_with_no_core_measurement_still_says_so_plainly():
    from app.services.measured_assumptions import SOURCE_MEASURED

    text = _head([{"kind": "sleeve", "weight_pct": 79,
                   "return_source": SOURCE_MEASURED}], [], 79.0)
    lower = text.lower()
    assert "no usable measurement yet" in lower
    assert "no strategy" not in lower, "the core is a strategy; never say otherwise"


def test_no_branch_of_the_headline_ever_denies_the_core_its_strategy():
    """The invariant Eran's correction bought. It holds in every branch."""
    from app.services.measured_assumptions import CORE_NOT_HELD, SOURCE_MEASURED

    measured = [{"kind": "core", "weight_pct": 27, "return_source": SOURCE_MEASURED}]
    cases = [
        _head(measured, [], 97.0),
        _head([], [{"kind": "core", "reason": CORE_NOT_HELD,
                    "evidence": {"missing": ["VTI"], "extra": []}}], 79.0),
        _head([], [], 79.0),
    ]
    for text in cases:
        assert "no strategy" not in text.lower(), text
