"""C24 (D125 follow-up): the target mix manages the 2x/3x sleeves to the leveraged
ceiling, and the excess is managed in the core.

PR 148 stopped every buy at 44%, but the book target still summed the sleeves as
chosen (40% TQQQ + 23% SOXL = 63%), so drift, the Plan tab and the max_weight caps
measured against a share no buyer would ever reach.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import leveraged_ceiling as lc  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import SOXL, _pos  # noqa: E402
from tests.test_pr141_one_bet_one_hold import TQQQ  # noqa: E402


def test_cap_scales_the_2x_3x_targets_and_leaves_the_rest():
    t = {"TQQQ": 0.40, "SOXL": 0.23, "QQQ": 0.10}
    capped, cut = lc.cap_targets(t)
    assert capped["QQQ"] == 0.10
    assert capped["TQQQ"] + capped["SOXL"] == pytest.approx(0.44)
    assert capped["TQQQ"] / capped["SOXL"] == pytest.approx(40 / 23)
    assert sum(cut.values()) == pytest.approx(0.19)
    assert lc.cap_targets({"TQQQ": 0.30, "QQQ": 0.5}) == ({"TQQQ": 0.30, "QQQ": 0.5}, {})


def _two_geared_sleeves(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("BND", 40.0, cls="Fixed Income")]})
    c.post("/api/v1/recommendations/restore")
    assert c.post(f"/api/v1/strategies/{TQQQ}/apply?sleeve_pct=40").status_code == 200
    assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=23").status_code == 200


def test_the_book_target_moves_the_excess_into_the_core():
    with TestClient(m.app) as c:
        _two_geared_sleeves(c)
        bt = c.get("/api/v1/recommendations").json()["book_target"]
        assert sum(bt["ceiling_cut"].values()) == pytest.approx(0.19, abs=0.002), bt
        assert bt["core_pct"] >= 0.37 - 0.002, "the core gets the 19 points the ceiling took"
        assert sum(bt["target"].values()) == pytest.approx(1.0, abs=0.005)
        lv = c.get("/api/v1/plan/sleeves").json()["leveraged_ceiling"]
        assert lv["chosen_pct"] == pytest.approx(63.0, abs=0.1)
        assert lv["managed_pct"] == pytest.approx(44.0, abs=0.1)
        assert lv["ceiling_pct"] == 44.0


def test_no_ceiling_means_the_sleeves_as_chosen():
    with TestClient(m.app) as c:
        _two_geared_sleeves(c)
        assert c.put("/api/v1/plan", json={"thresholds": {"leveraged_ceiling_pct": 0}}).status_code == 200
        bt = c.get("/api/v1/recommendations").json()["book_target"]
        assert not bt.get("ceiling_cut")
        lv = c.get("/api/v1/plan/sleeves").json()["leveraged_ceiling"]
        assert lv["managed_pct"] == pytest.approx(lv["chosen_pct"])


def test_the_plan_tab_says_where_the_excess_went():
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "managed in the core" in html and "leveraged_ceiling" in html
