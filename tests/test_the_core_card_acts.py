"""PR 39 -- the core card stops giving advice and does the thing.

Eran, 2026-09-02, on the three cards PR 38 shipped: *"I'm missing the most
important thing in these cards --> guidance is not enough... I want these cards
to act on my behalf (as other cards can do it)."*

PR 38 shipped them as guidance because this app cannot fund a 3,392 ILS purchase
from a 619 ILS cash balance. That reasoning was half right and the conclusion was
wrong: **the money is already in the book**, sitting in the wrong places.

    V         954 ILS   in the core region, not in the core's basket
    BND       576 ILS   above the core's own 40% leg
    sleeves 1,268 ILS   above the sleeve sizes he set (79% held vs 73%)

Bringing the core back to Classic 60/40 is a REBALANCE, not a purchase, and this
app rebalances. Measured on his live book (NAV 21,092, core 27%), the whole plan
still lands 565 ILS short once CGT and the cash floor are honoured -- and the
card says so rather than quietly buying less than it promised.

THE ONE RULE THIS FILE EXISTS TO HOLD: the card and the button call the SAME
sizing function. `_rebalance_to` is the cautionary tale in this repo -- the card
read one target, Accept applied another, the screen said "sell 4,324" and the
button sold 12,139.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

NAV = 21092.37
SIXTY_FORTY = {"VTI": 0.6, "BND": 0.4}
# His core region and cash, FX-normalised, sleeve-claimed tickers excluded.
HELD = {"BND": 0.1353 * NAV, "V": 0.0453 * NAV}
CASH = 0.0293 * NAV
FLOOR = 0.02 * NAV
SLEEVE_OVER = (0.7901 - 0.73) * NAV


def _plan(**kw):
    """Imported lazily so this file still COLLECTS against the previous commit."""
    from app.services.core_realign import plan_realign

    args = dict(basket=SIXTY_FORTY, core_pct=0.27, nav=NAV, held=dict(HELD),
                cash_ils=CASH, cash_floor_ils=FLOOR, sleeve_over_ils=SLEEVE_OVER,
                cgt_rate=0.25, gain_frac={"V": 0.20, "BND": 0.0, "__sleeves__": 0.30})
    args.update(kw)
    return plan_realign(**args)


def _legs(plan, action):
    return [x for x in plan["legs"] if x["action"] == action]


# --------------------------------------------------------------------------- #
# the plan on his actual book
# --------------------------------------------------------------------------- #
def test_the_plan_sells_what_the_basket_does_not_name():
    sells = _legs(_plan(), "sell")
    v = [s for s in sells if s["ticker"] == "V"]
    assert v, "V is in the core region and not in the basket, and nothing sells it"
    assert v[0]["full_exit"] is True, (
        "a half-exit leaves the core still holding something its strategy does not")
    assert round(v[0]["ils"]) == 955


def test_the_plan_trims_the_leg_that_is_over_and_not_past_it():
    sells = _legs(_plan(), "sell")
    bnd = [s for s in sells if s["ticker"] == "BND"]
    assert bnd and bnd[0]["full_exit"] is False
    assert round(bnd[0]["ils"]) == 576, bnd
    # what is left must be the leg itself, not zero
    assert round(_plan()["want_ils"]["BND"]) == 2278


def test_the_plan_buys_the_leg_that_is_missing():
    buys = _legs(_plan(), "buy")
    assert [b["ticker"] for b in buys] == ["VTI"]
    assert round(buys[0]["wanted_ils"]) == 3417


def test_it_reaches_for_the_sleeves_only_after_the_core_region_is_spent():
    plan = _plan()
    order = [x["action"] for x in plan["legs"]]
    assert order.index("trim_sleeves") > max(
        i for i, a in enumerate(order) if a == "sell"), (
        "the sleeves were reached for before the core's own money")
    assert order.index("trim_sleeves") < order.index("buy"), (
        "the buy runs before the money that funds it is raised")


def test_it_never_trims_a_sleeve_below_the_size_he_set():
    """The sleeve sizes are a decision made on the Plan tab.

    This action may reclaim what is ABOVE that decision. It may not overrule it,
    however short the buy still is.
    """
    plan = _plan()
    trim = _legs(plan, "trim_sleeves")[0]
    assert trim["ils"] <= round(SLEEVE_OVER, 2) + 0.01, (
        f"took {trim['ils']} from the sleeves, which hold only "
        f"{SLEEVE_OVER:.2f} above their own target")


def test_the_shortfall_is_named_rather_than_quietly_buying_less():
    """Even with everything, CGT and the cash floor leave it ~565 short."""
    plan = _plan()
    assert plan["shortfall_ils"] > 100, plan
    assert round(plan["shortfall_ils"]) == 565, plan["shortfall_ils"]
    assert plan["reason"] == "PARTIALLY_FUNDED"
    assert "unfunded" in plan["detail"]


def test_the_buy_is_scaled_to_what_was_actually_raised():
    plan = _plan()
    bought = sum(b["ils"] for b in _legs(plan, "buy"))
    assert bought <= plan["sells_ils"] + plan["sleeve_trim_ils"] + plan["cash_used_ils"]
    assert round(bought) == 2852, bought


# --------------------------------------------------------------------------- #
# the limits it will not cross
# --------------------------------------------------------------------------- #
def test_cash_below_the_floor_is_never_spent():
    plan = _plan(cash_ils=FLOOR)          # nothing above the floor
    assert plan["cash_used_ils"] == 0.0


def test_a_book_with_no_spare_cash_still_produces_a_plan():
    plan = _plan(cash_ils=0.0)
    assert plan["ok"] is True and _legs(plan, "buy")


def test_the_sleeves_can_be_left_alone_entirely():
    """The option Eran was offered and declined stays reachable in code."""
    plan = _plan(allow_sleeve_trim=False)
    assert _legs(plan, "trim_sleeves") == []
    assert plan["shortfall_ils"] > _plan()["shortfall_ils"], (
        "not touching the sleeves must leave MORE unfunded, not less")


def test_a_core_already_on_its_basket_does_nothing_and_says_so():
    plan = _plan(held={"VTI": 0.162 * NAV, "BND": 0.108 * NAV})
    assert plan["ok"] is False
    assert plan["reason"] == "ALREADY_ON_BASKET"
    assert plan["legs"] == []


def test_rounding_is_not_a_trade():
    from app.services.core_realign import MIN_LEG_ILS

    plan = _plan(held={"VTI": 0.162 * NAV - 5.0, "BND": 0.108 * NAV})
    assert plan["ok"] is False, f"a {MIN_LEG_ILS} ILS floor let a 5 ILS gap through"


def test_a_core_with_no_basket_refuses_rather_than_inventing_one():
    plan = _plan(basket={})
    assert plan["ok"] is False and plan["reason"] == "CORE_HAS_NO_BASKET"


def test_tax_is_charged_on_the_gain_and_not_on_the_proceeds():
    """A plan taxing the whole sale would over-state every trim's cost."""
    no_gain = _plan(gain_frac={})
    some_gain = _plan(gain_frac={"V": 1.0, "BND": 1.0, "__sleeves__": 1.0})
    assert no_gain["tax_ils"] == 0.0
    assert some_gain["tax_ils"] > no_gain["tax_ils"]
    assert some_gain["shortfall_ils"] > no_gain["shortfall_ils"], (
        "tax has to reduce what the sales can fund")


