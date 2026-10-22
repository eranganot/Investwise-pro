"""PR 38 -- "done" was a claim about the book, and nothing ever asked the book.

Eran, 2026-09-02: *"if I'm not holding VTI as part of my portfolio --> I expect
to get a recommendation card to purchase it."*

THE CARD EXISTED. "Buy Equities", MEDIUM, 1,215 ILS, hidden since 2026-08-21
because he pressed Done. Measured on 2026-09-02, twelve days later, the equities
gap was 1,194 ILS -- the same instruction to within 2%.

And PR 34's rule is why it stayed hidden, behaving exactly as designed: a muted
card returns when its own number MOVES. For a card you IGNORED that is right --
you looked, you chose not to act, and only a changed situation earns your
attention again.

FOR A CARD YOU MARKED DONE THE SIGNAL IS INVERTED. "Done" is a claim about the
book, and the book can be asked. If the same instruction still stands, unchanged,
after the trade would have settled, the number NOT MOVING is the evidence the
action was never taken. The app took the user's word for it and never checked --
and nothing else in this file trusts a claim it can measure.

THE SECOND HALF is that even when that card fires it says the wrong thing.
"Buy Equities, about 1,215 ILS" is an ASSET CLASS. On this book the equities
target is 89.2% -- 73% from two leveraged sleeves plus 16.2% from the core --
so buying more TQQQ satisfies it completely and leaves the core still holding no
VTI. A class is not an instruction.

The core is the one place the app knows the instrument, because a core IS a
basket: bal_6040 is 60% VTI / 40% BND, and the book's target for each leg is
core_pct x weight x NAV. On his book, today:

    VTI   target 3,417 ILS   held     0   ->  buy  ~3,417
    BND   target 2,278 ILS   held 2,854   ->  trim ~  576
    V     target     0 ILS   held   955   ->  not in your core's basket

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone  # noqa: E402


def _moved(entry, card, done=False):
    """Imported lazily so this file still COLLECTS against the previous commit."""
    from app.services.recommendations import moved_on
    return moved_on(entry, card, done=done)


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# The real card, verbatim from /api/v1/recommendations on 2026-09-02.
THE_HIDDEN_CARD = {"id": "rec_547b55", "title": "Buy Equities",
                   "severity": "MEDIUM", "est_amount": 1194.0}
THE_MARK = {"at": _ago(12), "amount": 1215.09, "severity": "MEDIUM"}


# --------------------------------------------------------------------------- #
# the card that started this
# --------------------------------------------------------------------------- #
def test_the_buy_equities_card_comes_back():
    """Twelve days, the same 1,200 ILS ask, and it was marked done."""
    why = _moved(THE_MARK, THE_HIDDEN_CARD, done=True)
    assert why, "the card he marked done, and never did, is still hidden"
    assert "did not happen" in why
    assert "1,194" in why, f"the card must quote the ask that still stands: {why}"


def test_the_reason_names_the_day_he_filed_it():
    """"It came back" is not enough; he has to be able to place it."""
    why = _moved(THE_MARK, THE_HIDDEN_CARD, done=True)
    day = (datetime.now(timezone.utc) - timedelta(days=12)).date().isoformat()
    assert day in why, f"the date he pressed done is not in the reason: {why}"


# --------------------------------------------------------------------------- #
# ignored is not done, and the difference is the whole rule
# --------------------------------------------------------------------------- #
def test_an_ignored_card_with_an_unchanged_number_stays_hidden():
    """You looked and chose not to act. An unchanged book confirms your choice.

    This is PR 34's behaviour and it is still correct. Firing here would be the
    nagging the mute exists to prevent.
    """
    assert _moved(THE_MARK, THE_HIDDEN_CARD, done=False) is None


def test_the_default_is_the_old_behaviour():
    """A caller that has not been taught the difference gets PR 34 exactly."""
    assert _moved(THE_MARK, THE_HIDDEN_CARD) is None


# --------------------------------------------------------------------------- #
# narrow on purpose: the failure mode of this rule is nagging
# --------------------------------------------------------------------------- #
def test_a_done_inside_the_settle_window_is_believed():
    """An order placed on Friday settles on Tuesday.

    A card reappearing the same afternoon to say "this did not happen" would be
    wrong most of the time, and rude the rest.
    """
    fresh = {"at": _ago(1), "amount": 1215.09, "severity": "MEDIUM"}
    assert _moved(fresh, THE_HIDDEN_CARD, done=True) is None


def test_the_settle_window_is_a_named_constant_not_a_literal():
    from app.services.recommendations import _DONE_SETTLE_DAYS

    assert 1 <= _DONE_SETTLE_DAYS <= 7
    just_inside = {"at": _ago(_DONE_SETTLE_DAYS - 0.5), "amount": 1215.09}
    just_outside = {"at": _ago(_DONE_SETTLE_DAYS + 0.5), "amount": 1215.09}
    assert _moved(just_inside, THE_HIDDEN_CARD, done=True) is None
    assert _moved(just_outside, THE_HIDDEN_CARD, done=True)


def test_a_small_ask_that_stays_small_proves_nothing():
    """40 ILS that is still 40 ILS has not shown you failed to act on it."""
    mark = {"at": _ago(30), "amount": 40.0}
    assert _moved(mark, {"est_amount": 42.0}, done=True) is None


def test_a_mark_with_no_amount_has_nothing_to_contradict():
    """An old mark carries no situation, so it keeps its TTL and nothing else."""
    assert _moved({"at": _ago(30)}, THE_HIDDEN_CARD, done=True) is None
    assert _moved("2026-08-21T08:51:55+00:00", THE_HIDDEN_CARD, done=True) is None


def test_a_card_with_no_amount_is_never_accused():
    """Advice with no number attached cannot be checked against the book."""
    mark = {"at": _ago(30), "amount": 1215.09}
    assert _moved(mark, {"title": "Give it more time"}, done=True) is None


def test_a_book_that_moved_is_never_accused_of_not_moving():
    """THE ONE THING THIS RULE MUST NEVER DO: call a completed action a lie.

    A drift that closed from 1,215 to 90 did happen. PR 34's rule still un-mutes
    here -- a 90 ILS ask is a different instruction from a 1,215 ILS one, and in
    practice a gap that small usually falls under the drift threshold and no card
    is generated at all -- but the reason it gives must be the neutral one about
    the number changing, NEVER the accusation.
    """
    mark = {"at": _ago(30), "amount": 1215.09, "severity": "MEDIUM"}
    why = _moved(mark, {"est_amount": 90.0}, done=True)
    assert "did not happen" not in (why or ""), (
        f"a book that moved was told its action did not happen: {why}")
    assert "asks for about 90" in why


def test_a_moved_number_still_refires_for_both_kinds():
    """PR 34's rule is untouched underneath."""
    mark = {"at": _ago(30), "amount": 1215.09, "severity": "MEDIUM"}
    card = {"est_amount": 5000.0, "severity": "MEDIUM"}
    for done in (True, False):
        why = _moved(mark, card, done=done)
        assert why and "asks for about 5,000" in why


def test_a_severity_rise_still_wins_and_says_so():
    mark = {"at": _ago(1), "amount": 1215.09, "severity": "MEDIUM"}
    why = _moved(mark, {"est_amount": 1215.0, "severity": "HIGH"}, done=True)
    assert why and "MEDIUM" in why and "HIGH" in why


def test_the_call_site_tells_the_two_maps_apart():
    """`completed` and `dismissed` are different claims and must be passed as such.

    They are stored under two different KV keys, so the caller always knows
    which one a mark came from; the bug would be merging them and forgetting.
    """
    import inspect

    from app.services import recommendations as R

    src = inspect.getsource(R.build_recommendations) if hasattr(
        R, "build_recommendations") else inspect.getsource(R)
    # 2026-10-22: `completed` alone is no longer the answer. An id can sit in BOTH
    # maps (an old Accept and a newer Ignore), and `resolve_marks` picks the
    # newest -- so "was the mark I am holding a done" is `_done_wins`, not
    # membership in `completed`. Same requirement, stated against the winner.
    assert "done=(_rid_ in _done_wins)" in src, (
        "moved_on is called without saying whether the WINNING mark was a done")
