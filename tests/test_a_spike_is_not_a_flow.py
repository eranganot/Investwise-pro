"""PR 28 - the chart charged a bad snapshot's reversion to performance.

Reported live 2026-08-31: the account-history chart read **-24.9137%** over ten
days while the summary card on the same screen read +6.67% on the same book.

The whole -24.9137% is ONE step. Taken from the live payload:

    08-17  NAV 21,168.33  10 positions
    08-22  NAV 28,496.54   5 positions   <- +34.6% while HALF the book left
    08-24  NAV 21,369.63   5 positions   <- reverts, no shape change

    21369.63 / 28496.54 - 1 = -25.0097%

PR 22 taught `time_weighted` that a NAV move with a position-count change and a
silent ledger is not performance. It fixed the step INTO such a move -- 08-22
scores 0.00%, correctly. It left the step OUT of it alone: 08-22's raw NAV
anchored the next period, so the phantom +7,328 was removed from the numerator
of one period and left in the denominator of the next.

PR 22's own test asserts the flagged step is 0.0%. It never asserts what the
NEXT step does. That is the gap these tests close.
"""
from __future__ import annotations

from app.services.nav_history import simple_change_pct, time_weighted

# The live series, verbatim from /api/v1/portfolio/nav-history?range=MAX on
# 2026-08-31. Not a fixture built from a description of the incident -- the
# actual rows, which is the only kind that can falsify anything.
LIVE = [
    {"as_of": "2026-08-16", "nav_ils": 21168.33, "positions": 10},
    {"as_of": "2026-08-17", "nav_ils": 21168.33, "positions": 10},
    {"as_of": "2026-08-22", "nav_ils": 28496.54, "positions": 5},
    {"as_of": "2026-08-24", "nav_ils": 21369.63, "positions": 5},
    {"as_of": "2026-08-25", "nav_ils": 21534.31, "positions": 5},
    {"as_of": "2026-08-26", "nav_ils": 21540.83, "positions": 5},
    {"as_of": "2026-08-27", "nav_ils": 22114.59, "positions": 5},
    {"as_of": "2026-08-28", "nav_ils": 21397.00, "positions": 5},
    {"as_of": "2026-08-29", "nav_ils": 21333.59, "positions": 4},
    {"as_of": "2026-08-30", "nav_ils": 21333.59, "positions": 4},
]


def test_the_reported_minus_25_percent_is_gone():
    """The headline number Eran saw, on the rows that produced it."""
    r = time_weighted(LIVE, [])

    assert r["total_pct"] > -1.0, (
        f"still reporting {r['total_pct']}% on a book that moved "
        f"{simple_change_pct(LIVE)}%")
    # The book really did go up slightly over the window. The measured return
    # must agree in SIGN with that, which -24.9137% did not.
    assert r["total_pct"] > 0.0
    assert simple_change_pct(LIVE) > 0.0


def test_the_bad_snapshot_is_named_and_not_plotted():
    """Dropping it silently would be PR 22's own mistake in a new place."""
    r = time_weighted(LIVE, [])

    named = [s["as_of"] for s in r["suspect_snapshots"]]
    assert named == ["2026-08-22"], f"expected only 08-22 to be suspect, got {named}"
    assert "2026-08-22" not in r["dates"], (
        "the spike is excluded from the RETURN but still drawn on the chart, "
        "under a caption calling it untrustworthy")
    assert len(r["dates"]) == len(LIVE) - 1
    assert len(r["pct"]) == len(r["dates"]) == len(r["nav_ils"])
    assert r["suspect_snapshots"][0]["nav_ils"] == 28496.54


def test_the_step_OUT_of_an_anomaly_is_not_performance():
    """THE ACTUAL DEFECT, stated as small as it goes.

    A flow is attributed on the way in and the raw NAV then anchors the way out.
    Before this fix the second step scored -25%; there is no market move here at
    all, only one snapshot disagreeing with its neighbours.
    """
    spike = [
        {"as_of": "2026-01-01", "nav_ils": 10000.0, "positions": 4},
        {"as_of": "2026-01-02", "nav_ils": 14000.0, "positions": 2},   # bad row
        {"as_of": "2026-01-03", "nav_ils": 10000.0, "positions": 2},   # reverts
    ]
    r = time_weighted(spike, [])

    assert all(p > -1.0 for p in r["pct"]), (
        f"a snapshot that reverted was charged as a loss: {r['pct']}")
    assert r["total_pct"] == 0.0, (
        f"nothing moved across this window; got {r['total_pct']}%")


