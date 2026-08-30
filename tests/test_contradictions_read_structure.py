"""Rule 2 stopped reading card titles, so it can finally see both legs.

Eran, 2026-08-29, on the deployed app: one card sold ~1,071 ILS of Equities
(*"Rebalance toward your target mix"*, book scope) while another bought ~3,139
of SOXL (sleeve scope). SOXL **is** Equities. Accepting both moved the mix the
wrong way, and `contradictions.py` rule 2 -- which exists for exactly this -- saw
neither leg.

Two holes, both live at once, and the pair went through both:

* the SELL leg was invisible because the check required
  ``str(r.get("title","")).lower().startswith("sell")``. `_reconcile` merges the
  rebalance legs and renames the survivor "Rebalance toward your target mix",
  which does not start with "sell". Worse, the merge left the card's remaining
  structure describing only ONE of its legs: `apply.asset_class` is whichever
  leg sorted first, and the others survived as prose in `meta.merged_legs`.
* the BUY leg was invisible because of ``if not cls: continue``. A sleeve top-up
  is ticker-scoped and carries no `asset_class` at all, so the card was skipped
  before its direction was ever considered.

The fix is structural on both sides: the generator records `direction`, the
merge writes `apply.legs[]` as data, and `_class_moves` derives (class,
direction) from legs -> apply fields -> `allocation_mix.classify` on the tickers
the card names. No branch of it reads a title.

These tests assert on the reported contradiction, never on card text. String
assertions are what let the original four defects ship, and `STATUS.md` records
that once already.
"""
import pytest

from app.services.contradictions import _class_moves, check

NAV = 100_000.0


def _rebalance_card(*, merged: bool) -> dict:
    """The book-scoped rebalance, before and after `_reconcile` merges its legs.

    `merged=True` is the shape that actually reached the screen: the title no
    longer names a direction and `asset_class` names only the first leg.
    """
    if not merged:
        return {"id": "rebal_equities", "title": "Sell Equities",
                "est_amount": 1071.0,
                "apply": {"kind": "rebalance_to_objective",
                          "asset_class": "Equities", "direction": "sell"}}
    return {
        "id": "rebal_merged", "title": "Rebalance toward your target mix",
        "est_amount": 1071.0,
        "meta": {"merged_legs": ["Sell Equities", "Buy Commodities"]},
        "apply": {"kind": "rebalance_to_objective",
                  "asset_class": "Equities", "direction": "sell",
                  "legs": [{"asset_class": "Equities", "direction": "sell",
                            "amount_ils": 1071.0},
                           {"asset_class": "Commodities", "direction": "buy",
                            "amount_ils": 940.0}]},
    }


def _soxl_topup() -> dict:
    """The sleeve top-up. Ticker-scoped, and it carries no asset_class at all."""
    return {"id": "fund_soxl", "title": "Top up your SOXL sleeve",
            "est_amount": 3139.0,
            "apply": {"kind": "fund_sleeve", "strategy_id": "btm_trend_soxl",
                      "legs": [{"ticker": "SOXL", "amount_ils": 3139.0}]}}


def _check(recs):
    return check(recs, sleeve_targets={}, nav=NAV, weights={}, book_target=None)


def test_the_live_pair_is_caught():
    """The whole point. Both cards survive; both are told about the other."""
    recs = [_rebalance_card(merged=True), _soxl_topup()]
    surviving, found = _check(recs)

    clash = [c for c in found if c["kind"] == "buys_and_sells_one_class"]
    assert clash, (
        "one card sells Equities and another buys SOXL, which is Equities, and "
        "nothing was reported")
    assert clash[0]["asset_class"] == "Equities"
    assert {clash[0]["card"], clash[0]["other"]} == {"rebal_merged", "fund_soxl"}
    # Reported, not suppressed: both are defensible on their own and the user
    # decides. What is not acceptable is being told nothing.
    assert clash[0]["suppressed"] is False
    assert len(surviving) == 2
    for r in surviving:
        assert r.get("notes"), f"{r['id']} was not told the other card exists"


