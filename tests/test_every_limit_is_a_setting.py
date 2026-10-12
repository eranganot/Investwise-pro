"""PR 104c: the goal's limits are settings (Eran, 2026-10-07: "I want every
decision to be controlled and changeable through the app").

The crash-risk limit decided which protection the app would suggest and lived
only as `HALVE_FLOOR_PCT = 1.0`; the on-track bar as `GOAL_CONFIDENCE = 0.70`.
Both are now plan settings (migration 0027) with those values as defaults.
"""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs
from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict


def test_the_defaults_are_his_answers_and_the_plan_overrides_them():
    st = fs.settings_of(SimpleNamespace(objective="Grow", target_roi_pct=30, kelly_fraction=None,
                                        return_confidence=None, halve_limit_pct=None, on_track_pct=None))
    assert st["halve_limit_pct"] == 1.0 and st["goal_confidence"] == 0.70
    assert st["halve_limit_source"] == st["on_track_source"] == "default"
    st = fs.settings_of(SimpleNamespace(objective="Grow", target_roi_pct=30, kelly_fraction=None,
                                        return_confidence=None, halve_limit_pct=5.0, on_track_pct=60.0))
    assert st["halve_limit_pct"] == 5.0 and st["goal_confidence"] == 0.60
    assert st["halve_limit_source"] == st["on_track_source"] == "plan"


def test_the_retune_honours_the_limit_it_is_given():
    import numpy as np

    from tests.test_protection_tuning import _stats
    table = {(False, 10.0): _stats(0.5, 0.0), (False, 15.0): _stats(0.6, 0.0), (False, 20.0): _stats(0.7, 0.1),
             (True, 10.0): _stats(19.9, 0.6), (True, 15.0): _stats(22.7, 0.8), (True, 20.0): _stats(24.7, 2.9)}

    def run(sl=None, gd="same", **kw):
        retire = sl is not None and sl[0].get("trail") is None
        return table.get((retire, round(gd["level"] * 100, 2) if gd and gd != "same" else 10.0), _stats(0.0, 0.0))
    args = (run, [{"col": 0, "trail": 0.2}], {"level": 0.10, "rearm": 0.075, "fraction": 0.25, "col": 0},
            {"sleeves": {"soxl": {"exit_rule_ids": ["r1"], "rules": ["trailing_stop SOXL (20%)"]}},
             "guard": {"rule_id": "g1"}}, ["soxl"], np.zeros((10, 1)), 1, table[(False, 10.0)])
    at_1 = fs._tune_protection(*args)                       # his default
    at_3 = fs._tune_protection(*args, floor=3.0)            # a looser limit he can now set
    assert at_1["halve_floor_pct"] == 1.0 and at_1["best"]["guard_pct"] == 15.0
    assert at_3["halve_floor_pct"] == 3.0 and at_3["best"]["guard_pct"] == 20.0


async def test_the_limits_save_and_a_verdict_under_other_limits_stands_down(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            r = c.put("/api/v1/plan", json={"halve_limit_pct": 5, "on_track_pct": 60})
            assert r.status_code == 200
            f = c.get("/api/v1/plan").json()["frontier"]
            assert f["halve_limit_pct"] == 5.0 and abs(f["goal_confidence"] - 0.60) < 1e-9
            assert c.put("/api/v1/plan", json={"on_track_pct": 99}).status_code == 422
            assert c.put("/api/v1/plan", json={"halve_limit_pct": 0}).status_code == 422
            # a verdict solved at the old 70% bar is not this plan's any more
            await _store_verdict(own_session, _goal_verdict(cur, p_star=0.70))
            assert [x for x in _cards(c) if x.get("dimension") == "goal"] == []
            await _store_verdict(own_session, _goal_verdict(cur, p_star=0.60))
            assert [x for x in _cards(c) if x.get("dimension") == "goal"]
    finally:
        await _reset_plan(own_session)
        async with own_session() as s:
            from sqlalchemy import select

            from app.models.tables import Plan
            from app.services.feed_service import ensure_superadmin
            user = await ensure_superadmin(s)
            plan = (await s.execute(select(Plan).where(Plan.user_id == user.id))).scalar_one_or_none()
            if plan is not None:
                plan.halve_limit_pct = None
                plan.on_track_pct = None
                await s.commit()


def test_the_old_goal_settings_form_is_gone():
    html = (Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    for gone in ('id="p_hor"', "Years invested", 'id="p_obj"', 'id="p_risk"', 'onclick="savePlan()"'):
        assert gone not in html, gone
    assert "function openDialsSheet()" in html and 'onclick="openDialsSheet()' in html
