"""Every note a sleeve cap can carry must fit the column it is stored in.

``trading_rules.note`` is ``VARCHAR(160)``. **SQLite silently accepts a longer
string; Postgres raises `StringDataRightTruncationError` and the request 500s.**
So the entire local suite can be green while the app is broken in production,
which is exactly what happened: C2's two-sleeve wording came to 161 characters,
passed 653 local tests, and turned CI's `test-postgres` job red.

These assert against the real catalog rather than a hand-written string, so
renaming a strategy to something long fails here instead of in production.
"""
import itertools

import pytest

from app.models.tables import TradingRule
from app.services import strategy_catalog
from app.services.strategy_service import MAX_RULE_NOTE, _cap_note


def test_the_constant_matches_the_column_it_guards():
    """A hardcoded 160 that drifts from the model is worse than no constant."""
    assert TradingRule.__table__.c.note.type.length == MAX_RULE_NOTE


@pytest.mark.parametrize("sid", strategy_catalog.ids())
def test_a_single_sleeve_note_fits(sid):
    note = _cap_note("TQQQ", 20.0, [sid])
    assert len(note) <= MAX_RULE_NOTE, f"{len(note)} chars: {note}"


@pytest.mark.parametrize("pair", list(itertools.combinations(strategy_catalog.ids(), 2)))
def test_every_pair_of_sleeves_sharing_a_ticker_fits(pair):
    """The case that broke: two sleeves on one ticker, both names in the note."""
    note = _cap_note("TQQQ", 35.0, sorted(pair))
    assert len(note) <= MAX_RULE_NOTE, f"{len(note)} chars: {note}"


def test_every_strategy_at_once_still_fits():
    """The pathological end of the range. Seven sleeves cannot all name
    themselves in 160 characters, so the short form has to take over."""
    note = _cap_note("TQQQ", 99.9, sorted(strategy_catalog.ids()))
    assert len(note) <= MAX_RULE_NOTE, f"{len(note)} chars: {note}"
    # ...and it must still say the thing that matters.
    assert "One cap at the total" in note


def test_the_long_form_is_preferred_while_it_fits():
    """The short form is a fallback, not the default -- naming the sleeves is
    the whole reason a summed cap is explainable."""
    ids = ("btm_trend_tqqq", "btm_vol_target_tqqq")   # the pair that broke it
    note = _cap_note("TQQQ", 35.0, sorted(ids))
    names = [(strategy_catalog.get(i) or {}).get("name") for i in ids]
    assert all(n in note for n in names), (
        f"fell back to the count form at {len(note)} chars, losing both names: {note}")