# --------------------------------------------------------------------------- #
# the card may not promise a leg the button will not run
# --------------------------------------------------------------------------- #
def test_every_line_the_card_shows_is_a_leg_the_button_runs():
    from app.services.core_realign import describe

    plan = _plan()
    lines = describe(plan)
    for leg in plan["legs"]:
        token = leg["ticker"] or "sleeves"
        assert any(token.lower() in ln.lower() for ln in lines), (
            f"the plan runs a {leg['action']} on {token} and no line says so")
    assert any("tax" in ln.lower() for ln in lines), "the tax is spent and never shown"
    assert any("unfunded" in ln.lower() for ln in lines)


def test_the_card_and_the_button_share_one_sizing_function():
    """`_rebalance_to`'s lesson, asserted rather than remembered.

    The card read one target and Accept applied another: the screen said
    "sell 4,324" and the button sold 12,139.
    """
    import inspect

    from app.services import recommendations as R

    card = inspect.getsource(R.build_recommendations)
    assert "_core_plan(session, user, rows, nav, objective, btgt)" in card
    ex = inspect.getsource(R._apply_core_to_basket)
    assert "_core_plan(session, user, rows, nav, objective, btgt)" in ex, (
        "the executor sizes the trade itself instead of reading the card's plan")


def test_the_card_declares_an_apply_kind_the_app_can_actually_run():
    from app.services.recommendations import _ACTIONABLE_KINDS

    assert "core_to_basket" in _ACTIONABLE_KINDS, (
        "the card offers a button the apply path does not implement")


def test_the_executor_prices_the_buys_before_it_sells_anything():
    """One timed-out quote must not leave the whole core sitting in cash.

    `_apply_buy_funded` sells first and holds the proceeds as cash if the buy
    cannot be priced. Survivable for one swap; here it would mean selling three
    positions because a quote blinked.
    """
    import inspect

    from app.services import recommendations as R

    src = inspect.getsource(R._apply_core_to_basket)
    price_at = src.index("guarded_quote")
    sell_at = src.index("_sell_ils(p")
    assert price_at < sell_at, "the executor sells before it knows it can buy"
    assert "aborted" in src, "there is no refuse-everything path"


def test_the_sleeve_trim_is_spread_across_the_sleeve_holdings():
    """Taking it all from one ticker silently re-weights the sleeves."""
    import inspect

    from app.services import recommendations as R

    src = inspect.getsource(R._apply_core_to_basket)
    block = src.split("trim_sleeves")[1]
    assert "v / total" in block, "the sleeve trim is not pro-rata"


def test_the_executor_says_no_brokerage_order_was_placed():
    """This updates the tracked book. Nothing here reaches a broker."""
    import inspect

    from app.services import recommendations as R

    assert "no brokerage order was placed" in inspect.getsource(R._apply_core_to_basket)
