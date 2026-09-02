"""PR 36 -- the card that owns two boxes forgot which numbers you put in them.

Eran, 2026-09-02: *"I want these fields (beat the benchmark & without exceeding)
to be the default of what I have approved."*

He had accepted **12 / 49** and the card kept opening on a measurement. Both
defaults are seeded in `_seedTarget`: the ceiling from
`performance(range=MAX).max_drawdown_pct`, the target from
`max(0, ceil(excess_cagr))`. That was the right design while nothing remembered
an answer -- a made-up default is the card guessing on the user's behalf, which
is what this card exists to stop.

But an ACCEPTED target is neither a guess nor a measurement. It is the question
he decided to ask and then acted on, and **it has been recorded on every apply
since Phase A**: `tgtAccept` sends `context.excess_pct` and
`context.ceiling_pct`, and `_record` writes that verbatim onto the
`PlanApplication` row. Nothing had ever read it back. So this PR adds a read
path and no new storage.

Two rules the read path has to get right, and both are about not putting words
in the user's mouth:

  * **An undo withdraws the acceptance.** Undo restores the sizes the apply
    replaced, so the target that produced them stops describing the book.
    Seeding from it afterwards would put a decision he reversed back in the box.
  * **Half a seed is worse than none.** A row that lost one of the two figures
    cannot seed a pair, and one remembered number beside one measured one, with
    nothing saying which is which, is the confusion this card is for.

The measured pair is still fetched, still shown, and one tap away. Silently
replacing a remembered decision with a measurement is the mirror of the bug.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402


class _Row:
    """A PlanApplication as `last_accepted` reads it."""

    def __init__(self, action, context, at=None, ident="app-1", allocated=75.0):
        self.action = action
        self.context = context
        self.created_at = at or datetime.now(timezone.utc)
        self.id = ident
        self.allocated_pct_after = allocated


class _Session:
    """Returns the newest row, which is all `last_accepted` asks for."""

    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda r: r.created_at, reverse=True)

    async def execute(self, _q):
        rows = self._rows

        class _R:
            def scalars(self_inner):
                class _S:
                    def first(self_s):
                        return rows[0] if rows else None
                return _S()
        return _R()


class _User:
    email = "eran@example.com"


ACCEPTED = {"excess_pct": 12.0, "ceiling_pct": 49.0, "outcome": "REACHED",
            "solved_total_sleeve_pct": 75.0}


async def _last(rows):
    from app.services.target_apply import last_accepted
    return await last_accepted(_Session(rows), _User())


# --------------------------------------------------------------------------- #
# the thing he asked for
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_accepted_pair_is_readable():
    """12 and 49, back out of the row the apply already wrote."""
    got = await _last([_Row("apply", ACCEPTED)])
    assert got is not None, "the accepted target is recorded and still unreadable"
    assert got["excess_pct"] == 12.0
    assert got["ceiling_pct"] == 49.0
    assert got["accepted_at"]
    assert got["outcome"] == "REACHED"


@pytest.mark.asyncio
async def test_nothing_accepted_yet_means_nothing_is_remembered():
    """The measured seed is the correct behaviour when there is no decision."""
    assert await _last([]) is None


# --------------------------------------------------------------------------- #
# not putting words in the user's mouth
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_undo_withdraws_the_acceptance():
    """Undo restores the sizes the apply replaced. The target goes with them."""
    now = datetime.now(timezone.utc)
    rows = [_Row("apply", ACCEPTED, at=now - timedelta(minutes=5)),
            _Row("undo", {"of": "app-1"}, at=now, ident="app-2")]
    assert await _last(rows) is None, (
        "a target the user reversed was seeded back into the card")


@pytest.mark.asyncio
async def test_an_apply_after_an_undo_stands_again():
    """The newest row decides, in both directions."""
    now = datetime.now(timezone.utc)
    rows = [_Row("apply", {"excess_pct": 8.0, "ceiling_pct": 30.0},
                 at=now - timedelta(hours=2)),
            _Row("undo", {"of": "x"}, at=now - timedelta(hours=1), ident="u"),
            _Row("apply", ACCEPTED, at=now, ident="app-3")]
    got = await _last(rows)
    assert got is not None and got["excess_pct"] == 12.0


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [
    {},                                              # an apply with no context
    {"excess_pct": 12.0},                            # half of it
    {"ceiling_pct": 49.0},                           # the other half
    {"excess_pct": None, "ceiling_pct": 49.0},
    {"excess_pct": "not a number", "ceiling_pct": 49.0},
])
async def test_half_a_seed_is_worse_than_none(ctx):
    """One remembered number beside one measured one says nothing about either."""
    assert await _last([_Row("apply", ctx)]) is None


@pytest.mark.asyncio
async def test_an_undo_of_an_undo_is_still_an_undo():
    """Only an `apply` can be the standing decision."""
    now = datetime.now(timezone.utc)
    rows = [_Row("apply", ACCEPTED, at=now - timedelta(minutes=9)),
            _Row("undo", {}, at=now - timedelta(minutes=5), ident="u1"),
            _Row("undo", {}, at=now, ident="u2")]
    assert await _last(rows) is None


# --------------------------------------------------------------------------- #
# through the API
# --------------------------------------------------------------------------- #
def test_the_card_can_ask_the_server_what_was_accepted():
    """The endpoint exists, is read-only, and says null rather than 404."""
    import logging

    from fastapi.testclient import TestClient

    import app.main as m

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            r = c.get("/api/v1/plan/target/accepted")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert "accepted" in body, "the card cannot tell 'none' from 'broken'"
    finally:
        logging.disable(logging.NOTSET)


# --------------------------------------------------------------------------- #
# issue 7: name the unmeasured share instead of leaving a bare percentage
# --------------------------------------------------------------------------- #
def test_the_assumptions_headline_names_what_is_not_measured():
    """"78.8% of the book measured" reads as a coverage hole, so name the rest.

    It was the first thing Eran asked about -- "why only 78.8%? why not 100%?"
    A percentage with nothing on the other side of it invites the reader to
    supply the missing half himself.
    """
    from app.services.measured_assumptions import (
        BASIS_MIXED, RETURN_BASIS_OOS, _headline,
    )

    text = _headline({"basis": BASIS_MIXED, "measured_pct_of_book": 78.8},
                     RETURN_BASIS_OOS)
    assert "78.8%" in text
    assert "21.2%" in text, f"the unmeasured share is not named: {text}"
    assert "core" in text and "cash" in text
    assert "sleeves" in text


def test_the_headline_does_not_claim_the_core_has_no_strategy():
    """Eran, on the first draft of that line: *"that's not true.... the sleeves
    have their strategy and the core has its own strategy -- the portfolio is
    being managed by two different strategies."*

    He is right, and this app's own code agrees with him. `core_search` ranks
    fourteen candidate cores on measured return, volatility and drawdown through
    `blend.core_spec` + `blend.measure_blend`. His core is `bal_6040`: a basket
    plus a rebalance discipline, sized at 27% of the book.

    So the reason his core is unmeasured is that `book_assumptions` never asks
    for the core row -- a gap in this app. The headline has to say that, and must
    never explain the gap away as a property of the core.
    """
    from app.services.measured_assumptions import (
        BASIS_MIXED, RETURN_BASIS_OOS, _headline,
    )

    text = _headline({"basis": BASIS_MIXED, "measured_pct_of_book": 78.8},
                     RETURN_BASIS_OOS)
    lower = text.lower()
    assert "no strategy" not in lower, (
        f"the core is a strategy and the headline denies it: {text}")
    # ...and it owns the gap rather than presenting it as the intended design
    assert ("not yet" in lower) or ("gap" in lower), (
        f"the headline does not say the app is what falls short here: {text}")


def test_a_fully_static_book_still_says_so_plainly():
    """The other branch is untouched: no strategy, no percentage games."""
    from app.services.measured_assumptions import (
        BASIS_STATIC, RETURN_BASIS_OOS, _headline,
    )

    text = _headline({"basis": BASIS_STATIC, "measured_pct_of_book": 0.0},
                     RETURN_BASIS_OOS)
    assert "Nothing on this book is measured yet" in text
