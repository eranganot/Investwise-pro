"""C3a -- funding N sleeves against ONE budget, preview only.

The hazard this phase exists to remove: funding two sleeves by calling the
single-sleeve path twice builds two funding plans from the same cash and the
same trim candidates. Both plan to spend the cash above your floor, both plan to
trim the same shares -- money counted once by the book and twice by the app.

Largest sleeve first when it will not stretch, each sleeve funded to the size
you chose or skipped entirely. That ordering is a PREFERENCE, not a result: it
honours the conviction the sizes encode and sells less, because what a sleeve
does not claim stays in the objective-managed core rather than sitting idle.

Nothing here may execute. C3a sizes and previews; C3b flips the write on.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.services import strategy_service as ss

SOXL = "btm_trend_soxl"        # SOXL
FACTOR = "btm_factor_stack"    # MTUM / QUAL / AVUV
TREND = "btm_trend_tqqq"       # TQQQ


def _pos(ticker, price=100.0, qty=100.0, basis=90.0):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c, cash=0.0):
    """A core of three ordinary holdings worth ~30k, plus optional cash."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V"), _pos("SCHD"), _pos("MSFT")]})
    if cash:
        c.post("/api/v1/portfolio/cash", json={"amount_ils": cash})


def _apply(c, sid, pct):
    return c.post(f"/api/v1/strategies/{sid}/apply?sleeve_pct={pct}").json()


def _fund(c, dry_run=True):
    return c.post(f"/api/v1/plan/sleeves/fund?dry_run={str(dry_run).lower()}").json()


def _by_id(res):
    return {x["strategy_id"]: x for x in res["sleeves"]}


# --------------------------------------------------------------------------- #
# One budget, not N
# --------------------------------------------------------------------------- #
def test_two_sleeves_share_one_funding_plan():
    """The whole point. Two calls to the single-sleeve path would each plan to
    spend the same cash; this plans it once, for the combined amount."""
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)

        res = _fund(c)
        assert res["ok"] is True
        rows = _by_id(res)
        assert rows[SOXL]["status"] == "funded"
        assert rows[FACTOR]["status"] == "funded"

        # ONE funding plan, covering the sum of both sleeves.
        total = round(rows[SOXL]["amount_ils"] + rows[FACTOR]["amount_ils"], 2)
        assert res["amount_ils"] == pytest.approx(total, abs=0.05)
        assert res["funding"]["amount_ils"] == pytest.approx(total, abs=0.05)


def test_the_cash_above_the_floor_is_only_spent_once():
    """The double-spend, pinned directly: the single plan may never draw more
    cash than the book actually has above its floor."""
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)
        res = _fund(c)
        f = res["funding"]
        spendable = 100000 - f["cash_floor_ils"]
        assert f["from_cash_ils"] <= spendable + 0.01, (
            f"drew {f['from_cash_ils']} of cash against {spendable} spendable")