def test_a_sleeve_topup_with_no_asset_class_is_still_classified():
    """`if not cls: continue` is what made the buy leg invisible."""
    moves = _class_moves(_soxl_topup())
    assert moves == {("Equities", "buy")}, (
        f"SOXL resolved to {moves} -- a card with no asset_class is not a card "
        "with no asset class")


def test_a_merged_rebalance_reports_every_leg_not_just_the_first():
    moves = _class_moves(_rebalance_card(merged=True))
    assert moves == {("Equities", "sell"), ("Commodities", "buy")}, (
        f"got {moves} -- the merged card describes only its first leg")


def test_the_direction_does_not_come_from_the_title():
    """Rename the card to anything at all; the answer must not move."""
    baseline = _class_moves(_rebalance_card(merged=True))
    for title in ("Rebalance toward your target mix", "Buy Equities",
                  "sell everything", "", "Have a nice day"):
        card = _rebalance_card(merged=True)
        card["title"] = title
        assert _class_moves(card) == baseline, (
            f"the answer changed when the title became {title!r}")


def test_an_unmerged_sell_leg_is_still_caught():
    """The pre-merge shape must keep working -- this is not a merge-only fix."""
    _, found = _check([_rebalance_card(merged=False), _soxl_topup()])
    assert [c for c in found if c["kind"] == "buys_and_sells_one_class"]


def test_a_card_does_not_contradict_itself():
    """A merged rebalance sells one class and buys another in ONE decision.

    Flagging that would turn the fix into a permanent false alarm on every
    rebalance, which is a fast way to get a real warning ignored.
    """
    _, found = _check([_rebalance_card(merged=True)])
    assert not [c for c in found if c["kind"] == "buys_and_sells_one_class"]


def test_two_cards_moving_different_classes_are_left_alone():
    sell_equities = {"id": "s1", "title": "Sell Equities", "est_amount": 500.0,
                     "apply": {"kind": "rebalance_to_objective",
                               "asset_class": "Equities", "direction": "sell"}}
    buy_bonds = {"id": "b1", "title": "Top up your bond sleeve", "est_amount": 500.0,
                 "apply": {"kind": "fund_sleeve",
                           "legs": [{"ticker": "BND", "amount_ils": 500.0}]}}
    surviving, found = _check([sell_equities, buy_bonds])
    assert not [c for c in found if c["kind"] == "buys_and_sells_one_class"]
    assert all(not r.get("notes") for r in surviving)


def test_an_advisory_card_cannot_contradict_anything():
    """Accepting it does nothing, so it cannot argue with a card that acts."""
    advisory = {"id": "a1", "title": "Sell Equities, maybe",
                "apply": {"kind": "none", "asset_class": "Equities"}}
    assert _class_moves(advisory) == set()


@pytest.mark.asyncio
async def test_the_merged_card_carries_its_legs_as_data(own_session):
    """End to end: whatever `_reconcile` produces must describe itself.

    Asserted on the real merge rather than a hand-built dict, because the shape
    that reached the screen is the one the merge produces -- and the defect was
    that it produced prose where the consumer needed data.
    """
    from app.services.recommendations import _reconcile

    legs = [
        {"id": "rebal_a", "title": "Sell Equities", "est_amount": 1071.0,
         "severity": "MEDIUM",
         "apply": {"kind": "rebalance_to_objective", "asset_class": "Equities",
                   "direction": "sell"}},
        {"id": "rebal_b", "title": "Buy Commodities", "est_amount": 940.0,
         "severity": "MEDIUM",
         "apply": {"kind": "rebalance_to_objective", "asset_class": "Commodities",
                   "direction": "buy"}},
    ]
    out = _reconcile(legs)
    merged = [r for r in out
              if (r.get("apply") or {}).get("kind") == "rebalance_to_objective"]
    assert len(merged) == 1, "the two legs should merge into one card"
    card = merged[0]
    assert card["apply"].get("legs"), (
        "the merged card lost its legs -- they survive only as prose in "
        "meta.merged_legs, which is what rule 2 could not read")
    assert _class_moves(card) == {("Equities", "sell"), ("Commodities", "buy")}
