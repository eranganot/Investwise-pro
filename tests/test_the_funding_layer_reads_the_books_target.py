"""The funding layer measured drift against a target the rest of the app had left behind.

Measured on production, 2026-09-03 at commit `d4ca292`. Both cards on Today were
funded by selling VTI -- the Classic 60/40 core basket bought two days earlier --
and both carried the same reason:

    "cheapest way to raise it: Equities is the most overweight class
     (86% against a 80% target) and VTI is among its least-taxed positions"

`/api/v1/mix`, at the same moment, on the same book:

    target_allocation      {"Equities": 0.892, "Fixed Income": 0.108}
    current_allocation     {"Equities": 0.8625, ...}
    drift_percentage       {"Equities": -0.0295}
    rebalance_required     false

**Opposite signs, same book, same screen.** 80% is `OBJ_TARGET["Grow"]`, the
objective table, which knows nothing about sleeves or a core. 89.2% is the book's
own target -- 73% from two strategy sleeves plus 16.2% from the core -- which is
what `/mix`, the rebalance card and the contradictions check all read. Equities at
86.25% is six points over the first and three points under the second, so the
funding layer was selling equities to correct an overweight that did not exist,
and reaching for the core to do it because sleeves are excluded and the core is
not.

Two separate faults, and the second survives the first being fixed:

1. `rank_trim_candidates`, `plan_funding` and `propose_funded_buy` read
   `OBJ_TARGET[objective]` with no way for a caller to supply the book's target.
2. The reason sentence claimed "the most overweight class" unconditionally. It
   is reached whenever a candidate scores at all, and the score carries a
   tax-friendliness term that stays positive on a book where nothing is over
   anything.

Imports are lazy so this file still COLLECTS against the previous commit.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from types import SimpleNamespace as NS  # noqa: E402

NAV = 100_000.0


def _fx():
    from app.services.fx import fx_rate
    return fx_rate("USD") or 1.0


def _holding(ticker, market, value_ils, price, *, loser=True):
    """A position worth `value_ils` shekels at `price` in its trading currency."""
    qty = value_ils / (price * _fx())
    return NS(ticker=ticker, market=market, quantity=qty, current_price=price,
              cost_basis=price * (1.10 if loser else 0.90), meta={})


def _book(equities_ils, fixed_income_ils, commodities_ils):
    """VTI / BND / COMT at the values asked for. The remainder of NAV is cash."""
    return [
        _holding("VTI", "NYSE", equities_ils, 376.88),
        _holding("BND", "NYSE", fixed_income_ils, 71.83),
        _holding("COMT", "NYSE", commodities_ils, 40.0),
    ]


def _snap():
    return {"nav": NAV}


def _reasons(cands):
    return " | ".join(c["reason"] for c in cands)


def test_a_trim_reason_may_not_name_an_overweight_that_is_not_there():
    """Nothing in this book is over its target. The sentence still said it was.

    Equities 79%, Fixed Income 9.5%, Commodities 9.5% against Grow's 80/10/10,
    with the balance in cash: every class is at or under target and no name is
    near the 25% cap. Every candidate is still offered -- the tax-friendliness
    term keeps the score positive -- and every one of them used to explain itself
    as "the most overweight class".
    """
    from app.services.funding_service import rank_trim_candidates

    cands = rank_trim_candidates(_book(79_000.0, 9_500.0, 9_500.0), _snap(), "Grow", 0.25)
    assert cands, "expected the candidates to still be offered, just honestly described"
    text = _reasons(cands)
    assert "overweight" not in text, (
        f"nothing in this book is above its target, and the reason says otherwise:\n"
        f"  {text}")
    assert "corrects no drift" in text, (
        f"a sale that corrects no drift has to say so:\n  {text}")


def test_the_book_target_decides_what_counts_as_overweight():
    """The same book, the same call, two targets, opposite verdicts.

    Equities 89% / Fixed Income 11% is exactly on a sleeves-plus-core target of
    89/11 and nine points over the objective table's 80. Which one the funding
    layer reads decides whether it sells the core.
    """
    from app.services.funding_service import rank_trim_candidates

    rows, snap = _book(89_000.0, 11_000.0, 0.0), _snap()

    # The objective table: Equities is nine points over, so the sale is a rebalance.
    by_objective = rank_trim_candidates(rows, snap, "Grow", 0.25)
    assert "most overweight class" in _reasons(by_objective), (
        "the objective table should see Equities as overweight here; if it does "
        "not, this test is no longer proving anything")

    # The book's own target: Equities is exactly on target, so there is nothing
    # to correct and the reason may not claim there is.
    by_book = rank_trim_candidates(rows, snap, "Grow", 0.25,
                                   book_target={"Equities": 0.89, "Fixed Income": 0.11})
    assert "overweight" not in _reasons(by_book), (
        f"the book is on its target and the funding layer still calls it "
        f"overweight:\n  {_reasons(by_book)}")


def test_an_overweight_too_small_to_trade_is_not_an_overweight():
    """An overweight below the app's own materiality unit is not one.

    Fixed Income at 10.2% against a 10% target is over by ILS 200 on a 100,000
    book -- under MIN_TRADE_ILS, so no sale can correct it, and at display
    precision it reads "the most overweight class (10% against a 10% target)".
    That sentence names an overweight of zero.

    Found by threading the book target through and re-running the live book: BND
    came out over by 0.06 points, ILS 15.73, and inherited the claim VTI had just
    been relieved of.
    """
    from app.services.funding_service import MIN_TRADE_ILS, rank_trim_candidates

    assert 0.002 * NAV < MIN_TRADE_ILS, "this fixture depends on 200 being immaterial"
    cands = rank_trim_candidates(_book(79_000.0, 10_200.0, 9_500.0), _snap(), "Grow", 0.25)
    text = _reasons(cands)
    assert "overweight" not in text, (
        f"a 200 ILS overweight on a 100,000 book is not something a trade can "
        f"correct, and the reason claims it is:\n  {text}")


def test_the_book_target_is_the_target_and_it_still_holds_commodities():
    """PR 71 replaced the PR 42 merge, and this test used to pin the merge.

    The merge kept the Commodities idea cards alive by filling that class from
    the objective table -- and on 2026-09-22 it summed to 110%: USO was bought
    toward a 10% target the rebalance card did not have, and sold by it the same
    day. The card-survival concern is now met by the book target itself:
    `compose` carves the objective's Commodities share out of a core that names
    none, so the book target holds Commodities AND sums to 1.0.
    """
    from app.services.allocation_mix import OBJ_TARGET
    from app.services.book_target import compose
    from app.services.funding_service import targets_for

    assert targets_for("Grow") == OBJ_TARGET["Grow"]
    assert targets_for("Grow", {}) == OBJ_TARGET["Grow"], "an empty target is not a target"

    bt = compose(sleeve_targets={"SOXL": 0.40, "QQQ": 0.19},
                 core_mix={"Equities": 0.6, "Fixed Income": 0.4},
                 objective="Grow", cash_pct=0.0064)["target"]
    t = targets_for("Grow", bt)
    assert t == bt, "the book target is used as-is -- nothing is filled in beside it"
    assert t["Commodities"] == OBJ_TARGET["Grow"]["Commodities"], (
        "the commodity share now lives IN the book target, so the idea cards survive")
    assert abs(sum(t.values()) - 1.0) < 0.005, f"a target mix must sum to 100%: {t}"
