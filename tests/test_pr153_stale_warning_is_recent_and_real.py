"""PR 153: Today's "Some checks didn't run: history_stale" is recent and real.

Report (2026-10-26): the warning sat on Today with no outage behind it.

Root cause: Today raised `history_stale` whenever the history tier's
`stale_served` counter was above zero. That counter only ever grows and is reset
only by a restart, so ONE stale read after a deploy kept the warning up for the
life of the process. And a stale read does not need a failure: when a scan asks
for more expired series than the 20/s rate limit admits, the rest come from the
stale cache by design -- identical daily bars, nothing wrong.

Owner's choice (option A): warn only for a stale read caused by a real failure
(a failed fetch, an open circuit, or a follower inheriting a leader's failure),
and only within the last 30 minutes. A rate-limit pause is the cache doing its
job, counted but not warned about.
"""
from __future__ import annotations

import pytest

from app.providers.resilience import CircuitBreaker, ResilienceTier, TokenBucket, TTLCache


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def tick(self, s: float) -> None:
        self.t += s


WINDOW = 1800.0


def _tier(clk: Clock, *, bucket: TokenBucket | None = None,
          breaker: CircuitBreaker | None = None) -> ResilienceTier:
    return ResilienceTier(cache=TTLCache(ttl=3600.0, clock=clk, stale_ttl=21600.0),
                          breaker=breaker, bucket=bucket, attempts=1, clock=clk)


def _dead():
    raise OSError("provider down")


def test_a_rate_limit_pause_is_counted_but_not_warned_about():
    """The reproduction: a 25-key burst on a 20/s bucket, provider healthy."""
    clk = Clock()
    tier = _tier(clk, bucket=TokenBucket(20, 20, clock=clk))
    keys = [f"hist:T{i}:260" for i in range(25)]
    for i, k in enumerate(keys):              # first scan, spread out: all fresh
        clk.t = i * 0.1
        tier.call(k, lambda k=k: [k])
    clk.t = 4000.0                            # past the 1h fresh window
    for k in keys:                            # a burst: 20 fetched, 5 from stock
        tier.call(k, lambda k=k: [k])

    s = tier.stale_snapshot(WINDOW)
    assert s["stale_served"] == 5, "every stale read is still counted"
    assert s["stale_served_on_failure"] == 0
    assert s["stale_warning"] is False, "a throttle is not a failure"


def test_b_a_failed_fetch_served_stale_warns():
    clk = Clock()
    tier = _tier(clk)
    tier.call("hist:V", lambda: [1.0])
    clk.tick(4000.0)
    assert tier.call("hist:V", _dead) == [1.0]

    s = tier.stale_snapshot(WINDOW)
    assert s["stale_served_on_failure"] == 1
    assert s["stale_warning"] is True
    assert s["seconds_since_stale_on_failure"] == 0.0


def test_c_the_warning_ends_when_the_window_passes():
    """The bug itself: one stale read used to warn until the next deploy."""
    clk = Clock()
    tier = _tier(clk)
    tier.call("hist:V", lambda: [1.0])
    clk.tick(4000.0)
    tier.call("hist:V", _dead)
    clk.tick(WINDOW + 1)

    s = tier.stale_snapshot(WINDOW)
    assert s["stale_served_on_failure"] == 1, "history is kept"
    assert s["stale_warning"] is False, "but it is no longer news"


def test_d_an_open_circuit_served_stale_warns():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, clock=clk)
    tier = _tier(clk, breaker=cb)
    tier.call("hist:A", lambda: [1.0])
    clk.tick(4000.0)
    cb.record_failure("hist:X", OSError("boom"))
    assert cb.state == "OPEN"
    assert tier.call("hist:A", lambda: pytest.fail("the circuit is open")) == [1.0]
    assert tier.stale_snapshot(WINDOW)["stale_warning"] is True


def test_e_provider_health_reports_the_window_not_the_lifetime(monkeypatch):
    from app.providers import registry

    clk = Clock()
    hist = _tier(clk, bucket=TokenBucket(1, 1, clock=clk),
                 breaker=CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, clock=clk))
    hist.call("hist:A", lambda: [1.0])
    clk.tick(4000.0)
    hist.call("hist:A", lambda: [2.0])        # uses the one token
    hist.call("hist:A", lambda: [3.0])        # fresh again: cached, no stale
    monkeypatch.setattr(registry, "_tiers", lambda: {"history": hist})

    h = registry.provider_health()["history"]
    assert "stale_served" in h, "kept: the smoke scripts and the health page read it"
    assert h["stale_warning"] is False


@pytest.mark.parametrize("ph, expected", [
    ({"history": {"circuit_state": "CLOSED", "stale_served": 9, "stale_warning": False}}, []),
    ({"history": {"circuit_state": "CLOSED", "stale_served": 9, "stale_warning": True}},
     ["history_stale"]),
    ({"fundamentals": {"circuit_state": "CLOSED", "stale_warning": True}}, ["history_stale"]),
    ({"history": {"circuit_state": "OPEN"}},
     ["holding_verdicts", "momentum", "war_room"]),
])
def test_f_today_reads_the_warning_not_the_counter(ph, expected):
    from app.services.recommendations import _provider_degraded
    assert _provider_degraded(ph) == expected
