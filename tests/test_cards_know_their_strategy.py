"""PR 9 -- one target, a scope on every card, and no card fighting a sleeve.

Everything here is premised on the book that was actually live on 2026-08-25,
because every defect was found on it rather than reasoned about:

    NAV                21,205
    sleeves            btm_swing_dip  38.4% -> TQQQ  (holding 38.6%)
                       btm_trend_soxl 25.6% -> SOXL  (holding 25.7%)
    core               bal_all_weather, 36% of the book, 35/40/25
    holdings           97.0% Equities, 3.0% Cash

and the three things it was serving:

    HIGH    "TQQQ is 39% of your portfolio - trim 24.231 shares"
            ...against a sleeve targeting 38.4% and sitting on it.
    MEDIUM  "Sell Equities ~12,139 / Buy Fixed Income ~8,469 / Buy Commodities
            ~5,293" -- the CORE's 35/40/25 measured against 100% of the book, so
            every leg was out by exactly 1/0.36 = 2.78x.
    LOW     "Add a commodities sleeve ... against your 10% target" -- in the same
            payload as a card quoting 25% for the same asset class.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services.book_target import compose, explain, sleeve_class_mix  # noqa: E402
from app.services.card_scope import scope_for  # noqa: E402
from app.services.contradictions import check  # noqa: E402

ALL_WEATHER = {"Equities": 0.35, "Fixed Income": 0.4, "Commodities": 0.25}
LIVE_SLEEVES = {"TQQQ": 0.384, "SOXL": 0.256}
NAV = 21205.04


# --------------------------------------------------------------------------- #
# The target
# --------------------------------------------------------------------------- #
def test_the_core_mix_is_not_the_book_target():
    """THE defect, in one assertion.

    A target that governs 36% of the book is not a target for the book. Handing
    it to `AllocationEngine.compute` against the whole NAV overstates every leg
    by 1/0.36, and the engine does not check -- it has no idea the target it was
    given covers a third of the portfolio.
    """
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=ALL_WEATHER, objective="Grow")

    assert bt["target"] == {"Equities": 0.766, "Fixed Income": 0.144, "Commodities": 0.09}
    assert bt["sleeves"] == {"Equities": 0.64}
    assert bt["core"] == {"Equities": 0.126, "Fixed Income": 0.144, "Commodities": 0.09}
    assert bt["core_pct"] == 0.36

    # The numbers the card WAS printing, and the ones it should have.
    current_equities = 20566.81                       # the live figure, not 0.97 x NAV
    assert round(current_equities - bt["target"]["Equities"] * NAV) == 4324
    assert round(ALL_WEATHER["Fixed Income"] * NAV) == 8482        # what it asked for
    assert round(bt["target"]["Fixed Income"] * NAV) == 3054       # what it should ask for


def test_a_book_target_always_sums_to_one():
    """`AllocationEngine` assumes this and never checks.

    A class present in `current_allocation` but missing from the target gets
    `target = 0.0` and a SELL sized at the whole holding, so a target summing to
    less than 1 emits sells that fund nothing. `_rebalance_to` renormalises
    downstream, which HIDES the bug at exactly the point it would be caught.
    """
    for sleeves in ({}, {"TQQQ": 0.384}, LIVE_SLEEVES, {"TQQQ": 0.9}):
        for core in (ALL_WEATHER, None, {"Equities": 1.0}):
            bt = compose(sleeve_targets=sleeves, core_mix=core, objective="Grow")
            assert abs(sum(bt["target"].values()) - 1.0) < 0.005, (sleeves, core, bt)


def test_a_fully_sleeved_book_leaves_the_core_nothing_rather_than_going_negative():
    bt = compose(sleeve_targets={"TQQQ": 0.7, "SOXL": 0.3}, core_mix=ALL_WEATHER,
                 objective="Grow")
    assert bt["core_pct"] == 0.0
    assert bt["core"] == {}
    assert bt["target"] == {"Equities": 1.0}


def test_no_sleeves_means_the_core_mix_IS_the_book_target():
    """The case the old code was right about, which is why it survived so long."""
    bt = compose(sleeve_targets={}, core_mix=ALL_WEATHER, objective="Grow")
    assert bt["target"] == {"Equities": 0.35, "Fixed Income": 0.4, "Commodities": 0.25}


def test_no_core_falls_back_to_the_objective_for_the_remainder_only():
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=None, objective="Grow")
    # Grow is 80/10/10, over the 36% the sleeves left.
    assert bt["core"] == {"Equities": 0.288, "Fixed Income": 0.036, "Commodities": 0.036}
    assert bt["target"]["Equities"] == 0.928


def test_sleeve_tickers_are_classified_not_assumed_to_be_equities():
    """A bond or commodity sleeve must not land in Equities by default.

    `classify` resolves the ETF sets by membership, and getting this wrong would
    make a bond sleeve read as 64 points of equity -- the same class of error as
    the one this module exists to fix, one level down.
    """
    assert sleeve_class_mix({"TQQQ": 0.4, "SOXL": 0.2}) == {"Equities": 0.6000000000000001}
    assert sleeve_class_mix({"BND": 0.3}) == {"Fixed Income": 0.3}
    assert sleeve_class_mix({"IAU": 0.1, "DBC": 0.1}) == {"Commodities": 0.2}


def test_the_card_can_say_where_the_number_came_from():
    """"77%" was the number that was wrong for twelve days and nothing on the
    screen could have caught it. "77% (64% your sleeves + 13% your core)" can be
    checked against the Plan tab in one glance."""
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=ALL_WEATHER, objective="Grow")
    bt["core_strategy_name"] = "All-Weather"
    assert explain(bt, "Equities") == "77% (64% your sleeves + 13% your core)"
    assert "your core" in explain(bt, "Fixed Income")
    assert "36%" in explain(bt, "Fixed Income")


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
_SCOPE_ARGS = dict(ticker_owner={"TQQQ": "btm_swing_dip", "SOXL": "btm_trend_soxl"},
                   sleeve_names={"btm_swing_dip": "Swing: Dip-Buy",
                                 "btm_trend_soxl": "Trend-Filtered Semis"},
                   sleeve_pcts={"btm_swing_dip": 38.4, "btm_trend_soxl": 25.6},
                   core_id="bal_all_weather", core_name="All-Weather", core_pct=36.0)


def test_a_card_acting_on_a_sleeve_ticker_names_that_sleeve():
    sc = scope_for({"id": "rule_1bc6cd92", "apply": {"kind": "trim", "ticker": "TQQQ"}}, **_SCOPE_ARGS)
    assert sc["kind"] == "sleeve"
    assert sc["strategy_id"] == "btm_swing_dip"
    assert "38.4% sleeve" in sc["label"]


def test_a_card_acting_on_an_unsleeved_ticker_belongs_to_the_core():
    """The core IS the remainder. A ticker no sleeve claims has nowhere else to
    live, so "book" would be a dodge rather than an answer."""
    sc = scope_for({"id": "rec_x", "apply": {"kind": "trim", "ticker": "V"}}, **_SCOPE_ARGS)
    assert sc["kind"] == "core" and sc["strategy_id"] == "bal_all_weather"


def test_a_card_naming_no_ticker_is_whole_book():
    for rec in ({"id": "rec_behind", "apply": {"kind": "set_plan"}},
                {"id": "rec_rebal", "apply": {"kind": "rebalance_to_objective",
                                              "asset_class": "Equities"}}):
        assert scope_for(rec, **_SCOPE_ARGS)["kind"] == "book"


def test_a_mixed_card_is_book_scoped_not_sleeve_scoped():
    """A tax-harvest card naming four losers, one of them sleeved, acts on the
    BOOK. Calling it a sleeve card would license it to argue with that sleeve's
    target, which is precisely the authority it must not have."""
    rec = {"id": "rec_tax", "apply": {"kind": "sell_losers", "tickers": ["TQQQ", "V", "SCHD"]}}
    assert scope_for(rec, **_SCOPE_ARGS)["kind"] == "book"


def test_a_card_that_already_names_a_strategy_is_believed():
    rec = {"id": "stratrules_btm_trend_so", "apply": {"kind": "create_rules"}}
    assert scope_for(rec, **_SCOPE_ARGS)["strategy_id"] == "btm_trend_soxl"
    rec2 = {"id": "rec_fund", "apply": {"kind": "fund_sleeve", "strategy_id": "btm_swing_dip"}}
    assert scope_for(rec2, **_SCOPE_ARGS)["strategy_id"] == "btm_swing_dip"


def test_legs_and_rule_specs_count_as_tickers():
    """Cards grew their subject fields one generator at a time. A scope derived
    from `apply.ticker` alone would call a multi-leg redeploy "whole book"."""
    rec = {"id": "rec_redeploy", "apply": {"kind": "redeploy_cash",
                                           "legs": [{"ticker": "TQQQ", "amount_ils": 100}]}}
    assert scope_for(rec, **_SCOPE_ARGS)["strategy_id"] == "btm_swing_dip"
    rec2 = {"id": "rec_mom", "apply": {"kind": "create_rules",
                                       "rules": [{"ticker": "SOXL", "rule_type": "trailing_stop"}]}}
    assert scope_for(rec2, **_SCOPE_ARGS)["strategy_id"] == "btm_trend_soxl"


# --------------------------------------------------------------------------- #
# Contradictions
# --------------------------------------------------------------------------- #
_WEIGHTS = {"TQQQ": 0.386, "SOXL": 0.257, "V": 0.303, "SCHD": 0.025, "CASH": 0.03}
_TGTS = {"TQQQ": 0.384, "SOXL": 0.256}


def test_a_card_may_not_sell_a_sleeve_that_is_on_target():
    """THE live one. The loudest card on Today wanted to sell 61% of a sleeve
    sitting 0.2 points above the target the user chose, because the rule behind
    it was armed at 15% when the sleeve was 15%."""
    trim = {"id": "rule_1bc6cd92", "title": "TQQQ is 39% of your portfolio",
            "severity": "HIGH", "apply": {"kind": "trim", "ticker": "TQQQ", "shares": 24.231}}
    kept, found = check([trim], sleeve_targets=_TGTS, nav=NAV, weights=_WEIGHTS)

    assert kept == [], "the card survived"
    assert found[0]["kind"] == "sells_a_sleeve_at_target"
    assert found[0]["suppressed"] is True
    assert "38.4%" in found[0]["detail"] and "38.6%" in found[0]["detail"]


def test_a_card_may_still_trim_a_sleeve_that_has_run_past_its_target():
    """The guard is scoped to the hazard, not to the feature. A sleeve genuinely
    over its target SHOULD be trimmed, and suppressing that would be the mirror
    image of the bug."""
    over = dict(_WEIGHTS, TQQQ=0.52)
    trim = {"id": "rec_drift", "title": "TQQQ has drifted to 52% of a 38% sleeve",
            "apply": {"kind": "trim", "ticker": "TQQQ"}}
    kept, found = check([trim], sleeve_targets=_TGTS, nav=NAV, weights=over)
    assert [r["id"] for r in kept] == ["rec_drift"]
    assert not [f for f in found if f["kind"] == "sells_a_sleeve_at_target"]


def test_an_advisory_card_about_a_sleeve_is_never_suppressed():
    """Only cards that MOVE MONEY can contradict anything. Suppressing an
    advisory would hide exactly the explanation a user needs."""
    note = {"id": "rec_verdict", "title": "TQQQ verdict", "apply": {"kind": "none"},
            "meta": {"ticker": "TQQQ"}}
    kept, found = check([note], sleeve_targets=_TGTS, nav=NAV, weights=_WEIGHTS)
    assert [r["id"] for r in kept] == ["rec_verdict"]
    assert found == []


def test_buying_and_selling_one_asset_class_is_flagged_on_both_cards():
    """Live: "Sell Equities (~12,139)" beside "Buy 2,120 of VXUS (global
    equity)". Neither card is wrong alone; the pair is nonsense."""
    sell = {"id": "rec_a", "title": "Sell Equities", "est_amount": 12139,
            "apply": {"kind": "rebalance_to_objective", "asset_class": "Equities"}}
    buy = {"id": "rec_b", "title": "Buy VXUS", "est_amount": 2120,
           "apply": {"kind": "buy_funded", "ticker": "VXUS", "asset_class": "Equities"}}
    kept, found = check([sell, buy], sleeve_targets={}, nav=NAV, weights=_WEIGHTS)

    assert len(kept) == 2, "neither is suppressed -- which to drop is the user's call"
    hit = [f for f in found if f["kind"] == "buys_and_sells_one_class"]
    assert hit and hit[0]["asset_class"] == "Equities"
    assert any("buys Equities" in n for n in sell["notes"])
    assert any("sells Equities" in n for n in buy["notes"])