def test_no_sleeve_is_a_funding_source_for_another():
    """SOXL and the factor legs are all held. Neither may appear as a sell."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V"), _pos("SCHD"), _pos("MSFT"),
            _pos("SOXL"), _pos("MTUM"), _pos("QUAL"), _pos("AVUV")]})
        _apply(c, SOXL, 20)
        _apply(c, FACTOR, 40)
        res = _fund(c)
        sells = {x["ticker"].upper() for x in (res.get("funding") or {}).get("sells", [])}
        assert not (sells & {"SOXL", "MTUM", "QUAL", "AVUV"}), (
            f"funding proposed selling a sleeve: {sells}")


# --------------------------------------------------------------------------- #
# Largest first, all-or-nothing per sleeve
# --------------------------------------------------------------------------- #
def test_when_the_money_is_short_the_largest_sleeve_is_tried_first():
    with TestClient(m.app) as c:
        _seed(c, cash=3000)          # ~30k of holdings, very little spendable
        _apply(c, SOXL, 5)
        _apply(c, FACTOR, 60)
        res = _fund(c)
        order = [x["strategy_id"] for x in res["sleeves"]]
        assert order[0] == FACTOR, f"largest sleeve must be considered first, got {order}"


def test_a_sleeve_that_does_not_fit_is_skipped_whole_never_part_funded():
    """A partially funded sleeve is a position at a size nobody chose -- the
    thing the single-sleeve path already abstains over."""
    with TestClient(m.app) as c:
        _seed(c, cash=0)
        _apply(c, FACTOR, 90)        # far beyond what this book can raise
        res = _fund(c)
        row = _by_id(res)[FACTOR]
        assert row["status"] == "skipped"
        assert row["shortfall_ils"] > 0
        assert "short" in row["reason"]
        assert res["amount_ils"] == 0.0
        assert (res.get("funding") is None
                or res["funding"]["amount_ils"] == 0), "a skipped sleeve must fund nothing"


def test_a_skipped_sleeve_does_not_block_the_ones_that_fit():
    with TestClient(m.app) as c:
        _seed(c, cash=4000)
        _apply(c, FACTOR, 95)        # cannot fit
        _apply(c, SOXL, 5)           # comfortably can
        res = _fund(c)
        rows = _by_id(res)
        assert rows[FACTOR]["status"] == "skipped"
        assert rows[SOXL]["status"] == "funded"


def test_a_sleeve_already_at_its_target_is_nothing_to_do_not_a_purchase():
    with TestClient(m.app) as c:
        # SOXL is ~25% of a 40k book; the sleeve asks for 10%.
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V"), _pos("SCHD"), _pos("MSFT"), _pos("SOXL")]})
        _apply(c, SOXL, 10)
        res = _fund(c)
        assert _by_id(res)[SOXL]["status"] == "nothing_to_do"
        assert res["amount_ils"] == 0.0


# --------------------------------------------------------------------------- #
# A partial result must never read as success
# --------------------------------------------------------------------------- #
def test_a_partial_fund_states_the_gap_between_intended_and_resulting():
    with TestClient(m.app) as c:
        _seed(c, cash=4000)
        _apply(c, FACTOR, 95)
        _apply(c, SOXL, 5)
        res = _fund(c)
        assert res["fully_funded"] is False
        assert res["intended_sleeve_pct"] == pytest.approx(100.0)
        assert res["resulting_sleeve_pct"] < res["intended_sleeve_pct"]
        assert "core" in res["message"], res["message"]
        assert "Factor" in res["message"] or FACTOR in res["message"]


def test_the_residual_stays_inside_the_tolerance_it_claims():
    """A probe against a realistic book is what shaped this rule.

    First attempt: demand the combined plan be payable to the shekel. Probing
    showed the residual is STRUCTURAL -- `plan_funding` sells whole shares and
    nets capital-gains tax out of the proceeds, so it lands ~339 shekels short of
    a 5,765 sleeve from one share of rounding plus tax. A shekel-exact rule
    rejects nearly everything, which is exactly why the single-sleeve path judges
    in points of NAV. So this asserts the documented bound, not perfection.
    """
    with TestClient(m.app) as c:
        _seed(c, cash=60000)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 20)
        res = _fund(c)
        if any(x["status"] == "funded" for x in res["sleeves"]):
            assert res["funding"] is not None
            # One point of NAV -- the bound the single-sleeve path has used since
            # P0.1 -- read off the plan that would actually run rather than the
            # trial that happened to accept the last sleeve.
            assert res["plan_shortfall_ils"] / res["nav"] * 100 < 1.0
            assert res["funding"]["shortfall_ils"] == pytest.approx(
                res["plan_shortfall_ils"], abs=0.01)


def test_the_residual_is_spread_across_every_leg_not_dumped_on_the_last():
    """The hazard the residual actually poses.

    `_execute_funded_sleeve` walks the legs spending min(want, budget) and skips
    any whose share falls below MIN_TRADE_ILS. Unscaled, the LAST leg wears the
    entire shortfall and can be dropped outright -- one sleeve quietly ends up
    smaller, or missing a position, after being reported funded. Scaling keeps
    every sleeve's composition and leaves them all a hair under instead.
    """
    with TestClient(m.app) as c:
        _seed(c, cash=60000)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 20)
        wanted = [x for x in _fund(c)["sleeves"] if x["status"] == "funded"]
        assert wanted, "precondition: something must be fundable"
        expected_legs = sum(len(x["buys"]) for x in wanted)

        original = ss.PLAN_FUNDING_EXECUTION
        try:
            ss.PLAN_FUNDING_EXECUTION = True
            res = _fund(c, dry_run=False)
        finally:
            ss.PLAN_FUNDING_EXECUTION = original

        assert res["leg_scale"] <= 1.0
        assert not res["skipped"], f"a leg was dropped: {res['skipped']}"
        assert len(res["bought"]) == expected_legs, (
            f"bought {len(res['bought'])} of {expected_legs} legs")


def test_the_residual_check_uses_the_plan_that_would_actually_run():
    """Belt and braces: whatever the per-sleeve trials said, the final plan is
    re-read. If anything ever accepts a sleeve the book cannot pay for, this is
    the assertion that catches it rather than the user's broker statement."""
    with TestClient(m.app) as c:
        _seed(c, cash=60000)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 20)
        res = _fund(c)
        if any(x["status"] == "funded" for x in res["sleeves"]):
            assert res["funding"] is not None
            assert res["funding"]["shortfall_ils"] < 250
            assert res["fully_funded"] == (
                all(x["status"] in ("funded", "nothing_to_do") for x in res["sleeves"]))


