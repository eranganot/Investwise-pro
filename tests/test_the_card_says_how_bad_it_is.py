"""PR 12 -- three things that reported success, or reported nothing, while wrong.

**1. A job that crashed on every boot and said it was fine.**
`scheduler.start_scheduler` primed the price refresh with a bare
`run_price_refresh_blocking()` on the FastAPI startup thread. That thread has a
running event loop and the function calls `asyncio.run()`, which refuses to
nest -- so every single boot logged the RuntimeError and then RETURNED
`{"updated": 0, "failed": 0}`. Those are the same two numbers a clean run
produces when nothing needs repricing. The call was also outside `_guarded`, so
`JOB_STATE` never heard about it and `push_service.blockers` -- which PR 2
taught to read job state -- could not name it. Seen live in the 2026-08-25
16:07:27 Railway log.

**2. The solver's biggest finding had no card.**
PR 11 stored the verdict and let the war room take a one-tap away with it. But
`floor_breaches_ceiling` is not a statement about any one signal: it means the
core alone breaches the ceiling, so NO sleeve size repairs it, including zero.
On the live book that was 35.1% against a 13.3% ceiling and Today showed five
cards, none of which mentioned it. Attaching it to `_war_room_recs` would not
have helped -- that book had one displayed session and it was a Rebalance, so a
21-point breach would have gone unsaid for want of an unrelated BUY.

**3. The rebalance card called a 20-point drift "nice to have".**
`severity` was the literal string "MEDIUM", so a book 3.1 points out of target
and a book 20.4 points out rendered identically. Four other card types in the
same file already scale by magnitude. And `_reconcile` rewrote the merged
card's id, title, action, why and meta while never touching its severity --
harmless while every leg was hardcoded MEDIUM, and a demotion-by-sort-order the
moment they differ. Fixing the scale without fixing the merge would have
introduced the bug the scale was meant to fix.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import pricing_service as ps  # noqa: E402
from app.services import recommendations as R  # noqa: E402
from app.services import solver_store as ss  # noqa: E402


class _Row:
    """A SolverVerdict without a database, so the rule stays testable."""

    def __init__(self, **kw):
        self.subject = kw.get("subject", "a@b.c")
        self.solver_version = kw.get("solver_version", "s1")
        self.outcome = kw.get("outcome", "DRAWDOWN_BOUND")
        self.target_excess_pct = kw.get("target_excess_pct", 2.0)
        self.max_drawdown_pct = kw.get("max_drawdown_pct", 13.3)
        self.solved_total_sleeve_pct = kw.get("solved_total_sleeve_pct", 0.0)
        self.floor_breaches_ceiling = kw.get("floor_breaches_ceiling", True)
        self.binding_constraint = kw.get("binding_constraint", "drawdown_ceiling")
        self.source = kw.get("source", "on_demand")
        self.detail = kw.get("detail", {"floor": {"max_drawdown_pct": -35.12}})
        self.created_at = kw.get("created_at",
                                 datetime.now(timezone.utc) - timedelta(hours=1))


# ===========================================================================
# 1. the price refresh must not report success from a crash
# ===========================================================================

def test_the_price_refresh_refuses_to_run_on_a_thread_with_a_loop():
    """The precise condition that failed on every boot.

    Asserted as a RAISE, not as a return value: the old code's whole defect was
    that it had a return value here at all.
    """
    async def _on_the_loop():
        with pytest.raises(RuntimeError) as err:
            ps.run_price_refresh_blocking()
        return str(err.value)

    msg = asyncio.run(_on_the_loop())
    # The message has to name the caller's mistake. asyncio's own wording
    # ("asyncio.run() cannot be called from a running event loop") reads like a
    # library bug and sent the last reader looking in the wrong file.
    assert "event loop" in msg
    assert "worker thread" in msg


def test_the_price_refresh_never_returns_the_success_shape_on_failure(monkeypatch):
    """`{"updated": 0, "failed": 0}` is what a CLEAN run returns when there is
    nothing to reprice. Returning it from a crash makes the two indistinguishable
    to every reader downstream -- which is exactly how this went unnoticed for
    as long as it did."""
    async def _boom():
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(ps, "_refresh_all", _boom)
    with pytest.raises(RuntimeError):
        ps.run_price_refresh_blocking()


def test_the_scheduler_primes_the_price_refresh_through_the_guarded_path():
    """The prime must go through `_guarded`, which runs its target in a worker
    thread AND records the outcome. A bare call does neither.

    Asserted against the source rather than by booting a scheduler: the failure
    was a single unguarded call site, and a test that starts APScheduler proves
    less about that line than reading it does.
    """
    import inspect

    from app.worker import scheduler

    src = inspect.getsource(scheduler.start_scheduler)
    assert "run_price_refresh_blocking()  # prime at startup" not in src, (
        "the bare on-loop prime is back")
    # It is registered once, guarded, and primed via next_run_time.
    assert '_guarded("price_refresh", run_price_refresh_blocking)' in src
    assert "next_run_time=" in src


# ===========================================================================
# 2. the solver's structural finding gets a card
# ===========================================================================

def _constraint(**kw):
    return ss.as_constraint(_Row(**kw))


def test_the_constraint_exposes_the_core_drawdown_as_a_number():
    """The headline states it in prose. A card that has to pull the figure back
    out of a sentence to put it in an audit trail is one rewording away from
    printing nothing."""
    c = _constraint()
    assert c["core_drawdown_pct"] == -35.12
    assert c["ceiling_pct"] == 13.3


def _card(monkeypatch, **kw):
    """Run `_core_ceiling_recs` against a verdict, with no database."""
    row = _Row(**kw) if kw.get("_row", True) else None

    async def _latest(session, user):
        return row

    monkeypatch.setattr(ss, "latest", _latest)
    return asyncio.run(R._core_ceiling_recs(None, None))


def test_a_core_that_breaches_the_ceiling_gets_a_card(monkeypatch):
    cards = _card(monkeypatch)
    assert len(cards) == 1
    c = cards[0]
    assert "drawdown ceiling" in c["title"].lower()
    # The numbers, on the card, both of them.
    assert "35.1" in c["why"] and "13.3" in c["why"]
    # And the gap stated rather than left as arithmetic for the reader.
    assert "21.8" in c["why"]


def test_the_card_is_advisory_and_can_never_be_one_tap(monkeypatch):
    """Swapping a core is a whole-book change and `target_solver` is read-only
    without exception. `apply.kind == "none"` is the mechanism every advisory
    card already uses, so nothing new can forget to check a new flag."""
    c = _card(monkeypatch)[0]
    assert c["apply"]["kind"] == "none"


def test_the_card_carries_the_verdicts_age(monkeypatch):
    """Same rule as every other reader of a stored verdict: a statement about
    one book at one moment has to say when that moment was."""
    c = _card(monkeypatch, created_at=datetime.now(timezone.utc) - timedelta(hours=5))[0]
    assert "5h ago" in c["why"]
    assert c["meta"]["age_hours"] == 5.0


def test_a_stale_verdict_still_speaks(monkeypatch):
    """PR 11's rule is "stale stops blocking, it does not stop speaking". This
    card blocks nothing, so staleness changes the wording and not whether it
    appears. Hiding a 21-point breach because the measurement is two days old
    would be that rule inverted."""
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    cards = _card(monkeypatch, created_at=old)
    assert len(cards) == 1, "a stale verdict must still produce the card"
    assert cards[0]["meta"]["stale"] is True
    assert "too old to enforce" in cards[0]["why"]


def test_no_verdict_means_no_card(monkeypatch):
    """A book with nothing measured gets silence, not an asserted breach.
    Inventing a finding is worse than missing one."""
    async def _none(session, user):
        return None

    monkeypatch.setattr(ss, "latest", _none)
    assert asyncio.run(R._core_ceiling_recs(None, None)) == []


def test_a_verdict_that_does_not_breach_produces_no_card(monkeypatch):
    """`floor_breaches_ceiling` is the whole trigger. A DRAWDOWN_BOUND verdict
    whose core is fine is a different conversation and this card must not
    borrow its authority."""
    cards = _card(monkeypatch, floor_breaches_ceiling=False)
    assert cards == []


def test_the_card_id_tracks_the_finding_not_the_family(monkeypatch):
    """PR 4: an id with no variable part means one Accept ever hides the whole
    family for the TTL. A breach that moves from 35% to 20% is a different
    statement and has to be able to reappear."""
    a = _card(monkeypatch)[0]["id"]
    b = _card(monkeypatch, detail={"floor": {"max_drawdown_pct": -20.0}})[0]["id"]
    assert a != b


# ===========================================================================
# 3. the rebalance card says how bad it is
# ===========================================================================

def test_drift_severity_scales_off_the_threshold_that_created_the_card():
    """The multiple, not a second set of absolute cut-offs.
    `allocation_drift_threshold` already decides a drift is worth a card at all;
    an independent scale would be a second opinion about what "drifted" means.
    At the default 3%: HIGH at 9 points, CRITICAL at 18."""
    assert R._drift_severity(0.50, 0.53) == "MEDIUM"      # 3.0 pts, 1x
    assert R._drift_severity(0.50, 0.59) == "HIGH"        # 9.0 pts, 3x
    assert R._drift_severity(0.50, 0.68) == "CRITICAL"    # 18.0 pts, 6x


def test_the_live_book_that_prompted_this_is_critical():
    """The book that rendered as "nice to have": Equities 97.03% against a
    76.6% target is 20.4 points out, which is 6.8x the 3% trigger."""
    assert R._drift_severity(0.9703, 0.766) == "CRITICAL"
    # And the two legs that ride with it are not MEDIUM either.
    assert R._drift_severity(0.0, 0.144) == "HIGH"        # Fixed Income
    assert R._drift_severity(0.0, 0.090) == "HIGH"        # Commodities


def test_severity_is_never_a_hardcoded_string_on_the_rebalance_card():
    """The defect was a literal. A test on `_drift_severity` alone would pass
    forever while the card ignored it."""
    import inspect

    src = inspect.getsource(R._recommendations_for) if hasattr(
        R, "_recommendations_for") else inspect.getsource(R)
    assert '_rid("rebal", a.asset_class), "dimension": "allocation", "severity": "MEDIUM"' \
        not in src


def test_a_merged_rebalance_takes_the_worst_legs_severity():
    """`_reconcile` rewrote the merged card's id, title, action, why and meta
    and never its severity, so `keep` kept `rebal[0]`'s -- and `rebal[0]` is
    whichever leg sorted first, which has nothing to do with which is worst."""
    def leg(cls, sev, amt):
        return {"id": R._rid("rebal", cls), "dimension": "allocation", "severity": sev,
                "title": f"Sell {cls}", "action": "", "why": "", "est_amount": amt,
                "apply": {"kind": "rebalance_to_objective", "asset_class": cls}}

    # MEDIUM first, so sort order and severity disagree -- which is the case the
    # old code got wrong and a same-order case would have passed.
    recs = [leg("Equities", "MEDIUM", 4401),
            leg("Fixed Income", "CRITICAL", 3102),
            leg("Commodities", "HIGH", 1939)]
    out = R._reconcile(recs, market={})
    merged = [r for r in out if (r.get("apply") or {}).get("kind") == "rebalance_to_objective"]
    assert len(merged) == 1, "the legs must still merge into one card"
    assert merged[0]["severity"] == "CRITICAL"


def test_merging_never_lowers_the_severity_below_any_leg():
    """The property, rather than the one arrangement above."""
    def leg(cls, sev):
        return {"id": R._rid("rebal", cls), "dimension": "allocation", "severity": sev,
                "title": f"Sell {cls}", "action": "", "why": "", "est_amount": 100,
                "apply": {"kind": "rebalance_to_objective", "asset_class": cls}}

    rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    for sevs in (["LOW", "HIGH"], ["HIGH", "MEDIUM"], ["CRITICAL", "LOW", "MEDIUM"],
                 ["MEDIUM", "MEDIUM"], ["LOW", "LOW", "CRITICAL"]):
        recs = [leg(f"C{i}", s) for i, s in enumerate(sevs)]
        out = R._reconcile(recs, market={})
        merged = [r for r in out
                  if (r.get("apply") or {}).get("kind") == "rebalance_to_objective"][0]
        assert rank[merged["severity"]] == max(rank[s] for s in sevs), sevs