def test_two_cards_may_not_quote_two_targets_for_one_class():
    """Live: a rebalance card measuring Commodities against 25% (the core's mix)
    beside a commodities card measuring it against 10% (the objective's). This
    is a BUG in a generator, not a choice to put to the user, so it is reported
    loudly rather than suppressed."""
    stale = {"id": "rec_c", "title": "Add a commodities sleeve",
             "apply": {"kind": "buy_funded", "ticker": "USO"},
             "audit_trail": {"raw_data": {"asset_class": "Commodities", "target_weight": 0.10}}}
    _, found = check([stale], sleeve_targets={}, nav=NAV, weights=_WEIGHTS,
                     book_target={"Commodities": 0.09})
    hit = [f for f in found if f["kind"] == "two_targets_for_one_class"]
    assert hit and "10.0%" in hit[0]["detail"] and "9.0%" in hit[0]["detail"]


def test_the_cards_together_may_not_liquidate_the_book_silently():
    """Live: trim TQQQ 5,003 + sell Equities 12,139 + sell V 2,180 = 19,322 of
    selling out of 20,567 held. Each card funds itself; none of them knows the
    others exist."""
    cards = [{"id": f"rec_{i}", "title": t, "est_amount": amt,
              "apply": {"kind": "trim", "ticker": "V"}}
             for i, (t, amt) in enumerate([("a", 5003), ("b", 12139), ("c", 2180)])]
    _, found = check(cards, sleeve_targets={}, nav=NAV, weights={"CASH": 0.03})
    hit = [f for f in found if f["kind"] == "cards_exceed_the_book"]
    assert hit, "three cards proposing 94% of the book raised nothing"
    assert len(hit[0]["cards"]) == 3
    assert all("do not share a funding budget" in n for c in cards for n in c["notes"])


