"""PR 141 -- a stop on one 2x/3x fund holds every 2x/3x fund (G3/G6 of the 2026-10-22 review; D116).

Observed on production, 2026-10-23, after the owner accepted the SOXL stop-loss
(SOXL sold at 82.11, a stop hold recorded on `btm_trend_soxl`, PR 139):

* the Fund-my-sleeves dry run showed SOXL `held_back` -- and funded the
  `btm_vol_target_tqqq` sleeve with the same money;
* the held-cash card ("cash is waiting for SOXL") listed TQQQ first among the
  places to invest the reserve.

Over the month before, SOXL and TQQQ daily returns correlated 0.95 and they fell
together on 8 of the 8 sessions SOXL dropped more than 3%. The strategies' own
signals measure 0.78 (under the 0.80 same-bet limit), which is why nothing
treated them as one bet. The rule now (owner, 2026-10-23): after a stop sells a
2x/3x fund, the app buys NO 2x/3x fund -- every buyer, the Plan buttons
included -- until that hold ends; the held-cash card offers non-blocked places only.

Reproduced on HEAD `9d5a9f7` before the fix: `test_the_plan_cannot_fund_another_
2x_3x_sleeve` bought TQQQ, and `test_the_held_cash_card_offers_no_blocked_fund`
offered it.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import SOXL, _accept_the_stop, _hold, _pos  # noqa: E402

TQQQ = "btm_vol_target_tqqq"


def _stop(level=82.11, sid=SOXL, **kw):
    return {"level": level, "kind": "stop", "strategy_id": sid, "exit_rule_type": "trailing_stop",
            "hold_rule_id": "h1", **kw}


def _qty(c, tk):
    rows = c.get("/api/v1/portfolio").json()["positions"]
    return sum(float(p.get("quantity") or 0) for p in rows if p["ticker"] == tk)


def _two_geared_sleeves_and_a_fired_soxl_stop(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income"), _pos("SOXL", 20.0)]})
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")
    assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
    assert c.post(f"/api/v1/strategies/{TQQQ}/apply?sleeve_pct=15").status_code == 200
    r = c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": "stop_loss",
                                      "mode": "price", "level": 110.0})
    assert r.status_code == 200, r.text
    c.post("/api/v1/rules/evaluate")
    from tests._cards import all_cards
    cards = all_cards(c.get("/api/v1/recommendations").json())
    stop = [x for x in cards if (x.get("apply") or {}).get("kind") == "sell_position"
            and (x.get("apply") or {}).get("ticker") == "SOXL"]
    assert stop, "the fired stop must be a card"
    return stop[0]


# --------------------------------------------------------------------------- #
# The group
# --------------------------------------------------------------------------- #
def test_a_stop_on_one_geared_fund_holds_them_all():
    from app.services import buyback_hold as bh
    held = bh.stop_held({"SOXL": _stop()})
    assert {"TQQQ", "UPRO", "QLD", "SQQQ", "TECL"} <= set(held)
    assert held["TQQQ"]["via"] == "SOXL" and held["TQQQ"]["hold_rule_id"] == "h1"
    assert "via" not in held["SOXL"]
    # -1x funds are inverse, not geared
    assert not {"PSQ", "SH"} & set(held)
    # active() is what the book target, Today and the core realign read
    assert "TQQQ" in bh.active({"SOXL": _stop()}, [])


def test_a_stop_on_a_plain_fund_holds_only_that_fund():
    from app.services import buyback_hold as bh
    assert set(bh.stop_held({"VTI": _stop(345.0, None)})) == {"VTI"}
    # a take-profit (price) hold on a geared fund is not a stop: no group
    assert set(bh.active({"SOXL": {"level": 80.0}}, [])) == {"SOXL"}


def test_the_refusal_names_the_stop_that_holds_it():
    from app.services import buyback_hold as bh
    msg = bh.refusal("TQQQ", bh.stop_held({"SOXL": _stop()})["TQQQ"])
    assert msg.startswith("TQQQ is a 2x/3x fund, and your trailing stop sold SOXL (also 2x/3x) at 82.11")
    assert f"until {SOXL}'s signal is out and back in" in msg
    assert "End the SOXL hold" in msg


# --------------------------------------------------------------------------- #
# Every buyer
# --------------------------------------------------------------------------- #
def test_the_plan_cannot_fund_another_2x_3x_sleeve():
    """The 10-23 preview: SOXL held back, TQQQ funded with the same money."""
    with TestClient(m.app) as c:
        _accept_the_stop(c, _two_geared_sleeves_and_a_fired_soxl_stop(c))
        tq0 = _qty(c, "TQQQ")
        c.post(f"/api/v1/strategies/{TQQQ}/load-basket", json={"mode": "fund"})
        assert _qty(c, "TQQQ") == pytest.approx(tq0, abs=1e-6), "load-basket bought TQQQ"
        prev = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()
        c.post("/api/v1/plan/sleeves/fund?dry_run=false")
        assert _qty(c, "TQQQ") == pytest.approx(tq0, abs=1e-6), f"Fund my sleeves bought TQQQ: {prev}"


def test_a_today_card_cannot_buy_another_2x_3x_fund():
    import asyncio

    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services import buyback_hold as bh
        from app.services.feed_service import ensure_user
        from app.services.intake_service import buy_into_book
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = await ensure_user(s, "pr141-card@example.com")
                await bh.record_stop(s, u, ticker="SOXL", sold_price=82.11, shares=20,
                                     exit_rule_type="trailing_stop", rule_id=None)
                await s.commit()
                with pytest.raises(bh.HeldBack, match="TQQQ is a 2x/3x fund"):
                    await bh.assert_buyable(s, u, ["VTI", "TQQQ"])
                with pytest.raises(bh.HeldBack, match="UPRO is a 2x/3x fund"):
                    await buy_into_book(s, u, ticker="UPRO", market="NYSE", shares=1, price=100.0)
                await s.rollback()
                u = await ensure_user(s, "pr141-card@example.com")   # the rollback expired it
                await bh.assert_buyable(s, u, ["VTI", "PSQ"])          # not blocked
                # you entering a purchase you made is still recorded
                await buy_into_book(s, u, ticker="TQQQ", market="NASDAQ", shares=1, price=50.0,
                                    recording=True)
                await s.commit()
                # ending the SOXL hold ends the block
                await bh.release(s, u, ["SOXL"])
                await s.commit()
                await bh.assert_buyable(s, u, ["TQQQ"])
        finally:
            await eng.dispose()
    asyncio.run(go())


def test_ending_the_soxl_hold_lets_the_plan_fund_tqqq():
    with TestClient(m.app) as c:
        _accept_the_stop(c, _two_geared_sleeves_and_a_fired_soxl_stop(c))
        tq0 = _qty(c, "TQQQ")
        assert c.delete(f"/api/v1/rules/{_hold(c)['id']}").status_code == 200
        c.post(f"/api/v1/strategies/{TQQQ}/load-basket", json={"mode": "fund"})
        assert _qty(c, "TQQQ") > tq0


# --------------------------------------------------------------------------- #
# The held-cash card
# --------------------------------------------------------------------------- #
def test_the_held_cash_card_offers_no_blocked_fund():
    """10-23: "cash is waiting for SOXL" with TQQQ first among the places."""
    import app.services.recommendations as rr
    from app.services import buyback_hold as bh
    holds = bh.active({"SOXL": {**_stop(), "price_now": None}}, [])
    legs = [{"ticker": "TQQQ", "amount_ils": 2000.0, "asset_class": "Equities", "reason": "sleeve short"},
            {"ticker": "VXUS", "amount_ils": 900.0, "asset_class": "Equities", "reason": "under target"}]
    ideas = [{"ticker": "UPRO", "score": 90}, {"ticker": "XLE", "score": 70}]
    card = rr._held_cash_card(holds, 4027.63, 4178.0, 1500.0, nav=19669.6,
                              under_legs=legs, ideas=ideas)
    dests = [d["ticker"] for d in card["apply"]["options"]["redeploy"]["destinations"]]
    assert "TQQQ" not in dests and "UPRO" not in dests, dests
    assert dests[0] == "VXUS" and "XLE" in dests
    # still one hold, SOXL: the blocked funds are not holds of their own
    assert card["apply"]["tickers"] == ["SOXL"]
    assert [x["ticker"] for x in card["apply"]["options"]["buy"]["legs"]] == ["SOXL"]
    assert card["title"].endswith("waiting for SOXL")
    assert "while the SOXL hold lasts, any other 2x/3x fund" in card["why"]


def test_no_drift_top_up_for_a_blocked_sleeve():
    """G6: the daily "TQQQ has drifted to" push kept asking to top up the bet the
    stop had just closed. A blocked fund gets no top-up card, so no push."""
    with TestClient(m.app) as c:
        _accept_the_stop(c, _two_geared_sleeves_and_a_fired_soxl_stop(c))
        from tests._cards import all_cards
        cards = all_cards(c.get("/api/v1/recommendations").json())
        tops = [x for x in cards if (x.get("meta") or {}).get("ticker") in ("TQQQ", "SOXL")
                and ("Add about" in (x.get("action") or "") or (x.get("meta") or {}).get("cold_start"))]
        assert not tops, [x["title"] for x in tops]
