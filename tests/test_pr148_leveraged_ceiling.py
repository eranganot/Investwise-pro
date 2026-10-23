"""PR 148 (D125) -- a ceiling on the 2x/3x share of the book (owner, 2026-10-23: 44%).

The plan's leveraged sleeves are 40% (TQQQ vol-target) + 23% (SOXL trend): fully
invested, 63% of the book in 2x/3x funds, and nothing in the app capped it. PR 141
stops a buy-back after a stop; this caps the share before one.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import leveraged_ceiling as lc  # noqa: E402
from app.services import thresholds  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import SOXL, _pos  # noqa: E402
from tests.test_pr141_one_bet_one_hold import TQQQ, _qty  # noqa: E402


def _rid(*a):
    return "rec_" + "_".join(str(x) for x in a)


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #
def test_the_setting_is_on_the_card_thresholds_sheet_at_44():
    assert "leveraged_ceiling_pct" in thresholds.SPEC
    assert thresholds.defaults()["leveraged_ceiling_pct"] == 44.0
    assert lc.ceiling_pct() == 44.0


def test_headroom_counts_2x_3x_funds_only_and_not_the_inverse_ones():
    held = {"VTI": 500.0, "TQQQ": 200.0, "SOXL": 100.0, "PSQ": 100.0, "CASH": 100.0}
    assert lc.geared_value(held) == 300.0
    assert lc.headroom_ils(1000.0, held) == pytest.approx(140.0)     # 440 - 300
    assert lc.share_pct(1000.0, held) == 30.0


def test_clip_scales_the_2x_3x_legs_and_leaves_the_rest():
    legs = [{"ticker": "TQQQ", "buy_ils": 300.0}, {"ticker": "SOXL", "buy_ils": 100.0},
            {"ticker": "QQQ", "buy_ils": 250.0}]
    out, clipped = lc.clip_legs(legs, 200.0, min_leg=40.0)
    by = {x["ticker"]: x["buy_ils"] for x in out}
    assert by == {"TQQQ": 150.0, "SOXL": 50.0, "QQQ": 250.0}
    assert clipped == 200.0
    out, clipped = lc.clip_legs(legs, 40.0, min_leg=40.0)       # both 2x/3x shares (30, 10) under a lot: dropped
    assert {x["ticker"] for x in out} == {"QQQ"} and clipped == 400.0
    assert lc.clip_legs(legs, None, min_leg=40.0) == (legs, 0.0)  # no ceiling


def test_zero_means_no_ceiling():
    tok = thresholds._OVERRIDES.set({"leveraged_ceiling_pct": 0.0})
    try:
        assert lc.headroom_ils(1000.0, {"TQQQ": 900.0}) is None
        assert lc.over_ceiling_card([], 1000.0, {"TQQQ": 900.0}, min_trade=40.0, rid=_rid) is None
    finally:
        thresholds._OVERRIDES.reset(tok)


# --------------------------------------------------------------------------- #
# Today
# --------------------------------------------------------------------------- #
def test_a_today_buy_that_does_not_fit_is_dropped_and_says_why():
    held = {"VTI": 560.0, "TQQQ": 430.0}                     # 43% of 1,000; room 10
    recs = [{"id": "rec_buy_tqqq", "title": "Add to TQQQ", "est_amount": 200.0,
             "apply": {"kind": "buy_funded", "ticker": "TQQQ"}},
            {"id": "rec_buy_vti", "title": "Add to VTI", "est_amount": 200.0,
             "apply": {"kind": "buy_funded", "ticker": "VTI"}},
            {"id": "rule_bb", "title": "Your buy-back rule", "est_amount": 200.0, "why": "It fired.",
             "apply": {"kind": "buy_funded", "ticker": "SOXL"}}]
    out, hidden = lc.filter_cards(recs, 1000.0, held, min_trade=40.0)
    assert [r["id"] for r in out] == ["rec_buy_vti", "rule_bb"]
    assert hidden[0]["id"] == "rec_buy_tqqq" and "over your 44% ceiling" in hidden[0]["reason"]
    assert "over your 44% ceiling" in out[1]["why"], "your own rule's card stays, and says so"


def test_a_cash_card_keeps_its_plain_legs_and_clips_the_2x_3x_ones():
    held = {"VTI": 600.0, "TQQQ": 380.0, "CASH": 20.0}      # room 60
    recs = [{"id": "rec_cash", "title": "Invest cash", "est_amount": 300.0,
             "apply": {"kind": "redeploy_cash", "legs": [{"ticker": "TQQQ", "amount_ils": 150.0},
                                                          {"ticker": "VTI", "amount_ils": 150.0}]}}]
    out, hidden = lc.filter_cards(recs, 1000.0, held, min_trade=40.0)
    legs = {x["ticker"]: x["amount_ils"] for x in out[0]["apply"]["legs"]}
    assert legs == {"TQQQ": 60.0, "VTI": 150.0} and out[0]["est_amount"] == 210.0 and not hidden


def test_over_the_ceiling_one_card_trims_the_largest_2x_3x_holding():
    rows = [SimpleNamespace(ticker="TQQQ", quantity=10.0), SimpleNamespace(ticker="SOXL", quantity=4.0)]
    held = {"VTI": 400.0, "TQQQ": 400.0, "SOXL": 200.0}     # 60% of 1,000
    c = lc.over_ceiling_card(rows, 1000.0, held, min_trade=40.0, rid=_rid)
    assert c["apply"] == {"kind": "trim", "ticker": "TQQQ", "shares": 4.0}   # 160 of 400 -> 4 of 10
    assert c["est_amount"] == 160.0 and c["meta"]["after_pct"] == 44.0
    assert lc.over_ceiling_card(rows, 1000.0, {"VTI": 600.0, "TQQQ": 400.0},
                                min_trade=40.0, rid=_rid) is None             # 40%: under


# --------------------------------------------------------------------------- #
# The writer and the planners
# --------------------------------------------------------------------------- #
def test_the_book_writer_refuses_an_app_buy_over_the_ceiling_but_records_yours():
    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services.feed_service import ensure_user
        from app.services.intake_service import buy_into_book, set_cash
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = await ensure_user(s, "pr148-writer@example.com")
                await set_cash(s, u, 10000.0)
                await s.commit()
                # $5,000 of TQQQ into a 10,000 ILS book: far over 44% (room 4,400 ILS)
                with pytest.raises(lc.OverCeiling, match="over your 44% ceiling"):
                    await buy_into_book(s, u, ticker="TQQQ", market="NASDAQ", shares=100.0, price=50.0)
                await s.rollback()
                u = await ensure_user(s, "pr148-writer@example.com")
                # a small one fits
                await buy_into_book(s, u, ticker="SOXL", market="NASDAQ", shares=1.0, price=10.0)
                # you entering a purchase you made is recorded whatever the share
                await buy_into_book(s, u, ticker="UPRO", market="NASDAQ", shares=1000.0, price=100.0,
                                    recording=True)
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(go())


def test_the_refusal_is_a_held_back_so_accept_returns_a_clean_400():
    from app.services.buyback_hold import HeldBack
    assert issubclass(lc.OverCeiling, HeldBack)


def test_fund_my_sleeves_stops_the_2x_3x_part_at_the_ceiling():
    """40% TQQQ + 23% SOXL on a plain book: the preview buys 2x/3x up to 44%, no more."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 60.0), _pos("BND", 40.0, cls="Fixed Income")]})
        c.post("/api/v1/recommendations/restore")
        assert c.post(f"/api/v1/strategies/{TQQQ}/apply?sleeve_pct=40").status_code == 200
        assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=23").status_code == 200
        prev = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()
        geared = sum(float(b.get("buy_ils") or 0.0) for x in prev.get("sleeves") or []
                     for b in (x.get("buys") or []) if lc.is_geared(b.get("ticker")))
        nav = float(prev.get("nav") or 10000.0)
        assert geared <= 0.445 * nav + 1.0, prev
        assert "ceiling" in (prev.get("message") or ""), prev.get("message")
        # and the one-sleeve button, after the other has taken the room
        c.post(f"/api/v1/strategies/{TQQQ}/load-basket", json={"mode": "fund"})
        r = c.post(f"/api/v1/strategies/{SOXL}/load-basket", json={"mode": "fund"}).json()
        tq, sx = _qty(c, "TQQQ"), _qty(c, "SOXL")
        port = c.get("/api/v1/portfolio").json()
        tot = sum(float(p.get("value_ils") or 0.0) for p in port["positions"]) or 1.0
        g = sum(float(p.get("value_ils") or 0.0) for p in port["positions"] if lc.is_geared(p["ticker"]))
        assert 100.0 * g / tot <= 44.5, (tq, sx, r)


def test_every_buy_path_checks_the_ceiling():
    """Same guard as PR 139's: a buy path added later that forgets fails here."""
    import ast
    import pathlib
    checks = {"assert_within", "clip_legs", "filter_cards", "headroom_ils", "OverCeiling"}
    bad = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == "buy_into_book":
                continue
            seen = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", None)
                    for n in ast.walk(fn) if isinstance(n, (ast.Attribute, ast.Name))}
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                if (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) != "buy_into_book":
                    continue
                rec = any(k.arg == "recording" and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in n.keywords)
                if not rec and not (checks & seen):
                    bad.append(f"{path}:{fn.name}")
    # The writer itself is the backstop for these; listed so a new one is a decision.
    known = {"app/services/recommendations.py:_apply_core_to_basket",
             "app/services/recommendations.py:_apply_core_realign",
             "app/services/recommendations.py:_rebalance_to"}
    assert not (set(bad) - known), f"buy paths with no ceiling check: {sorted(set(bad) - known)}"
