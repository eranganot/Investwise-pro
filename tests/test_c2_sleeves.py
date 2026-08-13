"""C2 -- a book runs N sleeves, and the safety rules that makes necessary.

C1 added the table and left it inert. C2 makes ``apply`` additive: tapping
"Apply strategy" on a second card runs both sleeves instead of silently dropping
the first. Three consequences, each of which is a test below rather than a hope:

* **Over-allocating refuses**, and writes nothing. Not "clamps to what fits" --
  a sleeve installed at a size nobody chose is what ``_fund_sleeve`` already
  abstains over.
* **One cap per ticker, at the SUM across sleeves.** Two sleeves both wanting
  TQQQ arming their own caps is the P1 duplicate bug at N scale.
* **No sleeve is a funding source for another.** Widened exclusion set, pulled
  forward from C3 because it stops being deferrable the moment apply is
  additive.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.services import sleeve_service as sv

TREND = "btm_trend_tqqq"       # TQQQ, suggested 20%
SOXL = "btm_trend_soxl"        # SOXL, suggested 10%
FACTOR = "btm_factor_stack"    # MTUM/QUAL/AVUV, suggested 40%
VOL = "btm_vol_target_tqqq"    # TQQQ again, suggested 25% -- shares a ticker


def _pos(ticker, price=100.0, qty=10.0, basis=90.0):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c):
    # Every sleeve ticker is HELD, deliberately. `rules_service.list_rules`
    # retires a rule whose ticker is not in the book, at read time (P4) -- so a
    # cap armed for a sleeve you do not yet hold is armed and then immediately
    # retired on the next Rules read. That is pre-existing behaviour, not
    # something C2 changed, but it means a cap test that seeds only one holding
    # measures the retirement sweep instead of the arming logic.
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V", 365, 100, 300), _pos("TQQQ"), _pos("SOXL"),
        _pos("MTUM"), _pos("QUAL"), _pos("AVUV")]})


def _apply(c, sid, pct):
    return c.post(f"/api/v1/strategies/{sid}/apply?sleeve_pct={pct}").json()


def _sleeves(c):
    return c.get("/api/v1/plan/sleeves").json()


def _rules(c):
    return c.get("/api/v1/rules").json()["rules"]


def _caps(c, active_only=True):
    return {r["ticker"].upper(): r for r in _rules(c)
            if r["rule_type"] == "max_weight" and (r["active"] or not active_only)}


# --------------------------------------------------------------------------- #
# Additive apply
# --------------------------------------------------------------------------- #
def test_a_second_apply_adds_a_sleeve_instead_of_replacing_the_first():
    """The whole point of C2. This used to overwrite: choosing a second strategy
    silently dropped the one you were already running."""
    with TestClient(m.app) as c:
        _seed(c)
        assert _apply(c, SOXL, 10)["ok"] is True
        assert _apply(c, FACTOR, 15)["ok"] is True

        body = _sleeves(c)
        assert sorted((r["strategy_id"], r["sleeve_pct"]) for r in body["sleeves"]) == [
            (FACTOR, 15.0), (SOXL, 10.0)]
        assert body["allocated_pct"] == 25.0
        assert body["core_pct"] == 75.0


def test_applying_the_same_strategy_again_resizes_it():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        r = _apply(c, SOXL, 20)
        assert r["sleeve"]["action"] == "resized"
        assert r["sleeve"]["previous_pct"] == 10.0
        body = _sleeves(c)
        assert len(body["sleeves"]) == 1, "resizing must not add a second copy"
        assert body["allocated_pct"] == 20.0


def test_over_allocating_refuses_and_writes_nothing():
    """Refuse, do not clamp. Clamping would install a sleeve at a size nobody
    chose and report success, which is the failure mode `_fund_sleeve` already
    abstains over."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 70)
        before = _sleeves(c)

        r = _apply(c, FACTOR, 40)
        assert r["ok"] is False
        assert "30" in r["reason"], r["reason"]

        after = _sleeves(c)
        assert after["sleeves"] == before["sleeves"], "a refusal wrote something"
        assert after["allocated_pct"] == 70.0