def test_a_quiet_book_produces_no_contradictions():
    """The expected state. A checker that fires on a healthy book is noise."""
    ok = [{"id": "rec_ok", "title": "Raise your contributions", "apply": {"kind": "set_plan"}}]
    kept, found = check(ok, sleeve_targets=_TGTS, nav=NAV, weights=_WEIGHTS,
                        book_target={"Equities": 0.766})
    assert kept == ok and found == []


# --------------------------------------------------------------------------- #
# The rules that started it
# --------------------------------------------------------------------------- #
def test_a_suggested_cap_is_never_below_the_sleeve_target_it_would_fight():
    """`cap = max(15, 5*ceil(weight/5))` is derived from the CURRENT weight and
    knew nothing about sleeves, so it wrote a 15% cap on a ticker whose sleeve
    would later target 38.4% -- and from that day the app told him hourly to
    trim a holding it was simultaneously targeting."""
    from app.services.rules_service import _suggestions_from

    # The weight that MATTERS is the one on the day the cap was suggested. TQQQ
    # was small then -- `max(15, 5*ceil(13/5))` is 15 -- and the sleeve grew to
    # 38.4% afterwards. Run the formula at today's 38.6% and it happens to give
    # 40, which is why this was invisible: the bug only exists in the gap
    # between when a cap is armed and when the sleeve is resized.
    idx = {"TQQQ": {"price": 69.01, "cost": 60.0, "weight_pct": 13.0}}
    out = _suggestions_from(idx, set(), {"TQQQ": 38.4})
    caps = [r for h in out for r in h["rules"] if r["rule_type"] == "max_weight"]
    assert caps, "no cap suggested at all"
    assert caps[0]["level"] >= 38.4, f"suggested {caps[0]['level']}% against a 38.4% sleeve"
    assert "sleeve targets" in caps[0]["why"].lower()


