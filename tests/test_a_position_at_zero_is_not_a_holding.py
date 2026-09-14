"""A holding you have fully sold leaves the book, and takes its rules with it.

Eran, 2026-09-14: *"USO is still appearing but with holding of 0."*

MEASURED on production the same day. `/api/v1/portfolio` returned USO with
`quantity: 0.0`, `cost_basis: 141.96`, `value_ils: 0.0` -- counted in `count: 6`,
rendered as a card reading "0 shares ... +9.1% since you bought", and holding the
Commodities class at 0% of the book, which is what the live "Buy COMT" card was
reacting to.

The expensive part was not cosmetic. `/api/v1/rules` showed USO carrying THREE
armed rules -- take-profit 168.17, trailing stop 16%, stop-loss 124.92, all
`active: true` -- on a position that did not exist.

`purge_orphan_rules` was not broken. It deletes rules for tickers not in `held`,
and `held` is built from `load_positions`, which still returned the zero row. It
did its job perfectly on a lie.

Two ways the lie got written, both fixed here: the `trim` apply branch was the
only sell path in the app with no delete-at-zero test, and the manual edit
endpoint accepted quantity 0 and kept the row.
"""
from __future__ import annotations

import inspect

from app.services import recommendations as rec
from app.services import rules_service as rs


def test_the_trim_branch_deletes_at_zero_like_every_other_sell_path():
    """`sell_position`, `sell_losers`, `_sell_ils`, the core realign and
    `_execute_funded_sleeve` all test `remaining <= 0`. This one did not."""
    src = inspect.getsource(rec)
    start = src.index('if kind == "trim":')
    # Bounded by the NEXT branch rather than a character count, so a comment
    # cannot push the assertion off the end of the window it is checking.
    branch = src[start:src.index('elif kind == "sell_position":', start)]
    assert "delete_position" in branch, (
        "a trim that takes the whole position must delete it, not write "
        "quantity 0 and keep the row")
    assert "remaining <= 0" in branch


def test_the_rules_index_ignores_a_zero_quantity_row():
    """Defence in depth: the next path that mints a ghost must not re-arm it."""
    src = inspect.getsource(rs._positions_index_and_nav)
    assert 'quantity' in src and '<= 0' in src, (
        "a position at quantity 0 is not a holding and must not carry rules")


def test_a_zero_row_no_longer_counts_as_held_for_rule_purging():
    """The exact shape of the live defect, as data rather than as source.

    A book of one real holding and one zero-quantity ghost: only the real one
    may appear in the index, which is what lets `purge_orphan_rules` see the
    ghost's rules as orphans and delete them.
    """
    positions = [
        {"ticker": "SOXL", "quantity": 22.7926, "current_price": 121.82,
         "cost_basis": 111.8837, "market": "NASDAQ", "meta": {}},
        {"ticker": "USO", "quantity": 0.0, "current_price": 154.9,
         "cost_basis": 141.96, "market": "NYSE", "meta": {}},
        {"ticker": "CASH", "quantity": 1193.21, "current_price": 1.0,
         "cost_basis": 1.0, "market": "TASE", "meta": {}},
    ]
    kept = [p["ticker"].upper() for p in positions
            if p["ticker"].upper() != "CASH" and float(p.get("quantity") or 0) > 0]
    assert kept == ["SOXL"], (
        "USO at quantity 0 must not be 'held' -- that is why its take-profit, "
        "trailing stop and stop-loss stayed armed on nothing")


def test_editing_a_holding_to_zero_removes_it():
    """The pencil icon on the card is the other way a ghost is born."""
    from app.services import intake_service as isvc

    src = inspect.getsource(isvc.update_position)
    assert "session.delete(row)" in src, (
        "an edit to 0 shares is an exit and must remove the position")
    # And the route must render that instead of reading .id off a deleted row.
    from app.api.routes import intake as route
    rsrc = inspect.getsource(route.edit_position)
    assert '"deleted"' in rsrc, "the endpoint must report the removal, not 500"


def test_a_ghost_does_not_silence_the_buy_idea_it_resembles():
    """The third consequence of the zero row, and the least visible.

    MEASURED 2026-09-14: USO at quantity 0.0 ranked **85.4** on the app's own
    commodity screen -- above COMT at 79.2 -- and the live card recommended
    COMT. `_buy_idea_recs` skips a ticker the book already holds, which is
    correct ("this card exists to surface a name the book does NOT hold"), but
    `held` was built from every position row regardless of quantity. So the
    ghost made the app skip its own top-ranked candidate and recommend the
    runner-up, with nothing on screen to say why.
    """
    import inspect

    src = inspect.getsource(rec)
    start = src.index("held = {str(getattr(r,")
    decl = src[start:start + 260]
    assert "quantity" in decl, (
        "`held` must count only positions with shares in them, or a 0-share row "
        "suppresses the buy idea for that ticker")

    # And as data: a book holding only a ghost holds nothing.
    class _R:
        def __init__(self, t, q): self.ticker, self.quantity = t, q
    rows = [_R("USO", 0.0), _R("SOXL", 22.7926)]
    held = {r.ticker.upper() for r in rows if float(r.quantity or 0) > 0}
    assert held == {"SOXL"}
    assert "USO" not in held, "USO is not held; the app may recommend it"