def test_a_refusal_leaves_the_plan_and_its_caps_untouched():
    """A half-applied strategy is worse than a refused one. The plan row and the
    armed caps must both be exactly where they were."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 70)
        plan_before = c.get("/api/v1/plan").json()
        caps_before = _caps(c)

        _apply(c, FACTOR, 40)

        plan_after = c.get("/api/v1/plan").json()
        assert plan_after["strategy"] == plan_before["strategy"]
        assert plan_after["strategy_sleeve_pct"] == plan_before["strategy_sleeve_pct"]
        assert _caps(c) == caps_before


def test_a_sleeve_may_take_the_whole_book_but_not_a_point_more():
    with TestClient(m.app) as c:
        _seed(c)
        assert _apply(c, SOXL, 100)["ok"] is True
        assert _sleeves(c)["core_pct"] == 0.0
        assert _apply(c, FACTOR, 1)["ok"] is False


# --------------------------------------------------------------------------- #
# One cap per ticker, at the sum
# --------------------------------------------------------------------------- #
def test_two_sleeves_wanting_the_same_ticker_arm_ONE_cap_at_the_sum():
    """The P1 duplicate bug, pre-empted at N scale.

    btm_trend_tqqq and btm_vol_target_tqqq both hold TQQQ. Each arming its own
    max_weight would leave two rules on one ticker at two levels, and whichever
    fired first would win.
    """
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, TREND, 20)
        _apply(c, VOL, 15)

        tqqq = [r for r in _rules(c)
                if r["ticker"].upper() == "TQQQ" and r["rule_type"] == "max_weight"]
        assert len(tqqq) == 1, f"{len(tqqq)} caps on TQQQ -- the P1 duplicate bug is back"
        assert tqqq[0]["level"] == pytest.approx(35.0)
        # And it says where the number came from. A 35% cap that is 20 from one
        # sleeve and 15 from another cannot be reconstructed from the screen.
        assert "+" in tqqq[0]["note"]


def test_resizing_one_sleeve_relevels_the_shared_cap():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, TREND, 20)
        _apply(c, VOL, 15)
        _apply(c, VOL, 25)
        assert _caps(c)["TQQQ"]["level"] == pytest.approx(45.0)


def test_a_multi_ticker_sleeve_arms_a_cap_per_leg():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, FACTOR, 40)
        caps = _caps(c)
        assert set(caps) == {"MTUM", "QUAL", "AVUV"}
        # 40% of the book, split by the basket's own weights (.40/.35/.25).
        assert sum(v["level"] for v in caps.values()) == pytest.approx(40.0, abs=0.2)


# --------------------------------------------------------------------------- #
# Removal
# --------------------------------------------------------------------------- #
def test_removing_a_sleeve_retires_the_cap_it_armed():
    """A cap that outlives its sleeve is a ceiling on a position now held for
    some other reason -- the stale-AMZN-stop shape P4 had to fix once already."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        assert "SOXL" in _caps(c)

        r = c.delete(f"/api/v1/plan/sleeves/{SOXL}").json()
        assert r["ok"] is True
        assert r["retired_caps"] == ["SOXL"]
        assert "SOXL" not in _caps(c)
        # Retired, not deleted: history is kept, exactly as P4 retires rules.
        assert "SOXL" in _caps(c, active_only=False)
        assert _sleeves(c)["sleeves"] == []


def test_removing_one_of_two_sleeves_relevels_a_shared_cap_rather_than_retiring_it():
    """TQQQ is still wanted by the sleeve that remains, so its cap survives --
    at the size that sleeve alone asks for."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, TREND, 20)
        _apply(c, VOL, 15)
        assert _caps(c)["TQQQ"]["level"] == pytest.approx(35.0)

        r = c.delete(f"/api/v1/plan/sleeves/{VOL}").json()
        assert r["retired_caps"] == [], "TQQQ is still wanted -- it must not be retired"
        assert _caps(c)["TQQQ"]["level"] == pytest.approx(20.0)


def test_a_hand_set_cap_on_a_non_sleeve_ticker_is_never_touched():
    """The regression that reached production, pinned.

    The first C2 build retired every active max_weight whose ticker no sleeve
    wanted. Applying one sleeve therefore silently disarmed live hand-set caps --
    on a real book, V, SCHD and MSFT, including the 20% MSFT cap the 2026-08-11
    notification fix is built around. Ownership is now MARKED, not assumed.
    """
    with TestClient(m.app) as c:
        _seed(c)
        # V is held and capped by hand. No sleeve ever wants it.
        c.post("/api/v1/rules", json={"ticker": "V", "rule_type": "max_weight",
                                      "mode": "pct", "level": 30, "note": "set by hand"})
        assert _caps(c)["V"]["level"] == pytest.approx(30.0)

        _apply(c, SOXL, 10)
        assert "V" in _caps(c), "applying a sleeve disarmed a hand-set cap"
        assert _caps(c)["V"]["level"] == pytest.approx(30.0), "and it must keep its level"

        _apply(c, FACTOR, 15)
        c.delete(f"/api/v1/plan/sleeves/{FACTOR}")
        c.delete(f"/api/v1/plan/sleeves/{SOXL}")
        assert "V" in _caps(c), "removing every sleeve disarmed a hand-set cap"
        assert _caps(c)["V"]["level"] == pytest.approx(30.0)


def test_a_hand_set_cap_on_a_sleeve_ticker_is_adopted_and_says_so():
    """The other half. The sleeve system already overwrote the LEVEL of a
    hand-set cap on a ticker it wants -- two max_weight rules on one ticker at
    two levels is the ambiguity it exists to remove. So it takes ownership, and
    reports `adopted` rather than doing it quietly."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": "max_weight",
                                      "mode": "pct", "level": 55, "note": "set by hand"})
        r = _apply(c, SOXL, 10)
        soxl = next(x for x in r["sleeve_caps"] if x["ticker"] == "SOXL")
        assert soxl["action"] == "adopted"
        assert soxl["previous_level"] == pytest.approx(55.0)
        assert _caps(c)["SOXL"]["level"] == pytest.approx(10.0)

        # Adopted means owned, so removing the sleeve does retire it.
        d = c.delete(f"/api/v1/plan/sleeves/{SOXL}").json()
        assert d["retired_caps"] == ["SOXL"]


