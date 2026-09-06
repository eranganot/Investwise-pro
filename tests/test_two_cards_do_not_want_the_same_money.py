"""PR 60. Eran, 2026-09-06, looking at Today after the B2 swap:

    "I don't understand these cards... why is TQQQ part of the core and not the
    sleeves? it should be part of the swing sleeve."

Two cards were on screen. One offered to sell all ₪7,476 of TQQQ into the core's
Classic 60/40 basket. The other said the 33% QQQ sleeve held nothing and needed
about ₪7,238. **Same money, two destinations, and neither card knew about the
other.**

WHY TQQQ WAS "IN THE CORE". The core is the *remainder* -- `PlanSleeve`'s own
docstring says so, "the core is the implicit remainder, not a row". PR 59 swapped
`btm_swing_dip` for `btm_qqq_hold`, so nothing claimed TQQQ any more, so it fell
into the core region, so rule 1 of `plan_realign` said *not in the core's basket,
sell it in full*. Every step correct; the conclusion wrong, because "retire the
overlay, hold the base" meant the money goes to QQQ, not to VTI and cash.

It would really have collided: `_fund_sleeve` excludes only tickers a sleeve
CURRENTLY claims, so its funding plan was free to sell the same TQQQ.

AND THE HEADLINE UNDERSTATED IT BY 17x. `_moved` read `buys_ils` -- the basket's
legs were only ₪422 short -- so a card whose first step sold ₪7,476 was titled
"Rebalance about ₪422 inside your own book". Every individual leg was honest;
the sentence on top of them was not, and neither said where the remaining
~₪7,040 went. It went to cash.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.core_realign import describe, plan_realign

# The live book on 2026-09-06, reproduced: core 26% of ~₪21,900 running Classic
# 60/40, holding TQQQ that no sleeve claims any more.
BOOK = dict(basket={"VTI": 0.6, "BND": 0.4}, core_pct=0.26, nav=21900.0,
            held={"TQQQ": 7476.0, "VTI": 2994.0, "BND": 2278.0},
            cash_ils=600.0, cash_floor_ils=600.0, cgt_rate=0.25,
            gain_frac={"TQQQ": 0.0075})


def test_the_card_says_where_the_proceeds_go():
    """₪7,476 out, ₪422 in. The difference is not nothing, and it is not zero.

    `describe()` is the card-claim invariant in one function -- what it returns
    is what Apply does. It listed every LEG and no CONSEQUENCE, so a full exit
    against a small gap left most of a third of the book sitting in cash with
    nothing on the card saying so.
    """
    plan = plan_realign(**BOOK)

    assert plan["sells_ils"] == 7476.0
    assert round(plan["buys_ils"]) == 422
    assert round(plan["to_cash_ils"]) == 7040, plan

    lines = describe(plan)
    assert any("stay as cash" in ln for ln in lines), lines
    # And the number in that line is the one on the payload, not a second sum.
    assert any("7,040" in ln for ln in lines), lines


def test_an_unfunded_sleeve_gets_first_claim_and_the_card_says_so():
    """The tie-break, and why it goes this way.

    A sleeve size is a decision made on the Plan tab. The core is what is left
    over. So when both want the same unclaimed position, the sleeve wins and the
    core card says what it is not touching -- silence there reads as "the app
    did not notice", which is how two cards came to propose two destinations for
    one position in the first place.
    """
    deferred = [{"ticker": "TQQQ", "ils": 7476.0,
                 "why": "your sleeves are about ₪7,238 short of the sizes you set"}]
    held = {k: v for k, v in BOOK["held"].items() if k != "TQQQ"}
    plan = plan_realign(**{**BOOK, "held": held, "deferred": deferred})

    sold = [leg["ticker"] for leg in plan["legs"] if leg["action"] == "sell"]
    assert "TQQQ" not in sold, (
        "the core card is still selling the position an unfunded sleeve needs")

    lines = describe(plan)
    assert any("TQQQ" in ln and "NOT sold here" in ln for ln in lines), lines


def test_a_basket_leg_is_never_deferred():
    """Core money doing its job is not funding for something else.

    Deferring VTI would stop the core ever reaching its own 60/40, which is the
    thing this card exists to do.
    """
    plan = plan_realign(**{**BOOK, "held": {"VTI": 6000.0, "BND": 500.0}})
    trimmed = [leg["ticker"] for leg in plan["legs"] if leg["action"] == "sell"]
    bought = [leg["ticker"] for leg in plan["legs"] if leg["action"] == "buy"]
    assert "VTI" in trimmed and "BND" in bought, plan


def test_the_headline_amount_is_the_money_that_moves():
    """`est_amount` fed the mute and re-fire logic, so it was not cosmetic.

    A card sized at ₪422 that actually trades ₪7,476 is muted, re-fired and
    merged as though it were a small one.
    """
    import inspect

    from app.services import recommendations

    src = inspect.getsource(recommendations)
    i = src.index('"title": f"Bring your core back to')
    window = src[i - 2000:i + 2000]
    assert '_moved = max(_sold' in window, (
        "the core card's headline is sized from the buy leg again")
    assert '_to_cash' in window, "the card no longer names the leftover cash"


# --------------------------------------------------------------------------- #
# PR 60.1 -- the deferral shipped dead, and finding out why found something worse
# --------------------------------------------------------------------------- #
def test_occupancy_reports_a_gap_in_one_unit():
    """`target_pct - held_pct` subtracted a percent from a fraction.

    `target_pct` is 0-100 (33.9 means 33.9%). `held_pct` is a FRACTION (0.3509),
    a convention the UI documents at index.html:1579. So a sleeve holding 35.09%
    of the book against a 33.9% target reported `gap_pct: +33.54` -- reading as
    "a third of the book under-funded" when it is 1.2 points OVER. Wrong by the
    whole held fraction, and always in the direction of "you hold nothing".
    """
    import inspect

    from app.services import sleeve_service as sv

    target_pct = round(33.9, 4)
    held_pct = round(7686.0 / 21900.0, 4)
    gap = round(target_pct - held_pct * 100.0, 4)

    assert gap < 0, f"a sleeve held above its target must report a negative gap, got {gap}"
    assert abs(gap - (33.9 - 35.09)) < 0.05, gap

    src = inspect.getsource(sv.sleeve_occupancy)
    assert "target_pct - held_pct * 100.0" in src, (
        "gap_pct is subtracting a fraction from a percent again")


def test_the_core_reads_the_key_occupancy_actually_emits():
    """`sleeve_occupancy` has never emitted `sleeve_pct`. Two loops read it.

    One is mine, and it is why PR 60's deferral shipped dead -- `unfunded_ils`
    came to 0.00 on a book with a 33% sleeve holding nothing, so the core went
    on offering to sell TQQQ. The deploy check caught it in production.

    The other predates this work and is worse: `want_pct` fell to 0.0 for every
    sleeve, so `held > 0` was the test and the core was handed the sleeves'
    ENTIRE held value as "overshoot" -- SOXL's whole ₪7,686 position on the live
    book. `core_realign` promises it "will not trim a sleeve BELOW its own
    target"; that guarantee was vacuous. It never bit only because the funding
    path engages when buys exceed sells plus cash, which this book never hit.
    """
    import inspect

    from app.services import recommendations

    src = inspect.getsource(recommendations._core_plan)
    assert 's.get("sleeve_pct")' not in src, (
        "_core_plan is reading a key sleeve_occupancy does not emit; every "
        "sleeve reads as 0% target")
    assert src.count('s.get("target_pct")') == 2, (
        "both the overshoot loop and the unfunded loop must read target_pct")
