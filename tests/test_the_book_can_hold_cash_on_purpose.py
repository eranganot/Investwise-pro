"""The book targets 0% cash and holds 2.9% of it, and nothing calls that a drift.

Measured on production 2026-09-04, commit `c93ceb9`. `/api/v1/mix`:

    target_allocation   {"Equities": 0.892, "Fixed Income": 0.108}
    current_allocation  {"Equities": 0.8643, "Cash": 0.0292, "Fixed Income": 0.1065}
    drift_percentage    {"Cash": 0.0292, "Equities": -0.0277, ...}
    rebalance_required  false

**Cash is not in the target at all**, so its target is zero by omission -- the
same silent-zero trap `targets_for` was written to avoid one layer down. ILS
618.65 sits at 2.92% of a ILS 21,207.78 book against a target of nothing, twice
`MIN_TRADE_ILS`, and the app has no opinion about it because a class it never
modelled cannot drift.

And it was quietly worse than "no opinion". `funding_service.CASH_FLOOR_PCT`
floors cash at 3% for a `Grow` book, so `spendable_cash` was already returning
zero -- the app had spent the reserve down to its own limit and nothing on any
screen called that a decision.

Eran's decision, 2026-09-04: **target 7.5% cash, deliberately, as dry powder --
"so I'd be able to buy something if there is a good opportunity."**

**WHERE THE CASH COMES FROM IS THE WHOLE DESIGN.** `compose` builds the target
as sleeves + core, where the core's share is the REMAINDER after the sleeves.
Two ways to make room for cash, and only one of them is honest:

  * Scale everything by 0.925. That silently rewrites the sleeve targets the
    user set one at a time -- a sleeve configured at 38.4% of NAV would be
    managed to 35.5% without anyone saying so. `book_target`'s own docstring is
    about the last time the app did that: *"The app was telling him to destroy
    a sleeve it had set itself."*
  * Take it from the CORE's remainder, which is the part of the book the user
    delegated rather than specified. Sleeves stay exactly where they were set.

The second. On the live book: sleeves 73%, remainder 27%, so cash 7.5% and the
core strategy 19.5% -- and `bal_6040` at 60/40 across 19.5 gives Equities 11.7
and Fixed Income 7.8.

    Equities  0.73 + 0.117  = 0.847
    Fixed Income      0.078 = 0.078
    Cash                    = 0.075
                              -----
                              1.000

**This changes which cards exist, and in the opposite direction from today.**
Against the new target the book is 4.6 points UNDER on cash (about ILS 972) and
2.9 points OVER on Fixed Income. Today's screen says there is nothing to do;
with a cash target it should say to raise about ILS 972, mostly out of BND.
That is what asking for dry powder means, and it is stated here so it is a
consequence someone chose rather than one that appeared.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# The live book: two sleeves at 73% of NAV, core `bal_6040`.
SLEEVES = {"TQQQ": 0.384, "SOXL": 0.346}
CORE_6040 = {"Equities": 0.6, "Fixed Income": 0.4}


def _compose(cash_pct=0.0):
    from app.services.book_target import compose
    # commodities_pct=0: these pin the CASH arithmetic alone. The PR 71
    # Commodities carve-out has its own tests (test_one_target_holds_commodities).
    return compose(sleeve_targets=SLEEVES, core_mix=CORE_6040,
                   objective="Grow", cash_pct=cash_pct, commodities_pct=0.0)


def test_no_cash_target_is_exactly_what_the_book_does_today():
    """The regression guard. A default that changes the book is not a default.

    Everything below only matters if the zero case is byte-for-byte what
    production is managed to right now.
    """
    from app.services.book_target import compose

    with_arg = _compose(0.0)
    without = compose(sleeve_targets=SLEEVES, core_mix=CORE_6040, objective="Grow",
                      commodities_pct=0.0)
    assert with_arg == without, (
        "adding the parameter changed the answer when the parameter is zero")
    assert "Cash" not in with_arg["target"], with_arg["target"]
    assert abs(sum(with_arg["target"].values()) - 1.0) < 0.005


def test_the_cash_reserve_comes_out_of_the_core_and_not_the_sleeves():
    """The sleeves are what the user chose by hand. They do not move.

    If this ever fails by the sleeves shrinking, the app has gone back to
    quietly re-sizing positions the user set deliberately -- which is the
    defect `book_target` was written to fix.
    """
    bt = _compose(0.075)

    assert bt["sleeves"] == _compose(0.0)["sleeves"], (
        f"the cash reserve moved the sleeves: {bt['sleeves']} vs "
        f"{_compose(0.0)['sleeves']}")
    assert abs(bt["sleeved_pct"] - 0.73) < 0.001, bt["sleeved_pct"]

    # The core absorbed all of it: 27% remainder -> 7.5 cash + 19.5 core.
    assert abs(bt["core_pct"] - 0.195) < 0.001, (
        f"the core should hold the remainder minus the reserve: {bt}")
    assert abs(bt["cash_pct"] - 0.075) < 0.001, bt


def test_the_target_reads_the_way_the_arithmetic_says():
    """Equities 84.7, Fixed Income 7.8, Cash 7.5 -- and it sums to 1."""
    t = _compose(0.075)["target"]
    assert abs(t["Equities"] - 0.847) < 0.001, t
    assert abs(t["Fixed Income"] - 0.078) < 0.001, t
    assert abs(t["Cash"] - 0.075) < 0.001, t
    assert abs(sum(t.values()) - 1.0) < 0.005, t


def test_a_reserve_bigger_than_the_core_is_clamped_and_says_so():
    """A silent clamp is a lie the size of the difference.

    Sleeves already claim 73%, so 40% cash is not available without taking it
    from them -- which this deliberately will not do. It gives what the core
    has, and reports that it was capped, because the alternative is a target
    that quietly means something other than what was asked for.
    """
    bt = _compose(0.40)

    assert abs(bt["cash_pct"] - 0.27) < 0.001, (
        f"expected the reserve capped at the core's 27%: {bt}")
    assert bt.get("cash_clamped") is True, (
        f"the reserve was capped and the payload does not say so: {bt}")
    assert abs(bt["core_pct"]) < 0.001, bt
    assert bt["sleeves"] == _compose(0.0)["sleeves"], "the sleeves still do not move"
    assert abs(sum(bt["target"].values()) - 1.0) < 0.005, bt["target"]


def test_a_core_that_already_holds_cash_adds_to_the_reserve():
    """`OBJ_TARGET["Preserve"]` holds 10% cash. The reserve is on top of it.

    Stated rather than discovered: the reserve is a floor the user asked for,
    not a ceiling on what the core may choose to hold.
    """
    from app.services.book_target import compose

    bt = compose(sleeve_targets={}, core_mix={"Equities": 0.3, "Fixed Income": 0.6,
                                              "Cash": 0.1},
                 objective="Preserve", cash_pct=0.075)
    # No sleeves, so the core gets 92.5% and its own 10% of that is 9.25.
    assert abs(bt["target"]["Cash"] - (0.075 + 0.0925)) < 0.002, bt["target"]
    assert abs(sum(bt["target"].values()) - 1.0) < 0.005, bt["target"]


async def test_the_plan_is_where_the_reserve_is_configured(monkeypatch):
    """It is a property of the book, so it lives with the objective and horizon.

    `cash_target_pct` is stored the way every other `_pct` column on `plans` is
    -- as a percentage, 7.5 rather than 0.075 -- because a column that means
    something different from its neighbours is a bug waiting for the next
    reader.
    """
    from types import SimpleNamespace as NS

    import app.services.book_target as bt_mod

    async def _targets(session, user, **kw):
        return SLEEVES

    async def _core_id(session, user):
        return "bal_6040"

    async def _plan(session, user):
        return NS(objective="Grow", cash_target_pct=7.5)

    import app.services.sleeve_service as sv
    monkeypatch.setattr(sv, "all_sleeve_targets", _targets)
    monkeypatch.setattr(sv, "core_strategy_id", _core_id)
    monkeypatch.setattr(bt_mod, "get_plan", _plan, raising=False)

    out = await bt_mod.book_target(None, None, "Grow")
    assert abs(out["cash_pct"] - 0.075) < 0.001, (
        f"the plan says 7.5 and the book target says {out.get('cash_pct')!r}: {out}")
    assert abs(out["target"]["Cash"] - 0.075) < 0.001, out["target"]


def test_the_reserve_is_also_the_floor_that_funding_may_not_spend():
    """A reserve the machine may nibble into is not a reserve.

    This is the one judgement call inside the decision, so it is asserted
    rather than buried. On the live book `Grow` floors cash at 3% and the
    balance sat at 2.92% -- the app had already spent the reserve down to its
    own limit, and `spendable_cash` was returning 0 for that reason rather than
    because anyone had decided anything.

    With the target set, the floor is the target: routine cards may not fund
    themselves out of the powder, so deploying it is a deliberate act. That is
    what dry powder is for, and it is why `spendable_cash` is 0 AT target.
    """
    from types import SimpleNamespace as NS

    from app.services.funding_service import (cash_target_amount_ils, cash_target_fraction,
                                              spendable_cash)

    nav, cash = 21_207.78, 618.65

    # Today: the Grow table, and the book is already under it.
    assert abs(cash_target_fraction("Grow", None) - 0.03) < 1e-9
    assert spendable_cash(cash, nav, "Grow", None) == 0.0

    plan = NS(objective="Grow", cash_target_pct=7.5)
    assert abs(cash_target_fraction("Grow", plan) - 0.075) < 1e-9, (
        "the column is a PERCENTAGE and this returns a FRACTION; if these are "
        "ever the same number the /100 has been lost")
    assert abs(cash_target_amount_ils(nav, "Grow", plan) - 1590.58) < 0.5

    # Under target: nothing is spendable, and the gap is what the book must raise.
    assert spendable_cash(cash, nav, "Grow", plan) == 0.0
    assert abs(cash_target_amount_ils(nav, "Grow", plan) - cash - 971.93) < 1.0, (
        "the book is about 972 ILS short of a 7.5% reserve")

    # At target: still nothing is spendable by ROUTINE funding. That is the
    # reserve doing its job, not a bug.
    assert spendable_cash(1590.58, nav, "Grow", plan) == 0.0

    # A deliberate zero is honoured as zero, not fallen back to the objective.
    zero = NS(objective="Grow", cash_target_pct=0.0)
    assert cash_target_fraction("Grow", zero) == 0.0


def test_the_dead_plan_override_is_gone():
    """`plan.cash_floor_pct` read as configurable and could never fire.

    `Plan` has never had that column, so the `getattr` returned None on every
    call in production and the objective table was the only answer the function
    could give. Left in place it would now shadow the real column for anyone
    who set it.
    """
    import inspect

    from app.services import funding_service as f

    src = inspect.getsource(f.cash_floor_pct)
    assert 'getattr(plan, "cash_floor_pct"' not in src, (
        "the dead override is back; it names a column Plan does not have")
    assert 'getattr(plan, "cash_target_pct"' in src, src


def test_the_plan_table_has_somewhere_to_put_it():
    """The column, and the mirror that is what actually reaches production.

    `tests/test_startup_ddl_covers_migrations.py` already enforces the mirror in
    general; this asserts the specific column, because the two times that
    mirror was missed it cost an outage and then ten silent days.
    """
    from app.models.tables import Plan

    assert "cash_target_pct" in Plan.__table__.columns, (
        "Plan has nowhere to store the reserve")
    assert Plan.__table__.columns["cash_target_pct"].nullable, (
        "it must be nullable -- an existing plan has never been asked")