def test_without_a_sleeve_the_cap_formula_is_unchanged():
    """The floor is scoped to the hazard. An ordinary concentrated holding still
    gets the concentration cap it always got."""
    from app.services.rules_service import _suggestions_from

    idx = {"V": {"price": 382.41, "cost": 300.0, "weight_pct": 30.3}}
    out = _suggestions_from(idx, set(), {})
    caps = [r for h in out for r in h["rules"] if r["rule_type"] == "max_weight"]
    assert caps and caps[0]["level"] == 35.0
    assert "trim back toward" in caps[0]["why"]


@pytest.mark.parametrize("weight,floor,expected_min", [(38.6, 38.4, 40.0), (13.0, 25.6, 30.0)])
def test_the_lifted_cap_sits_ABOVE_the_target_not_on_it(weight, floor, expected_min):
    """A cap exactly at the target fires the moment the sleeve is correctly
    funded, which is the same bug with a friendlier number."""
    from app.services.rules_service import _suggestions_from

    out = _suggestions_from({"TQQQ": {"price": 69.01, "cost": 60.0, "weight_pct": weight}},
                            set(), {"TQQQ": floor})
    caps = [r for h in out for r in h["rules"] if r["rule_type"] == "max_weight"]
    assert caps[0]["level"] >= expected_min
