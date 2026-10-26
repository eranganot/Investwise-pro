"""PR 154: a stop on a geared EQUITY fund does not hold geared gold or bonds.

Found 2026-10-26 while reviewing the SOXL stop hold: PR 141 (D116) holds every
fund in `strategy_profile.GEARED` after a stop on any one of them. GEARED also
holds UGL (2x gold) and TMF / UBT (3x and 2x Treasuries). On the live book the
2x Gold Trend sleeve (added 10-25) owns UGL, so the day gold's own signal turned
in, the funder would have refused UGL "because SOXL was stopped out".

PR 141's evidence was equity on equity: SOXL and TQQQ, 0.95 daily-return
correlation, down together on 8 of 8 sessions SOXL fell more than 3%. Gold and
Treasuries are not that bet. Owner, 2026-10-26: "continue with your
recommendation" (narrow the group; D116 amended).

The leveraged ceiling (PR 148) still counts every geared fund: this changes
only what a stop holds.
"""
from __future__ import annotations


def _stop(level: float = 82.11, strategy_id: str | None = "btm_trend_soxl") -> dict:
    return {"kind": "stop", "level": level, "strategy_id": strategy_id,
            "rule_id": "h1", "shares": 15.78}


def test_a_soxl_stop_does_not_hold_gold_or_bonds():
    from app.services import buyback_hold as bh
    held = bh.stop_held({"SOXL": _stop()})
    assert not {"UGL", "TMF", "UBT"} & set(held), "gold and Treasuries are not SOXL's bet"
    # the bet itself is still held -- PR 141 unchanged for equity funds
    assert {"TQQQ", "UPRO", "QLD", "TECL", "SPXL"} <= set(held)
    assert "UGL" not in bh.active({"SOXL": _stop()}, [])


def test_a_gold_stop_holds_gold_only():
    from app.services import buyback_hold as bh
    held = bh.stop_held({"UGL": _stop(60.0, "div_2x_gold_trend")})
    assert set(held) == {"UGL"}, "a gold stop holds gold, not the equity funds"


def test_a_treasury_stop_holds_that_fund_only():
    from app.services import buyback_hold as bh
    assert set(bh.stop_held({"TMF": _stop(40.0, None)})) == {"TMF"}


def test_the_ceiling_still_counts_gold_and_bonds():
    from app.services import leveraged_ceiling as lc
    from app.services.strategy_profile import GEARED, STOP_GROUP
    assert {"UGL", "TMF", "UBT"} <= GEARED
    assert not {"UGL", "TMF", "UBT"} & STOP_GROUP
    assert STOP_GROUP < GEARED
    assert lc.geared() == GEARED, "the ceiling counts every geared fund"
