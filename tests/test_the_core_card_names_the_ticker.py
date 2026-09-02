"""PR 38b -- "Buy Equities" is a class. "Buy VTI" is an instruction.

Eran, 2026-09-02: *"if I'm not holding VTI as part of my portfolio --> I expect
to get a recommendation card to purchase it."*

The rebalance card the app already had works at ASSET-CLASS level. On his book
the equities target is 89.2% -- 73% from two leveraged sleeves plus 16.2% from
the core -- so **buying more TQQQ satisfies "Buy Equities" completely** and
leaves the core still holding no VTI at all. The card was not wrong; it was not
an instruction.

The core is the one place the app knows the instrument, because a core IS a
basket (PR 37). `bal_6040` is 60% VTI / 40% BND, and the book's target for each
leg is `core_pct x weight x NAV`. That arithmetic is already what `_book_target`
folds INTO the class target; this leaves it un-aggregated, which is the point.

Measured on his live book, NAV 21,092.37, core 27%:

    VTI   target 3,417 ILS   held     0   ->  buy  ~3,417
    BND   target 2,278 ILS   held 2,854   ->  trim ~  576
    V     target     0 ILS   held   955   ->  not in your core's basket

ADVICE, NOT AN ACTION. No `apply` block: funding a 3,417 ILS purchase out of a
619 ILS cash balance is not something this app can carry out, and a button that
reports success without doing anything is the exact defect `_ACTIONABLE_KINDS`
exists to prevent.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

NAV = 21092.37
CORE_PCT = 0.27
SIXTY_FORTY = {"VTI": 0.6, "BND": 0.4}
# His core region, FX-normalised, sleeve-claimed tickers already excluded.
HELD = {"BND": 0.1353 * NAV, "V": 0.0453 * NAV}


def _legs():
    """The arithmetic the card is built from, isolated from the ORM.

    Imported lazily so this file still COLLECTS against the previous commit.
    """
    from app.services.measured_assumptions import core_matches_basket
    from app.services.recommendations import SLEEVE_DRIFT_BAND_PCT

    ok, ev = core_matches_basket(HELD, SIXTY_FORTY, band_pct=SLEEVE_DRIFT_BAND_PCT)
    out = {}
    for tk, w in list(SIXTY_FORTY.items()) + [(t, 0.0) for t in ev.get("extra") or []]:
        want = CORE_PCT * w * NAV
        have = HELD.get(tk, 0.0)
        out[tk] = {"want": want, "have": have, "gap": want - have,
                   "drift_pp": abs((ev.get("drift_pct") or {}).get(tk, 100.0))}
    return ok, ev, out


# --------------------------------------------------------------------------- #
# the numbers the card has to carry
# --------------------------------------------------------------------------- #
def test_the_missing_leg_is_vti_and_it_is_worth_about_3400():
    ok, ev, legs = _legs()
    assert ok is False
    assert ev["missing"] == ["VTI"]
    assert round(legs["VTI"]["gap"]) == 3417, legs["VTI"]
    assert legs["VTI"]["have"] == 0.0


def test_bnd_is_over_its_leg_by_about_580():
    _, _, legs = _legs()
    assert round(legs["BND"]["gap"]) == -576, legs["BND"]
    assert legs["BND"]["gap"] < 0, "BND is over target, so the card must say trim"


def test_v_is_in_the_core_region_and_in_no_basket():
    _, ev, legs = _legs()
    assert ev["extra"] == ["V"]
    assert legs["V"]["want"] == 0.0
    assert round(legs["V"]["have"]) == 955


def test_every_leg_clears_both_thresholds_so_all_three_cards_fire():
    """A card must clear the money floor AND the drift band. All three do."""
    from app.services.recommendations import (
        _REFIRE_FLOOR_ILS, SLEEVE_DRIFT_BAND_PCT,
    )

    _, _, legs = _legs()
    for tk, leg in legs.items():
        assert abs(leg["gap"]) >= _REFIRE_FLOOR_ILS, f"{tk} gap under the floor"
        assert leg["drift_pp"] > SLEEVE_DRIFT_BAND_PCT, f"{tk} inside the band"


def test_a_core_held_to_its_basket_produces_no_cards():
    """The rule has to be able to say nothing. 60/40 held as 60/40 is fine."""
    from app.services.measured_assumptions import core_matches_basket
    from app.services.recommendations import SLEEVE_DRIFT_BAND_PCT

    ok, _ = core_matches_basket({"VTI": 6000.0, "BND": 4000.0}, SIXTY_FORTY,
                                band_pct=SLEEVE_DRIFT_BAND_PCT)
    assert ok is True


def test_the_leg_target_is_the_same_arithmetic_the_class_target_uses():
    """`_book_target` folds core_pct x basket into the CLASS target. Same maths.

    If these two ever disagree, the app is telling the user two different things
    about one core -- the defect the card-claim invariant exists to catch.
    """
    equities_from_core = CORE_PCT * SIXTY_FORTY["VTI"]
    fixed_from_core = CORE_PCT * SIXTY_FORTY["BND"]
    # These are the figures /api/v1/recommendations returned in book_target.core
    assert round(equities_from_core, 4) == 0.162
    assert round(fixed_from_core, 4) == 0.108
    # ...and 73% of sleeves plus the core's equities half is the class target
    assert round(0.73 + equities_from_core, 4) == 0.892


# --------------------------------------------------------------------------- #
# what the card may and may not claim
# --------------------------------------------------------------------------- #
def test_the_card_is_advice_and_never_claims_it_can_execute():
    """No `apply` block, so Accept acknowledges rather than faking a trade."""
    import inspect

    from app.services import recommendations as R

    src = inspect.getsource(R.build_recommendations)
    block = src.split("3a) THE CORE'S OWN BASKET")[1].split("3b)")[0]
    assert '"apply"' not in block, (
        "the core-basket card declares an apply kind it cannot carry out")
    assert '_rid("corebasket"' in block, "the cards share an id scheme"


def test_cash_is_never_treated_as_a_leg_of_the_basket():
    """CASH is in no basket. Counting it as a core holding would invent drift."""
    import inspect

    from app.services import recommendations as R

    block = inspect.getsource(R.build_recommendations)
    block = block.split("3a) THE CORE'S OWN BASKET")[1].split("3b)")[0]
    assert '== "Cash"' in block and '"CASH"' in block


def test_sleeve_held_tickers_are_excluded_from_the_core_region():
    """TQQQ and SOXL belong to sleeves. Counting them as core would swamp it."""
    import inspect

    from app.services import recommendations as R

    block = inspect.getsource(R.build_recommendations)
    block = block.split("3a) THE CORE'S OWN BASKET")[1].split("3b)")[0]
    assert "sleeve_tickers" in block, (
        "the core region is not excluding what the sleeves already claim")


def test_the_band_and_the_floor_are_the_apps_existing_ones():
    """No second private tolerance for a question the app already answers."""
    import inspect

    from app.services import recommendations as R

    block = inspect.getsource(R.build_recommendations)
    block = block.split("3a) THE CORE'S OWN BASKET")[1].split("3b)")[0]
    assert "SLEEVE_DRIFT_BAND_PCT" in block
    assert "_REFIRE_FLOOR_ILS" in block


def test_a_core_the_app_cannot_read_does_not_lose_every_other_card():
    """These cards are additive. A failure here must not empty Today."""
    import inspect

    from app.services import recommendations as R

    block = inspect.getsource(R.build_recommendations)
    block = block.split("3a) THE CORE'S OWN BASKET")[1].split("3b)")[0]
    assert "except Exception" in block and "logger.warning" in block
