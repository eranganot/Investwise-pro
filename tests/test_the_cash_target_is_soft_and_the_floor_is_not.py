"""PR 64. Eran, 2026-09-06:

    "it should be a soft target. meaning if there is a good/valueble/interesting
     holding that I must buy you can use the cash to do it --> but if you
     recommend me to use my cash for this investment you should explain why I
     should go below the 7.5%. the hard minimum cash I should hold is 150"

**This reverses a decision the code stated outright.** `cash_floor_pct`'s
docstring argued: *"THE FLOOR IS THE TARGET, and that is the point rather than a
shortcut. A reserve the machine is allowed to nibble into is not a reserve."* It
was written against a real failure — the live book sat at 2.92% cash against its
own 3% limit and no screen called that a decision — and that reasoning is kept in
the file rather than deleted, because the failure has not gone away.

What changed is narrower: there are now TWO numbers where there was one.

* the **target** (7.5%, or the objective table) — what the book is managed
  toward, and where every routine card still stops;
* the **hard floor** (₪150, absolute) — which nothing crosses, ever.

Between them is a reserve that may be spent only by a card that names a reason.

**The reason is a precondition, not a convention.** `plan_funding` refuses
`allow_reserve` without a non-empty `reserve_reason`, the same shape as
`describe_funding`'s required `buying_class` and for the same reason: a missing
argument is better than a missing sentence. A boolean on its own would let a
future call site opt in and forget to explain.

**And the bar for reaching the reserve is structural, not a score threshold.**
Any cutoff would be a number invented for someone else's money. The buy-idea card
plans the ordinary way first and only retries against the reserve when trims and
spendable cash could not pay for the purchase at all. The app never decides a
name is worth the reserve — it puts the score, the reasons and the resulting cash
percentage on the card.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import funding_service as fs  # noqa: E402


class _Plan:
    def __init__(self, cash_target_pct=None, objective="Grow"):
        self.cash_target_pct = cash_target_pct
        self.objective = objective


NAV = 21_874.0


# --------------------------------------------------------------------------- #
# 1. two numbers, and which one binds
# --------------------------------------------------------------------------- #
def test_the_hard_floor_is_shekels_not_a_percentage():
    """It must not move when the market does. That is what makes it a floor."""
    assert fs.CASH_HARD_FLOOR_ILS == 150.0


def test_routine_funding_still_stops_at_the_target():
    """Unchanged behaviour, and the point of keeping the old docstring's argument.

    A reserve routine cards may nibble is not a reserve.
    """
    plan = _Plan(cash_target_pct=7.5)
    # 7.5% of 21,874 is ~1,641. A book holding 1,800 may spend ~159 of it.
    assert fs.spendable_cash(1800.0, NAV, "Grow", plan) == pytest.approx(159.45, abs=1.0)
    # At target, nothing is spendable the ordinary way.
    assert fs.spendable_cash(1640.0, NAV, "Grow", plan) == 0.0


def test_the_reserve_is_reachable_only_by_asking():
    plan = _Plan(cash_target_pct=7.5)
    assert fs.spendable_cash(1640.0, NAV, "Grow", plan) == 0.0
    assert fs.spendable_cash(1640.0, NAV, "Grow", plan, allow_reserve=True) == \
        pytest.approx(1490.0)


def test_nothing_crosses_the_hard_floor():
    plan = _Plan(cash_target_pct=7.5)
    assert fs.spendable_cash(150.0, NAV, "Grow", plan, allow_reserve=True) == 0.0
    assert fs.spendable_cash(120.0, NAV, "Grow", plan, allow_reserve=True) == 0.0
    assert fs.spendable_cash(151.0, NAV, "Grow", plan, allow_reserve=True) == \
        pytest.approx(1.0)


def test_on_a_small_book_the_hard_floor_outranks_the_percentage():
    """7.5% of a 1,000 book is 75, which is under the hard minimum.

    Without the `max` the ordinary path would spend into the floor with nobody
    opting in — the percentage quietly overruling the absolute.
    """
    plan = _Plan(cash_target_pct=7.5)
    assert fs.cash_soft_floor_ils(1000.0, "Grow", plan) == 150.0
    assert fs.spendable_cash(200.0, 1000.0, "Grow", plan) == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# 2. the reason is a precondition
# --------------------------------------------------------------------------- #
def _book():
    """One holding, at target, so nothing is trimmable — cash is the only source."""
    class P:
        ticker, market, quantity = "VTI", "NYSE", 100.0
        cost_basis, current_price = 100.0, 100.0
        meta = {"asset_class": "Equities"}
    rows = [P()]
    snap = {"nav": 12_000.0, "exposure_ticker": {"VTI": 1.0}}
    return rows, snap


def test_allow_reserve_without_a_reason_is_refused():
    """A boolean alone would let a call site opt in and forget the sentence."""
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    quiet = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0,
                            cash_ils=1000.0, allow_reserve=True)
    named = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0,
                            cash_ils=1000.0, allow_reserve=True,
                            reserve_reason="TSEM ranks 88/100 and nothing was trimmable")

    assert quiet["from_cash_ils"] < named["from_cash_ils"], (
        "the reserve was spent without a reason to show the reader")
    assert quiet["reserve_used_ils"] == 0.0
    assert named["reserve_used_ils"] > 0.0
    assert named["reserve_reason"]


def test_the_payload_says_where_the_cash_landed():
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    f = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0, cash_ils=1000.0,
                        allow_reserve=True, reserve_reason="a reason")
    assert f["cash_hard_floor_ils"] == 150.0
    assert f["cash_after_ils"] == pytest.approx(150.0, abs=1.0), f
    assert f["cash_after_pct"] == pytest.approx(150.0 / 12_000.0, abs=0.001)
    # The soft target on this book is 900; 850 of it was spent.
    assert f["reserve_used_ils"] == pytest.approx(750.0, abs=1.0), f


def test_a_routine_plan_reports_no_reserve_use():
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    f = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0, cash_ils=1000.0)
    assert f["reserve_used_ils"] == 0.0
    assert f["reserve_reason"] is None


# --------------------------------------------------------------------------- #
# 3. the sentence on the card
# --------------------------------------------------------------------------- #
def test_the_card_explains_why_it_went_below_the_target():
    """Three facts and his own why: how much, where cash lands, floor untouched."""
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    f = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0, cash_ils=1000.0,
                        allow_reserve=True,
                        reserve_reason="TSEM ranks 88/100 on the Grow screen")
    line = fs.describe_funding(f, "Equities")

    assert "cash reserve" in line, line
    assert "hard floor" in line, line
    assert "TSEM ranks 88/100" in line, line
    assert "7.5%" in line, line


def test_a_routine_plan_says_nothing_about_the_reserve():
    """The sentence must not appear when it did not happen."""
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    f = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 1500.0, cash_ils=1000.0)
    assert "reserve" not in fs.describe_funding(f, "Equities").lower()


def test_the_explanation_cannot_be_printed_with_nothing_behind_it():
    """`reserve_reason` is cleared unless the reserve was actually spent."""
    rows, snap = _book()
    plan = _Plan(cash_target_pct=7.5)
    # Small enough to be paid for out of cash above target: no reserve touched.
    f = fs.plan_funding(rows, snap, plan, "Grow", 0.4, 60.0, cash_ils=1000.0,
                        allow_reserve=True, reserve_reason="a reason nobody needs")
    assert f["reserve_used_ils"] == 0.0
    assert f["reserve_reason"] is None
    assert "Worth it because" not in fs.describe_funding(f, "Equities")


# --------------------------------------------------------------------------- #
# 4. the bar for reaching it
# --------------------------------------------------------------------------- #
def test_the_buy_idea_card_tries_the_ordinary_plan_first():
    """Last resort, not first. And no invented score threshold.

    Asserted on the source because the alternative is a full screener fixture
    with a live provider behind it. The rule it guards is the one that keeps the
    reserve meaningful: reached only when trims and spendable cash could not pay.
    """
    import inspect

    from app.services import recommendations

    src = inspect.getsource(recommendations._buy_idea_recs)
    assert src.count("propose_funded_buy(") == 2, (
        "the buy-idea card no longer plans the ordinary way before reaching for "
        "the reserve")
    i = src.index("propose_funded_buy(")
    j = src.index("allow_reserve=True")
    assert i < j, "the reserve attempt comes first"
    assert "if buy is None and cash_ils > _fund.CASH_HARD_FLOOR_ILS:" in src, (
        "the retry is not gated on the ordinary plan having failed")
    # No magic number decides this on Eran's behalf.
    assert "score >" not in src and "score >=" not in src, (
        "a score threshold was introduced; the card is meant to explain and let "
        "him decide")