def test_a_removal_that_is_NOT_reversed_stays_an_ordinary_flow():
    """The other half, and the reason the fix is not simply 'ignore flows'.

    My first attempt stripped every attributed flow from the next period's base
    as well. It produced a total that matched the raw NAV change EXACTLY -- which
    looked like confirmation and was the opposite: it had cancelled every flow
    adjustment, so a genuine deposit would have stopped being excluded from
    performance too.

    The -63.41 on 2026-08-29 is a real removal. Nothing contradicts it, so it
    must stay ATTRIBUTED -- and never quietly folded into the return.

    PR 33 changed WHERE an unexplained step lands, not whether it is named. With
    a reconstruction available it is an estimated flow; without one the day is
    unmeasurable and the line breaks. Both are "named"; neither is performance.
    The assertion is on the property, so a future change of mechanism does not
    silently drop the protection this test exists for.
    """
    r = time_weighted(LIVE, [])
    named = ([f["as_of"] for f in r["unexplained_flows"]]
             + [u["as_of"] for u in r["unmeasured_days"]])
    assert "2026-08-29" in named, "the real removal stopped being attributed at all"
    assert "2026-08-22" not in named, (
        "the spike is still being called a flow; it should be a suspect snapshot")

    # and with a reconstruction it is specifically a FLOW, correctly signed
    r2 = time_weighted(LIVE, [], window_return=lambda a, b: 0.0)
    flows = {f["as_of"]: f["amount_ils"] for f in r2["unexplained_flows"]}
    assert "2026-08-29" in flows, "a flat market leaves the whole step as a flow"
    assert flows["2026-08-29"] < 0, "a removal cannot be money arriving"

    # ...and the two measures must still DIFFER. If time-weighted return equals
    # the raw NAV change to the decimal, the flow logic is dead again.
    assert r["total_pct"] != simple_change_pct(LIVE)


def test_a_small_wobble_that_reverses_is_not_reclassified():
    """The guard is aimed at a NAV that is plainly wrong, not at noise.

    Without the materiality floor, any two-day zigzag across a position change
    would start deleting snapshots from the user's chart.
    """
    wobble = [
        {"as_of": "2026-01-01", "nav_ils": 10000.0, "positions": 4},
        {"as_of": "2026-01-02", "nav_ils": 10100.0, "positions": 3},   # +1%
        {"as_of": "2026-01-03", "nav_ils": 10000.0, "positions": 3},
    ]
    r = time_weighted(wobble, [])
    assert r["suspect_snapshots"] == []
    assert len(r["dates"]) == 3


def test_a_second_shape_change_blocks_the_reversal_reading():
    """If the book changes shape again on the next step, that step has its own
    explanation and cannot be read as a clean reversal of the previous one."""
    twice = [
        {"as_of": "2026-01-01", "nav_ils": 10000.0, "positions": 4},
        {"as_of": "2026-01-02", "nav_ils": 14000.0, "positions": 2},
        {"as_of": "2026-01-03", "nav_ils": 10000.0, "positions": 1},   # changed AGAIN
    ]
    r = time_weighted(twice, [])
    assert r["suspect_snapshots"] == []


def test_a_recorded_deposit_is_never_treated_as_a_spike():
    """A ledger entry is authoritative. Money that really arrived and was then
    spent down is not a bad snapshot, however much it looks like one."""
    pts = [
        {"as_of": "2026-01-01", "nav_ils": 10000.0, "positions": 4},
        {"as_of": "2026-01-02", "nav_ils": 14000.0, "positions": 5},
        {"as_of": "2026-01-03", "nav_ils": 10000.0, "positions": 5},
    ]
    # `occurred_at`, which is the key net_flow_between actually reads. Writing
    # "as_of" here made this test pass a ledger the code could not see, so it
    # went red for the right reason on the wrong input -- the fixture was wrong,
    # not the guard.
    ledger = [{"occurred_at": "2026-01-02", "amount_ils": 4000.0}]
    r = time_weighted(pts, ledger)
    assert r["suspect_snapshots"] == [], "a recorded deposit was deleted as a spike"
