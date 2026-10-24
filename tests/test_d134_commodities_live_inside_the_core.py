"""D134 (owner, 2026-10-24): "I want to remove the commodities from my mix, it should be part of
the sleeves/core and not stand alone"; chose "Inside the core". PR 71 carved the objective table's
Commodities weight (Grow: 10%) out of any core that names none; that is now the setting
`commodities_carve_out_pct`, default 0.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import book_target as bt  # noqa: E402
from app.services import thresholds  # noqa: E402

GROWTH_CORE = {"Equities": 1.0}


def test_no_carve_out_by_default_and_ten_restores_pr71():
    assert thresholds.defaults()["commodities_carve_out_pct"] == 0.0
    assert bt.carve_out_fraction() == 0.0
    plain = bt.compose(sleeve_targets={}, core_mix=GROWTH_CORE, objective="Grow",
                       commodities_pct=bt.carve_out_fraction())
    assert "Commodities" not in plain["target"] and plain["commodities_pct"] == 0.0
    assert plain["target"]["Equities"] == 1.0
    tok = thresholds._OVERRIDES.set({"commodities_carve_out_pct": 10.0})
    try:
        old = bt.compose(sleeve_targets={}, core_mix=GROWTH_CORE, objective="Grow",
                         commodities_pct=bt.carve_out_fraction())
    finally:
        thresholds._OVERRIDES.reset(tok)
    assert old["commodities_pct"] == 0.1 and old["target"]["Commodities"] == 0.1


def test_a_core_with_its_own_commodities_keeps_them():
    aw = {"Equities": 0.3, "Fixed Income": 0.55, "Commodities": 0.15}
    out = bt.compose(sleeve_targets={}, core_mix=aw, objective="Grow", commodities_pct=bt.carve_out_fraction())
    assert out["target"]["Commodities"] == 0.15
    assert bt.carved_classes(GROWTH_CORE, "Grow", bt.carve_out_fraction()) == set()


def test_the_plan_tab_has_no_commodities_slice():
    with TestClient(m.app) as c:
        c.put("/api/v1/plan", json={"objective": "Grow"})
        sl = c.get("/api/v1/plan/sleeves").json()
        assert sl["commodities_pct"] == 0.0
        spec = c.get("/api/v1/plan").json().get("thresholds_spec") or {}
        assert "commodities_carve_out_pct" in spec
