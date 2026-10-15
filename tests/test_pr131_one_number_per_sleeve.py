"""PR 131 (D95, D96). Owner, 2026-10-14: "the sleeves holds much more than they
should but I'm not getting any alert" -- and, the next day, "how come the
chances of meeting the goals are still not aligned?".

**D95 -- one holding, split between the sleeves that share it.** Volatility-
Targeted Leverage and Trend-Filtered Leveraged Nasdaq both hold TQQQ, and
`sleeve_occupancy` credited each with the WHOLE position: the Plan tab read
45.7% / 38.4% / 13.5% (97.6%) for sleeves that together held 59.2% of a 60%
target. The same rows fed the core's "sleeve overshoot" (39 points of NAV that
did not exist), the health score and the measured expected return.

**D96 -- one goal chance.** Every screen asks `frontier_service` for the goal's
chance. When the stored verdict was merely old (a day, a rule change, a resize),
Today dropped it for the projection's rough no-rules run -- 44% beside Plan's 20%
-- and said so only in small print. Now an old verdict is quoted with its age and
why, and the screens ask for a re-solve.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pathlib  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_frontier_goal import _goal_verdict, _reset_plan, _seed  # noqa: E402
from tests.test_frontier_resize import _store_verdict  # noqa: E402

NASDAQ_TREND = "btm_trend_tqqq"         # TQQQ, base QQQ
VOL_TARGET = "btm_vol_target_tqqq"      # TQQQ, base QQQ
SEMIS = "btm_trend_soxl"                # SOXL, base SMH
HEADLINE = {"basis": "rules", "p_goal_pct": 20.0, "without_rules_pct": 28.7}


def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


async def _book(c, own_session):
    """The owner's shape: one TQQQ position two sleeves want, SOXL for a third.

    The second TQQQ sleeve is written straight to the book: since PR 132 the app
    refuses to ADD a sleeve that is the same bet as one held, but a book that
    already runs two (as the owner's did) must still be measured right.
    """
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin

    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("TQQQ", 3840.0), _pos("SOXL", 1350.0), _pos("VTI", 2700.0),
        _pos("BND", 2110.0, cls="Fixed Income")]})
    c.post(f"/api/v1/strategies/{VOL_TARGET}/apply?sleeve_pct=25")
    async with own_session() as s:
        await sv.add_or_resize(s, await ensure_superadmin(s), NASDAQ_TREND, 20.0)
        await s.commit()
    c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=15")
    return c.get("/api/v1/plan/sleeves").json()


# --------------------------------------------------------------------------- #
# D95: one holding, split
# --------------------------------------------------------------------------- #
async def test_a_shared_ticker_is_split_by_target_weight_not_counted_twice(own_session):
    with TestClient(m.app) as c:
        j = await _book(c, own_session)
    rows = {s["strategy_id"]: s for s in j["sleeves"]}
    tqqq = rows[VOL_TARGET]["held_ils"] + rows[NASDAQ_TREND]["held_ils"]
    # On HEAD each sleeve held all ~3,840 of TQQQ: 7,680 between them.
    assert tqqq == pytest.approx(3840.0, rel=1e-3), rows
    assert rows[VOL_TARGET]["held_ils"] == pytest.approx(3840.0 * 25 / 45, rel=1e-3)
    assert rows[NASDAQ_TREND]["held_ils"] == pytest.approx(3840.0 * 20 / 45, rel=1e-3)
    assert rows[VOL_TARGET]["shared_with"] == {"TQQQ": [NASDAQ_TREND]}
    assert rows[SEMIS]["shared_with"] == {}


async def test_the_rows_add_up_to_the_book_total(own_session):
    """The invariant the screenshot broke: per-sleeve held == the union."""
    with TestClient(m.app) as c:
        j = await _book(c, own_session)
    total = sum(s["held_pct"] for s in j["sleeves"])
    assert total == pytest.approx(j["allocated_held_pct"], abs=2e-4), j
    assert total <= 1.0


async def test_a_ticker_only_one_sleeve_wants_is_unchanged(own_session):
    with TestClient(m.app) as c:
        j = await _book(c, own_session)
    semis = next(s for s in j["sleeves"] if s["strategy_id"] == SEMIS)
    assert semis["held_ils"] == pytest.approx(1350.0, rel=1e-3)


async def test_the_measured_return_weights_the_sleeves_by_what_they_hold(own_session):
    """`book_assumptions` weighted each sleeve by its held share: 84% of the book
    on the owner's numbers for 38% actually held in TQQQ."""
    from app.services.feed_service import ensure_superadmin
    from app.services.measured_assumptions import book_assumptions
    with TestClient(m.app) as c:
        j = await _book(c, own_session)
    async with own_session() as s:
        a = await book_assumptions(s, await ensure_superadmin(s))
    w = sum(x["weight_pct"] for x in a.get("components") or [] if x.get("kind") == "sleeve")
    assert w == pytest.approx(j["allocated_held_pct"] * 100.0, abs=0.05), a.get("components")


# --------------------------------------------------------------------------- #
# D96: one goal chance
# --------------------------------------------------------------------------- #
async def test_an_old_verdict_is_quoted_with_its_reason_not_swapped(own_session):
    """A sleeve resized since the solve: Today, the chart and Plan quote the
    verdict's 20%, labelled, instead of the rough no-rules run."""
    try:
        with TestClient(m.app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur + 5, headline=HEADLINE))
            proj = c.get("/api/v1/plan/projection").json()
            gh = proj["goal_headline"]
            assert gh and gh["p_goal_pct"] == 20.0, proj.get("goal_headline")
            assert gh["current"] is False and gh["stale_reason"] == "SLEEVES_CHANGED"
            assert "sleeve sizes changed" in gh["stale_text"]

            h = c.get("/api/v1/health-check").json()
            # On HEAD: the projection's no-rules figure, "rough estimate".
            assert h["on_track"] == 20 and h["on_track_basis"] == "goal_model_stale", h
            assert "As of" in h["on_track_detail"] and "re-solved" in h["on_track_detail"]
            assert "rough estimate" not in h["on_track_detail"]

            p = c.get("/api/v1/plan").json()
            assert p["goal_chance"]["p_goal_pct"] == 20.0
            assert p["goal_chance"]["current"] is False
    finally:
        await _reset_plan(own_session)


