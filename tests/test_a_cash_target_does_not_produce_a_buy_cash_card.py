"""PR 54 put Cash in the book target, and the drift card offered to buy it.

Live on production, `6044124`, minutes after PR 54 shipped. Two cards for one
ILS 972, and they instruct opposite things:

    "Raise ₪972 of cash"   liquidity, core scope
      action: Sell 4 BND (~₪972) to rebuild your cash floor. cheapest way to
              raise it: Fixed Income is the most overweight class...

    "Buy Cash"             allocation, book scope
      action: Buy about ₪971 of Cash to move toward 8%.
      how:    Buy Cash by ~₪971 — e.g. BIL

The first is right. The second is wrong twice over:

  * **Buying BIL spends cash.** It is a T-bill ETF -- a security. A card whose
    stated purpose is to raise the cash weight, offering a purchase that lowers
    it, is not a wording problem.
  * **It cannot do what it claims even in principle.** Its apply spec is
    `{"kind": "rebalance_to_objective", "asset_class": "Cash"}`, and
    `_rebalance_to` excludes cash rows from the classes it moves -- with a long
    comment saying why, ending *"Turning cash into securities is a purchase and
    belongs to the funded-buy path."* So pressing it reshuffles Equities and
    Fixed Income and leaves the cash weight exactly where it was. **That is the
    card-claim invariant: a card may not claim a portfolio change it does not
    produce.**

Cause: `build_recommendations` feeds the whole book target to
`AllocationEngine().compute` and builds one card per returned action. The target
never had a `Cash` key before PR 54, so the generic renderer had never been
asked to render one -- including its `CLASS_ETF["Cash"] = "BIL"` suggestion,
which had sat unreachable since it was written.

Raising cash already has an owner, and it is the better card: the liquidity path
names the ticker, sizes the lot, and explains the tax choice. Cash comes out of
the drift card, not out of the target.

A SECOND ONE, found in the sandbox while reproducing the first:
`_holding_verdict_recs` walked every row and rated the cash balance like a
security -- *"CASH screens well (fundamentals 80/100) and is only 1% of your
book — adding on weakness is reasonable"*. It has never reached production
only because Yahoo returns no fundamentals for the symbol, so the score is None
and the loop skips it BY ACCIDENT. An accident is not a guard.

Not financial advice.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

CASH_TARGET_PCT = 7.5


def _pos(ticker, market, qty, price, basis, asset_class="Equities"):
    return {"ticker": ticker, "market": market, "asset_class": asset_class, "depth": 2,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 18}


# The live book's shape: mostly equities, some bonds, and a cash balance well
# under a 7.5% reserve, so the target has a real Cash gap to render.
BOOK = [
    _pos("VTI", "NYSE", 40, 380.0, 300.0),
    _pos("BND", "NASDAQ", 30, 72.0, 70.0, asset_class="Fixed Income"),
]
CASH_ILS = 600.0


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio",
               json={"entity_name": "Personal", "positions": BOOK})
        r = c.put("/api/v1/plan",
                  json={"objective": "Grow", "cash_target_pct": CASH_TARGET_PCT})
        assert r.status_code < 400, f"{r.status_code} {r.text[:200]}"
        assert r.json().get("cash_target_pct") == CASH_TARGET_PCT
        c.post("/api/v1/portfolio/cash", json={"amount_ils": CASH_ILS})
        yield c


def _cards(client):
    return client.get("/api/v1/recommendations").json().get("recommendations", [])


def test_the_reserve_is_actually_under_target(client):
    """The fixture has to produce the gap, or the rest proves nothing."""
    mix = client.get("/api/v1/mix").json()
    assert "Cash" in mix["target_allocation"], (
        f"the book target has no Cash key, so there is no gap to render and "
        f"this file is asserting nothing: {mix['target_allocation']}")
    assert mix["current_allocation"].get("Cash", 0.0) < mix["target_allocation"]["Cash"], mix


def _cash_legs(card) -> list:
    """Every place a rebalance card can name a class, including after a merge.

    THE FIRST VERSION OF THIS TEST PASSED AGAINST THE BUG. It looked only at
    `apply.asset_class`, and `_reconcile` had merged four legs into one card
    whose top-level class was "Equities" -- with `Buy Cash (~₪4,271)` sitting
    inside `apply.legs` and `meta.merged_legs`. A guard that reads only the
    summary of a merge does not guard the merge.
    """
    apply = card.get("apply") or {}
    if apply.get("kind") != "rebalance_to_objective":
        return []
    found = []
    if apply.get("asset_class") == "Cash":
        found.append(f"apply.asset_class={apply.get('direction')}")
    for leg in apply.get("legs") or []:
        if (leg or {}).get("asset_class") == "Cash":
            found.append(f"legs: {leg.get('direction')} {leg.get('amount_ils')}")
    for leg in (card.get("meta") or {}).get("merged_legs") or []:
        if "cash" in str(leg).lower():
            found.append(f"merged_legs: {leg}")
    return found


def test_no_card_offers_to_buy_cash(client):
    """The drift renderer may not emit a Cash leg, merged or not.

    Asserted on the apply spec rather than on the title, because the title is
    rewritten when `_reconcile` merges legs -- and reading a card's direction
    back out of its prose is the exact mistake `contradictions.py` made.
    """
    offenders = [(c, legs) for c in _cards(client) if (legs := _cash_legs(c))]
    assert not offenders, (
        "a rebalance card names Cash. `_rebalance_to` excludes cash from the "
        "classes it moves, so pressing this changes the cash weight by nothing "
        "at all:\n  " + "\n  ".join(
            f"{c.get('title')!r}: {legs}" for c, legs in offenders))


def test_no_card_screens_your_cash_balance_as_a_security(client):
    """A cash balance is not a holding with a thesis.

    `_holding_verdict_recs` walked every row and rated CASH like a stock:
    "CASH screens well (fundamentals 80/100) ... adding on weakness is
    reasonable". It has not reached production only because Yahoo returns no
    fundamentals for the symbol, so the score is None and the loop skips it by
    accident. An accident is not a guard.
    """
    bad = [c for c in _cards(client)
           if c.get("dimension") == "holding"
           and "cash" in str(c.get("title", "")).lower()]
    assert not bad, (
        "the holding screener rated the cash balance:\n  "
        + "\n  ".join(f"{c.get('title')!r}: {c.get('action')!r}" for c in bad))


def test_no_card_tells_you_to_buy_a_security_to_raise_cash(client):
    """The half a reader would notice before the apply spec.

    `CLASS_ETF["Cash"]` is "BIL", a T-bill ETF. Buying it spends the balance the
    card exists to rebuild.
    """
    bad = []
    for c in _cards(client):
        text = " ".join([str(c.get("title", "")), str(c.get("action", "")),
                         " ".join(str(h) for h in (c.get("how") or []))]).lower()
        if "buy" in text and "cash" in text and "bil" in text:
            bad.append(c)
    assert not bad, (
        "a card proposes buying a security in order to raise cash:\n  "
        + "\n  ".join(f"{c.get('title')!r}: {c.get('how')}" for c in bad))


def test_raising_cash_still_has_a_card(client):
    """The fix must not delete the honest one along with the wrong one.

    The liquidity card is the one that owns this: it names a ticker, sizes the
    lot and explains the tax choice, which is everything the drift card could
    not do for a class that is not investable.
    """
    cards = _cards(client)
    liquidity = [c for c in cards
                 if c.get("dimension") == "liquidity"
                 or "raise" in str(c.get("title", "")).lower()]
    assert liquidity, (
        "the book is under its cash reserve and no card says so:\n  "
        + "\n  ".join(f"{c.get('dimension')}: {c.get('title')!r}" for c in cards))