def test_a_complete_fund_says_so():
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        res = _fund(c)
        assert res["fully_funded"] is True
        assert "message" not in res


# --------------------------------------------------------------------------- #
# C3a cannot sell anything
# --------------------------------------------------------------------------- #
def test_execution_is_refused_and_the_refusal_still_shows_the_plan():
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        before = {p["ticker"]: p["quantity"]
                  for p in c.get("/api/v1/portfolio").json()["positions"]}
        cash_before = c.get("/api/v1/portfolio/cash").json()

        res = _fund(c, dry_run=False)
        assert res["ok"] is False
        assert "preview-only" in res["error"]
        # The refusal is not a blank wall: the sizing it refused to act on is
        # exactly what the user needs in order to check it.
        assert res["sleeves"] and res["funding"] is not None

        after = {p["ticker"]: p["quantity"]
                 for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert after == before, "the gated path moved holdings"
        assert c.get("/api/v1/portfolio/cash").json() == cash_before


def test_the_gate_is_the_only_thing_stopping_it():
    """Proves the refusal is a deliberate gate rather than a path that never
    worked -- otherwise C3b would flip a switch onto untested code."""
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        original = ss.PLAN_FUNDING_EXECUTION
        try:
            ss.PLAN_FUNDING_EXECUTION = True
            res = _fund(c, dry_run=False)
            assert res["ok"] is True and res["dry_run"] is False
            assert res["bought"], "with the gate open it must actually buy"
            bought = {b["ticker"] for b in res["bought"]}
            assert "SOXL" in bought
        finally:
            ss.PLAN_FUNDING_EXECUTION = original


def test_single_sleeve_funding_still_executes_untouched():
    """C3a changes sizing, not the path that has been live since P0.1."""
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 10)
        r = c.post(f"/api/v1/strategies/{SOXL}/load-basket",
                   json={"mode": "fund", "dry_run": False}).json()
        assert r["ok"] is True and r["dry_run"] is False
        assert {b["ticker"] for b in r["bought"]} == {"SOXL"}


# --------------------------------------------------------------------------- #
# Sizing comes from the sleeve row
# --------------------------------------------------------------------------- #
def test_funding_sizes_from_the_sleeve_row_not_the_legacy_column():
    """The bug this fixes: the old fallback read plans.strategy_sleeve_pct, and
    only when plans.strategy named this strategy. On a book running two sleeves,
    funding the one applied FIRST fell through to the catalog default instead of
    the size you chose."""
    with TestClient(m.app) as c:
        _seed(c, cash=100000)
        _apply(c, SOXL, 7)           # applied first
        _apply(c, FACTOR, 12)        # ...so the legacy column now names FACTOR

        plan = c.get("/api/v1/plan").json()
        assert plan["strategy"] == FACTOR, "precondition: the pointer moved"

        r = c.post(f"/api/v1/strategies/{SOXL}/load-basket",
                   json={"mode": "fund", "dry_run": True}).json()
        # 7% of the book, from the row -- not the catalog's suggested 10%.
        assert r["chosen_sleeve_pct"] == pytest.approx(7.0, abs=0.3), (
            f"sized at {r['chosen_sleeve_pct']}%, expected the row's 7%")


def test_a_book_with_no_sleeves_is_told_so_rather_than_funding_nothing_quietly():
    with TestClient(m.app) as c:
        _seed(c, cash=1000)
        res = _fund(c)
        assert res["ok"] is False and "no sleeves" in res["error"]