async def test_a_current_verdict_says_current(own_session):
    try:
        with TestClient(m.app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur, headline=HEADLINE))
            gh = c.get("/api/v1/plan/projection").json()["goal_headline"]
            assert gh["current"] is True and gh["stale_reason"] is None
            assert c.get("/api/v1/health-check").json()["on_track_basis"] == "goal_model"
    finally:
        await _reset_plan(own_session)


async def test_a_verdict_about_a_different_goal_is_not_quoted(own_session):
    """Solved for 25%/yr; the plan now says 30%. That chance answers another
    question, so it is withheld and the labelled rough figure stands in."""
    try:
        with TestClient(m.app) as c:
            cur = _seed(c, 10)
            v = _goal_verdict(cur, headline=HEADLINE)
            v["goal"] = {**v["goal"], "roi_pct": 25.0}
            await _store_verdict(own_session, v)
            assert c.get("/api/v1/plan/projection").json()["goal_headline"] is None
            assert c.get("/api/v1/plan").json()["goal_chance"] is None
    finally:
        await _reset_plan(own_session)


async def test_the_automatic_re_solve_skips_a_current_or_just_solved_goal(own_session):
    """The screens call it on every load of an old chance; it must be cheap to
    say no. A just-stored verdict that is already stale is 'RECENT_SOLVE'."""
    try:
        with TestClient(m.app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur, headline=HEADLINE))
            r = c.post("/api/v1/plan/frontier/refresh?only_if_stale=true").json()
            assert r == {"skipped": "CURRENT"}, r
            await _store_verdict(own_session, _goal_verdict(cur + 5, headline=HEADLINE))
            r = c.post("/api/v1/plan/frontier/refresh?only_if_stale=true").json()
            assert r.get("skipped") == "RECENT_SOLVE", r
    finally:
        await _reset_plan(own_session)


def test_every_reason_has_words_and_the_withheld_ones_are_known():
    from app.services import frontier_service as fs
    assert fs.STALE_WITHHELD <= set(fs.STALE_TEXT)
    for code in ("TOO_OLD", "RULES_CHANGED", "SLEEVES_CHANGED", "SETTINGS_CHANGED"):
        assert code in fs.STALE_TEXT and code not in fs.STALE_WITHHELD


# --------------------------------------------------------------------------- #
# the screen
# --------------------------------------------------------------------------- #
def _html():
    return (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "static_app" / "index.html").read_text(encoding="utf-8")


def test_the_mix_chip_counts_over_as_well_as_under():
    html = _html()
    i = html.index("function mixPanel(")
    body = html[i:i + 4000]
    assert "pts over" in body and "pts to fund" in body


def test_the_screens_label_an_old_chance_and_ask_for_one_re_solve():
    html = _html()
    assert "only_if_stale=true" in html
    for fn in ("function goalSummary(", "Chance of hitting your goal", "const gh=r.goal_headline;"):
        i = html.index(fn)
        window = html[max(0, i - 800):i + 2500]
        assert "_staleGoalNote(" in window or "_autoResolveGoal(" in window, fn
