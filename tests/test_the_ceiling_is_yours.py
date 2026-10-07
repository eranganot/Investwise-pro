"""PR 14 -- the ceiling went stale and nothing noticed.

`index.html` seeds the drawdown ceiling ONCE, from
`performance(range=MAX).max_drawdown_pct` -- "the risk the book already carries"
-- and only when it is null. From then on it lives in the stored verdict, and
PR 11's nightly job re-runs *"the LAST verdict's own parameters"*, so whatever
was seeded propagates forever.

On this book a **13.3%** ceiling survived from an era when the history provider
returned a short window. The same endpoint now measures **58.59%** over 2,513
sessions. Every answer the solver gave for a week was "no", and the reason was
the number rather than the portfolio:

    T2   DRAWDOWN_BOUND          the target needs more risk than 13.3%
    T6b  NONE_ADMISSIBLE         all 15 candidate cores breach 13.3%
    T6b2 BASELINE_BREACHES_...   the two held sleeves breach 13.3%

At 58.6% the same three searches return RANKED, count=2 converged, and a real
ranking with the current holdings third of fifteen.

**It does not re-seed.** Silently moving a risk constraint the user is supposed
to own is worse than leaving it wrong -- the rule PR 11 already states for the
nightly job: *"a constraint strong enough to disable a button has to come from a
number the user actually set."* So this measures the mismatch and says it, and
the number stays theirs.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

from app.services import recommendations as R  # noqa: E402
from app.services import solver_store as ss  # noqa: E402


class _Row:
    def __init__(self, **kw):
        self.subject = "a@b.c"
        self.solver_version = kw.get("solver_version", "s1")
        self.outcome = kw.get("outcome", "DRAWDOWN_BOUND")
        self.target_excess_pct = 2.0
        self.max_drawdown_pct = kw.get("max_drawdown_pct", 13.3)
        self.solved_total_sleeve_pct = 0.0
        self.floor_breaches_ceiling = kw.get("floor_breaches_ceiling", True)
        self.binding_constraint = "drawdown_ceiling"
        self.source = "on_demand"
        self.detail = kw.get("detail", {"floor": {"max_drawdown_pct": -35.12}})
        self.created_at = datetime.now(timezone.utc) - timedelta(hours=1)


def _cc(**kw):
    base = {"ceiling_pct": 13.3, "book_measured_pct": 58.59, "at_sleeve_pct": 64.0,
            "book_already_breaches": True, "gap_pct": 45.29,
            "window_sessions": 2513, "note": "n"}
    base.update(kw)
    return base


# ===========================================================================
# the solver measures the mismatch
# ===========================================================================

def test_the_solver_attaches_a_ceiling_check_to_every_outcome():
    """Four exit paths; a field present on three of them is a field no reader
    can rely on."""
    import inspect

    from app.services import target_solver as T

    src = inspect.getsource(T.solve)
    assert src.count("_out(") >= 4, "every exit path must stamp the check"
    assert "ceiling_check" in src


def test_the_solver_does_not_reseed_the_ceiling():
    """The whole point. A search that quietly moved the number would 'fix' this
    book and hide the same failure on every other one."""
    import inspect

    from app.services import target_solver as T

    src = inspect.getsource(T.solve)
    assert "does NOT re-seed" in src or "does not re-seed" in src.lower()
    # And nothing in the solver may assign to the ceiling it was handed.
    assert "max_drawdown_pct =" not in src.replace("max_drawdown_pct ==", "")


# ===========================================================================
# the stored verdict carries it, and says when it does not know
# ===========================================================================

def test_a_verdict_solved_before_pr14_says_it_does_not_know():
    """"We did not check" and "checked, nothing wrong" are different claims and
    a card must not render them the same way."""
    v = ss.as_constraint(_Row())
    assert v["ceiling_check"]["known"] is False
    assert v["ceiling_check"]["book_already_breaches"] is False
    assert v["ceiling_check"]["book_measured_pct"] is None


def test_a_stored_ceiling_check_reaches_the_constraint():
    v = ss.as_constraint(_Row(detail={"floor": {"max_drawdown_pct": -35.12},
                                      "ceiling_check": _cc()}))
    cc = v["ceiling_check"]
    assert cc["known"] is True
    assert cc["book_already_breaches"] is True
    assert cc["ceiling_pct"] == 13.3
    assert cc["book_measured_pct"] == 58.59
    assert cc["gap_pct"] == 45.29


def test_a_ceiling_the_book_sits_inside_is_known_and_not_breaching():
    v = ss.as_constraint(_Row(detail={"ceiling_check": _cc(
        ceiling_pct=75.0, book_already_breaches=False, gap_pct=-16.41)}))
    assert v["ceiling_check"]["known"] is True
    assert v["ceiling_check"]["book_already_breaches"] is False


# ===========================================================================
# the card names ONE cause, in precedence order
# ===========================================================================

def _cause(ceiling_check=None, none_admissible=False):
    c = {"ceiling_check": ceiling_check or {"known": False,
                                            "book_already_breaches": False}}
    found = {"none_admissible": none_admissible,
             "means": "The core is not what is breaching the ceiling."}
    return R._ceiling_cause(c, found)


def test_the_ceiling_outranks_the_sleeves_and_the_core():
    """A ceiling below what the book already does BOUNDS every finding
    underneath it, so reporting the core or the sleeves first would answer a
    question the reader cannot act on yet."""
    out = _cause(ceiling_check={"known": True, "book_already_breaches": True,
                                "ceiling_pct": 13.3, "book_measured_pct": 58.59,
                                "gap_pct": 45.29},
                 none_admissible=True)
    assert out["kind"] == R._CAUSE_CEILING
    assert "13.3" in out["action"] and "58.59" in out["action"]
    # And it says WHERE to change it, not just that it is wrong.
    assert "WITHOUT EXCEEDING" in out["action"]


def test_the_sleeves_are_named_once_the_ceiling_is_settled():
    out = _cause(ceiling_check={"known": True, "book_already_breaches": False},
                 none_admissible=True)
    assert out["kind"] == R._CAUSE_SLEEVES


def test_the_core_is_named_only_when_a_measurement_supports_it():
    """"Change the core" is the advice T6b refuted on this book. It survives
    only where nothing has refuted it."""
    out = _cause(ceiling_check={"known": True, "book_already_breaches": False},
                 none_admissible=False)
    assert out["kind"] == R._CAUSE_CORE
    assert "core" in out["action"].lower()


def test_an_unchecked_ceiling_never_becomes_the_cause():
    """Absence of a check is not evidence the ceiling is fine, but it is also
    not evidence it is broken - so it must not seize the headline."""
    out = _cause(ceiling_check={"known": False, "book_already_breaches": False},
                 none_admissible=True)
    assert out["kind"] == R._CAUSE_SLEEVES


@pytest.mark.parametrize("breaches,none_adm,expected", [
    (True, True, R._CAUSE_CEILING),
    (True, False, R._CAUSE_CEILING),
    (False, True, R._CAUSE_SLEEVES),
    (False, False, R._CAUSE_CORE),
])
def test_the_precedence_is_total(breaches, none_adm, expected):
    """Every combination lands on exactly one cause. A card that can name two
    is a card that will name both."""
    out = _cause(ceiling_check={"known": True, "book_already_breaches": breaches,
                                "ceiling_pct": 13.3, "book_measured_pct": 58.59,
                                "gap_pct": 45.29},
                 none_admissible=none_adm)
    assert out["kind"] == expected


# ===========================================================================
# the smoke scripts stop re-asserting a stale number
# ===========================================================================

@pytest.mark.parametrize("name", [
    "smoke-t5-solver-constraint.ps1",
    "smoke-t6b-core-search.ps1",
    "smoke-t6b-sleeve-count.ps1",
])
def test_no_smoke_script_hardcodes_a_ceiling(name):
    """13.3 was a DEFAULT in three of my own scripts, which kept feeding a stale
    figure back into production every time one was run with -Solve."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "scripts" / "archive" / "smoke" / name).read_text(encoding="utf-8")
    assert "$MaxDrawdownPct = 13.3" not in src
    assert "Resolve-Ceiling" in src, "it must read the user's stored ceiling"