def test_removing_the_named_sleeve_moves_the_legacy_pointer():
    """/plan must not go on reporting a strategy the book no longer runs."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)
        assert c.get("/api/v1/plan").json()["strategy"] == FACTOR

        c.delete(f"/api/v1/plan/sleeves/{FACTOR}")
        assert c.get("/api/v1/plan").json()["strategy"] == SOXL

        c.delete(f"/api/v1/plan/sleeves/{SOXL}")
        assert c.get("/api/v1/plan").json()["strategy"] is None


def test_removing_a_sleeve_you_do_not_run_is_refused_not_silently_fine():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.delete(f"/api/v1/plan/sleeves/{SOXL}").json()
        assert r["ok"] is False and "not a sleeve" in r["error"]


def test_removal_sells_nothing():
    """Dropping a sleeve stops the app steering toward it. The shares stay."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        before = {p["ticker"]: p["quantity"]
                  for p in c.get("/api/v1/portfolio").json()["positions"]}
        c.delete(f"/api/v1/plan/sleeves/{SOXL}")
        after = {p["ticker"]: p["quantity"]
                 for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert after == before


# --------------------------------------------------------------------------- #
# No sleeve funds another  (the C3 piece pulled forward)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_exclusion_set_covers_every_sleeve_not_just_the_named_one():
    """Funding Factor Stack must never sell the SOXL sleeve.

    The old exclusion set was built from ``plans.strategy`` -- one strategy. The
    moment a book can run two, the other one is harvestable and fundable, which
    is the same two-agents-disagreeing bug the SOXL tax-harvest fix already
    addressed once.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401
    from app.core.config import get_settings
    from app.models.base import Base
    from app.services.feed_service import ensure_superadmin

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            user = await ensure_superadmin(s)
            await sv.add_or_resize(s, user, SOXL, 10)
            await sv.add_or_resize(s, user, FACTOR, 15)
            await s.commit()

            tickers = await sv.sleeve_tickers(s, user)
            assert "SOXL" in tickers, "the other sleeve is not protected"
            assert {"MTUM", "QUAL", "AVUV"} <= tickers

            targets = await sv.all_sleeve_targets(s, user)
            assert targets["SOXL"] == pytest.approx(0.10)
            assert sum(targets.values()) == pytest.approx(0.25, abs=1e-6)
    finally:
        await eng.dispose()


def test_the_tax_harvester_leaves_every_sleeve_alone():
    """Losers inside ANY sleeve must not be offered up for a tax saving."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            # Both at a loss, so both look harvestable to the tax engine.
            {"ticker": "SOXL", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 20, "listing_price": 20, "quantity": 100, "cost_basis": 60},
            {"ticker": "MTUM", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 150, "listing_price": 150, "quantity": 100, "cost_basis": 200},
        ]})
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)

        recs = c.get("/api/v1/recommendations").json().get("recommendations", [])
        harvest = [r for r in recs if "harvest" in (r.get("title", "") + r.get("id", "")).lower()]
        for card in harvest:
            named = (card.get("raw_data") or {}).get("losing_tickers") or []
            assert "SOXL" not in named, "the harvester offered the SOXL sleeve"
            assert "MTUM" not in named, "the harvester offered the Factor Stack sleeve"


# --------------------------------------------------------------------------- #
# Guardrails: sleeves stop writing them, static families still do
# --------------------------------------------------------------------------- #
def test_a_sleeve_no_longer_rewrites_the_books_objective_and_risk():
    """Objective and risk set the concentration cap and cash floor for the WHOLE
    book. Under N sleeves, "whichever you applied last decides your guardrails"
    is not a rule anyone would choose."""
    with TestClient(m.app) as c:
        _seed(c)
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Low"})
        before = c.get("/api/v1/plan").json()

        _apply(c, SOXL, 10)

        after = c.get("/api/v1/plan").json()
        assert after["objective"] == "Balanced"
        assert after["risk_tolerance"] == "Low"
        assert after["caps"] == before["caps"], "a sleeve moved the book's guardrails"


def test_a_static_family_still_sets_them_because_it_IS_the_portfolio():
    """The distinction, not an oversight. The four static families are model
    PORTFOLIOS -- "Grow AI & Semis" is a whole-book allocation and its objective
    is part of what you chose. Only rule-based sleeves were overreaching."""
    with TestClient(m.app) as c:
        _seed(c)
        c.put("/api/v1/plan", json={"objective": "Preserve", "risk_tolerance": "Low"})
        r = c.post("/api/v1/strategies/grow_ai_semis/apply").json()
        assert r["ok"] is True
        plan = c.get("/api/v1/plan").json()
        assert plan["objective"] == "Grow"
        # ...and it is not a sleeve, so it creates no row.
        assert _sleeves(c)["sleeves"] == []
