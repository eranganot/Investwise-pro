"""Nothing manages the core, and the app never asked.

`core` has been `null` since C6 shipped on 2026-08-13. Not refused, not
deferred -- never put to anyone. `set_core` existed with no route, so the only
way to choose one was to apply a static family card on the Strategies tab, where
it reads as "add this strategy" and its real effect (naming the target mix the
whole book is measured against) is invisible. The Plan tab said "Core - 36%" and
one line of grey text, which is a statement about SIZE and silent about who
manages it.

This matters more than a missing dropdown. The solver already reported on
2026-08-16 that the core ALONE breaches the 13.3% drawdown ceiling -- so no
sleeve size, including zero, fixes it, and its own card says "change the core".
The one control that could act on that finding had no door.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _client():
    logging.disable(logging.CRITICAL)
    return TestClient(m.app)


def _seed(c, objective="Grow", risk="High"):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 400.0, "listing_price": 400.0, "quantity": 10.0,
         "cost_basis": 340.0, "expected_return_pct": 7, "volatility_pct": 20}]})
    c.put("/api/v1/plan", json={"objective": objective, "risk_tolerance": risk})


# --------------------------------------------------------------------------- #
# the choice is reachable
# --------------------------------------------------------------------------- #
def test_a_every_eligible_family_is_offered_with_what_it_would_do():
    try:
        with _client() as c:
            _seed(c)
            o = c.get("/api/v1/plan/core/options").json()
    finally:
        logging.disable(logging.NOTSET)

    assert o["core_is_unset"] is True
    assert o["current"] is None
    opts = o["options"]
    assert len(opts) == 14, f"expected the 14 static families, got {len(opts)}"
    assert all(x["eligible"] for x in opts), "a rule-based strategy leaked into the core list"

    # A choice you cannot compare is not a choice. Every row has to carry the
    # mix it would impose and what it is expected to cost.
    for x in opts:
        assert x["target_allocation"], f"{x['strategy_id']} offers no target mix"
        assert x["est_max_drawdown_pct"] is not None, f"{x['strategy_id']} has no drawdown figure"
        assert x["expected_return_pct"] is not None
        assert x["drawdown_source"] in ("measured", "estimate")


def test_b_estimated_and_measured_drawdown_are_never_merged():
    """A modelled number wearing the authority of a measured one.

    That is the failure this app keeps having, so the payload keeps them in
    separate fields and names which one it is showing.
    """
    try:
        with _client() as c:
            _seed(c)
            o = c.get("/api/v1/plan/core/options").json()
    finally:
        logging.disable(logging.NOTSET)

    for x in o["options"]:
        assert "est_max_drawdown_pct" in x
        assert "measured_max_drawdown_pct" in x
        if x["drawdown_source"] == "estimate":
            assert x["measured_max_drawdown_pct"] is None
            assert x["measured_period"] is None
        else:
            assert x["measured_max_drawdown_pct"] is not None
            assert x["measured_period"], "a measured figure must carry its window"


def test_c_the_core_can_be_set_through_a_door_of_its_own():
    """`set_core` had no route. Its only caller was `apply_strategy`."""
    try:
        with _client() as c:
            _seed(c)
            r = c.post("/api/v1/plan/core", json={"strategy_id": "bal_all_weather"}).json()
            assert r["ok"] is True and r["action"] == "set"
            sl = c.get("/api/v1/plan/sleeves").json()
            o = c.get("/api/v1/plan/core/options").json()
    finally:
        logging.disable(logging.NOTSET)

    assert sl["core"]["strategy_id"] == "bal_all_weather"
    assert sl["core"]["target_allocation"] == {"Equities": 0.35, "Fixed Income": 0.4,
                                               "Commodities": 0.25}
    assert o["core_is_unset"] is False
    assert o["current"] == "bal_all_weather"
    assert [x for x in o["options"] if x["is_current"]][0]["strategy_id"] == "bal_all_weather"


def test_d_setting_a_core_buys_and_sells_nothing():
    """It names a target. Acting on it stays a separate decision you take."""
    try:
        with _client() as c:
            _seed(c)
            before = c.get("/api/v1/portfolio").json()
            c.post("/api/v1/plan/core", json={"strategy_id": "pre_low_vol"})
            after = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert before["nav_ils"] == pytest.approx(after["nav_ils"], abs=0.01)
    assert ({p["ticker"]: p["quantity"] for p in before["positions"]}
            == {p["ticker"]: p["quantity"] for p in after["positions"]})


def test_e_a_rule_based_sleeve_is_still_refused_as_a_core():
    """A sleeve and a core are different objects, and the route must not blur them.

    Letting `btm_trend_soxl` be a core would hand the book a target allocation
    derived from a 3x fund's model basket.
    """
    try:
        with _client() as c:
            _seed(c)
            r = c.post("/api/v1/plan/core", json={"strategy_id": "btm_trend_soxl"}).json()
            o = c.get("/api/v1/plan/core/options").json()
    finally:
        logging.disable(logging.NOTSET)

    assert r["ok"] is False
    assert "rule-based sleeve" in r["reason"]
    assert "btm_trend_soxl" not in {x["strategy_id"] for x in o["options"]}


def test_f_an_unknown_strategy_is_refused():
    try:
        with _client() as c:
            _seed(c)
            r = c.post("/api/v1/plan/core", json={"strategy_id": "not_a_thing"}).json()
    finally:
        logging.disable(logging.NOTSET)
    assert r["ok"] is False


def test_g_choosing_again_replaces_rather_than_adding():
    """Two is_core rows would give `get_core` an oldest-first answer that
    silently ignored the newer choice."""
    try:
        with _client() as c:
            _seed(c)
            c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"})
            r = c.post("/api/v1/plan/core", json={"strategy_id": "bal_all_weather"}).json()
            sl = c.get("/api/v1/plan/sleeves").json()
    finally:
        logging.disable(logging.NOTSET)

    assert r["action"] == "changed" and r["previous"] == "bal_6040"
    assert sl["core"]["strategy_id"] == "bal_all_weather"


# --------------------------------------------------------------------------- #
# the banner
# --------------------------------------------------------------------------- #
def test_h_the_page_shows_the_choice_only_while_it_is_unmade():
    """A banner that cannot be dismissed, because there is nothing to dismiss.

    It disappears when a core exists -- the only thing that actually resolves
    it. The old copy pointed at "the goal tabs below", which is where the
    decision was hiding in the first place.
    """
    from pathlib import Path

    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert "function corePicker()" in html
    assert "Nothing manages your core" in html
    assert "chooseCore(" in html
    # Rendered only when there is no core.
    assert "${c?\"\":corePicker()}" in html
    # And it must not send the reader back to the tabs that hid the choice.
    assert "Pick one from the goal tabs below" not in html


def test_i_the_options_are_fetched_only_when_there_is_a_choice_to_make():
    from pathlib import Path

    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert "if(MYSLEEVES && !MYSLEEVES.core && !COREOPTS){" in html, (
        "the fourteen families should not be fetched to draw nothing")
