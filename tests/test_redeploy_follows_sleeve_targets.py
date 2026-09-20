"""Cash above the soft target goes where the PLAN is short -- never into a
sleeve that is already over its target.

Reproduced 2026-09-20 on the live book's own numbers (value_ils from
/api/v1/portfolio, sleeves from /api/v1/plan/sleeves, class targets from
/api/v1/mix), with a ₪1,067/month standing order arriving:

    month 3, before this fix:   ₪623 -> QQQ   (sleeve target 9%, held ~11%)
                                ₪270 -> GOOGL
                                ₪270 -> SOXL  (sleeve target 42%, held ~38%)

Inside a class the card split its budget evenly and handed the smallest holding
the concentrated remainder, so the money went to the one sleeve that was OVER
target. It had no way to know better: it was never told the sleeve targets.

Also pinned here, both from the same card on the same day:
  * "the sale proceeds left your mix off-plan" -- false when the surplus is a
    deposit; the card makes no claim about where the cash came from now.
  * "against a 8% floor" -- the user's number is 7.5, a SOFT target; `:.0%`
    rounded it into a different number with a different word.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import funding_service as fund  # noqa: E402
from app.services.recommendations import _redeploy_cash_recs  # noqa: E402


class _Row:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


class _Plan:
    cash_target_pct = 7.5
    currency = "ILS"
    objective = "Grow"
    risk_tolerance = "High"


SLEEVES = {"SOXL": 0.42, "QQQ": 0.09}
# /api/v1/mix target_allocation on 2026-09-20 (sleeves + implicit bal_6040 core).
TARGET = {"Equities": 0.759, "Fixed Income": 0.166, "Cash": 0.075}


def _month3():
    """The live book after two accepted redeploys and three deposits."""
    rows = [
        _Row("CASH", 3063.12, "Cash"),
        _Row("QQQ", 2741.41 + 318.0),
        _Row("GOOGL", 5433.11 + 318.0),
        _Row("SOXL", 9302.25 + 318.0),
        _Row("AGG", 1124.69 + 422.5, "Fixed Income"),
        _Row("BND", 1885.65 + 422.5, "Fixed Income"),
    ]
    nav = sum(r.quantity for r in rows)
    return rows, nav


def _card(rows, nav, cash, sleeves=SLEEVES):
    out = _redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, _Plan(), "Grow",
                              0.45, cash, TARGET, None, sleeves)
    assert out, "cash is ~12% against a 7.5% target -- there must be a card"
    return out[0]


def test_no_money_goes_to_a_sleeve_already_over_its_target():
    rows, nav = _month3()
    qqq = next(r for r in rows if r.ticker == "QQQ").quantity
    assert qqq / nav > SLEEVES["QQQ"], "fixture: QQQ must be over its 9% target"
    legs = {x["ticker"]: x["amount_ils"] for x in _card(rows, nav, 3063.12)["apply"]["legs"]}
    assert "QQQ" not in legs, f"QQQ is over its sleeve target and was topped up: {legs}"


def test_the_sleeve_below_target_is_funded():
    rows, nav = _month3()
    legs = {x["ticker"]: x["amount_ils"] for x in _card(rows, nav, 3063.12)["apply"]["legs"]}
    assert legs.get("SOXL", 0) >= fund.MIN_TRADE_ILS, legs


def test_no_leg_pushes_a_name_past_its_own_target():
    """The claim-invariant, per name: re-measure after the buy."""
    rows, nav = _month3()
    card = _card(rows, nav, 3063.12)
    held = {r.ticker: r.quantity for r in rows}
    for leg in card["apply"]["legs"]:
        tk = leg["ticker"]
        if tk in SLEEVES:
            after = (held[tk] + leg["amount_ils"]) / nav
            assert after <= SLEEVES[tk] + 1e-6, f"{tk} ends at {after:.2%} > {SLEEVES[tk]:.0%}"


def test_the_surplus_is_placed_not_left_idle():
    rows, nav = _month3()
    card = _card(rows, nav, 3063.12)
    soft = fund.cash_soft_floor_ils(nav, "Grow", _Plan())
    assert card["meta"]["cash_after_ils"] - soft < fund.MIN_TRADE_ILS
    assert card["meta"]["cash_after_ils"] >= soft - 1.0, "never spends below the soft target"


def test_the_card_quotes_the_users_number_and_invents_no_history():
    rows, nav = _month3()
    card = _card(rows, nav, 3063.12)
    text = " ".join([card["title"], card["why"], card["action"], " ".join(card["how"])])
    assert "7.5%" in text, text
    assert "8% floor" not in text
    assert "sale proceeds" not in card["why"]
    assert "₪150" in text, "the hard floor is stated beside the soft target"


def test_when_every_name_is_on_target_the_rest_goes_in_at_plan_weights():
    """Nothing short anywhere: new money keeps the mix on plan rather than idling."""
    nav_ex_cash = 20000.0
    cash = 3000.0
    nav = nav_ex_cash + cash
    # Every name exactly at its target share of the book.
    rows = [_Row("CASH", cash, "Cash"),
            _Row("SOXL", 0.42 * nav), _Row("QQQ", 0.09 * nav),
            _Row("GOOGL", (0.759 - 0.51) * nav),
            _Row("BND", 0.166 * nav, "Fixed Income")]
    card = _card(rows, nav, cash)
    legs = {x["ticker"]: x["amount_ils"] for x in card["apply"]["legs"]}
    soft = fund.cash_soft_floor_ils(nav, "Grow", _Plan())
    assert sum(legs.values()) == pytest.approx(cash - soft, abs=fund.MIN_TRADE_ILS)
    # Largest target gets the largest share.
    assert max(legs, key=legs.get) == "SOXL", legs


def test_a_book_without_sleeves_keeps_its_old_behaviour():
    rows, nav = _month3()
    out = _redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, _Plan(), "Grow",
                              0.45, 3063.12, TARGET, None, None)
    assert out and out[0]["apply"]["legs"]
