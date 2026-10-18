"""PR 135a (wave 4): every sleeve move scored by the goal model -- the inputs of the
add / remove / replace / move-to-a-hedge cards (PR 135b).

The sleeve plan, wave 4: each card shows what its move does to the goal's chance
with your rules AND to the chance the book halves (owner decision 4: shown both
ways). Scored nightly (`sleeve_moves`, 04:40) and on demand; Today only reads.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import sleeve_moves as sm  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402
from tests.test_frontier_goal import _reset_plan, _seed  # noqa: E402

VOL = "btm_vol_target_tqqq"
SEMIS = "btm_trend_soxl"
TREND = "btm_trend_tqqq"


# --------------------------------------------------------------------------- #
# which moves are scored (pure)
# --------------------------------------------------------------------------- #
def test_hedge_moves_come_out_of_the_leveraged_sleeve_at_1x_and_2x():
    moves = sm.candidate_moves({VOL: 40.0, SEMIS: 26.5}, {})
    hedge = [x for x in moves if x["kind"] == "hedge"]
    assert hedge, "no hedge moves"
    dbmf = [x for x in hedge if x["to"] == "div_dbmf" and x["from"] == VOL]
    assert {x["scale"] for x in dbmf} == {1, 2}
    one = next(x for x in dbmf if x["scale"] == 1)
    assert one["pct"] == 15 and one["book"][VOL] == 25.0 and one["book"]["div_dbmf"] == 15
    assert one["book"][SEMIS] == 26.5, "the other sleeve is untouched"
    for x in hedge:
        assert cat.get(x["to"])["badges"] and not cat.get(x["to"]).get("explore_only")


def test_a_hedge_that_is_the_same_bet_as_a_held_sleeve_is_not_offered():
    moves = sm.candidate_moves({VOL: 40.0}, {})
    assert not [x for x in moves if x["to"] == "div_qqq_trend_short"], "a Nasdaq bet beside a Nasdaq sleeve"


def test_every_held_sleeve_gets_a_remove_move():
    moves = sm.candidate_moves({VOL: 40.0, SEMIS: 26.5}, {})
    rem = {x["from"]: x["book"] for x in moves if x["kind"] == "remove"}
    assert rem == {VOL: {SEMIS: 26.5}, SEMIS: {VOL: 40.0}}


def test_a_same_bet_pair_gets_replace_moves_each_way_and_never_into_a_look_alike():
    moves = sm.candidate_moves({VOL: 25.0, TREND: 20.0}, {})
    rep = [x for x in moves if x["kind"] == "replace"]
    assert {x["from"] for x in rep} == {VOL, TREND}
    for x in rep:
        assert x["to"] not in (VOL, TREND, "div_qqq_trend_short", "btm_qqq_hold")
        assert x["book"][x["to"]] == (25.0 if x["from"] == VOL else 20.0)
        assert not cat.get(x["to"]).get("explore_only")


def test_no_replace_moves_when_nothing_is_the_same_bet():
    assert not [x for x in sm.candidate_moves({VOL: 40.0, SEMIS: 26.5}, {}) if x["kind"] == "replace"]


# --------------------------------------------------------------------------- #
# scored end to end by the goal model
# --------------------------------------------------------------------------- #
def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


async def test_the_moves_are_scored_stored_and_served(own_session):
    try:
        with TestClient(m.app) as c:
            _seed(c, 30)                       # the goal tests' book: a goal and a QQQ sleeve
            # "30% on every shekel put in" needs a shekel put in.
            c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
            assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
            assert c.get("/api/v1/plan/sleeve-moves").json()["reason"] == "NOT_SCORED"

            r = c.post("/api/v1/plan/sleeve-moves/refresh").json()
            assert r.get("ok") and r.get("stored"), r
            assert r["before"]["p_goal_pct"] is not None
            scored = [x for x in r["moves"] if x["ok"]]
            assert scored, r["moves"][:3]
            for x in scored:
                assert x["after"]["p_goal_pct"] is not None and x["goal_pts"] is not None
                assert x["halve_pts"] is None or isinstance(x["halve_pts"], float)
            assert {x["kind"] for x in r["moves"]} >= {"hedge", "remove"}

            got = c.get("/api/v1/plan/sleeve-moves").json()
            assert got["as_of"] == r["as_of"] and len(got["moves"]) == len(r["moves"])
    finally:
        await _reset_plan(own_session)


async def test_a_book_without_sleeves_is_not_scored(own_session):
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        out = await sm.refresh(s, await ensure_superadmin(s))
    assert out["ok"] is False and out["stored"] is False and out["reason"] == "NO_SLEEVES"
