"""Phase B: the resilience tier under real concurrency.

Two of these fail on the pre-Phase-B code -- both single-flight tests. The rest
pass on it, and saying so matters more than a tidier claim would: they are
REGRESSION GUARDS, not demonstrations of a bug that was hurting production.

What was measured while writing them, since the textbook answer and the
observed one disagree:

  * CircuitBreaker's `failures += 1` lost ZERO updates in 1.28 million
    concurrent increments across 64 threads. Not reproducible at all.
  * TokenBucket's check-then-act DOES over-admit -- a capacity-5 bucket granted
    12 -- but only at ~1000 threads with sys.setswitchinterval(1e-9). A test
    that needs that to fail would be flaky, which is worse than no test, so the
    version here runs at ordinary concurrency and guards the lock instead.

The locks are still worth having: CPython's incidental atomicity is an
implementation detail, not a guarantee, and free-threaded builds remove it.
But no test here should be read as evidence that the bucket or the breaker was
misbehaving in production, because none of them showed that.
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.providers.resilience import (
    CircuitBreaker, ResilienceTier, TokenBucket, TTLCache,
)


def _parallel(fn, n: int):
    """Run fn() on n threads released as simultaneously as we can arrange."""
    start = threading.Barrier(n)

    def run(i):
        start.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result() for f in [pool.submit(run, i) for i in range(n)]]


def test_token_bucket_does_not_over_admit_under_threads():
    """REGRESSION GUARD -- passes on the unlocked code too.

    `take` is check-then-act: `if self.tokens >= n: self.tokens -= n`. That
    genuinely over-admits (a capacity-5 bucket was observed granting 12), but
    only at ~1000 threads with sys.setswitchinterval(1e-9). Reproducing it here
    would mean a 1000-thread test that fails intermittently, which is a worse
    test than this one. This asserts the invariant at realistic concurrency so
    that removing the lock is at least noticed.
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        bucket = TokenBucket(capacity=5, refill_per_sec=0)
        granted = _parallel(lambda _: bucket.take(), 64)
        assert sum(1 for g in granted if g) == 5, "the rate limit leaked"
        assert bucket.tokens == 0
    finally:
        sys.setswitchinterval(old)


def test_circuit_breaker_counts_every_concurrent_failure():
    """REGRESSION GUARD -- passes on the unlocked code too.

    `failures += 1` is the textbook non-atomic read-modify-write, and it could
    not be made to lose one update in 1.28M concurrent increments across 64
    threads. Kept because the breaker is the guard that protects us from a dead
    provider, so its count is worth pinning even if CPython currently gets it
    right by accident.
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        breaker = CircuitBreaker(failure_threshold=5000, recovery_timeout=999)

        def hammer(_):
            for _ in range(200):
                breaker.record_failure()

        _parallel(hammer, 16)
        assert breaker.failures == 16 * 200, (
            f"lost {16 * 200 - breaker.failures} failures to a lost update"
        )
        assert breaker.state == "CLOSED"  # 3200 < 5000, so it must NOT open
    finally:
        sys.setswitchinterval(old)


def test_circuit_breaker_opens_at_its_threshold_under_threads():
    breaker = CircuitBreaker(failure_threshold=10, recovery_timeout=999)
    _parallel(lambda _: breaker.record_failure(), 10)
    assert breaker.failures == 10
    assert breaker.state == "OPEN"


def test_ttl_cache_survives_concurrent_readers_and_writers():
    cache = TTLCache(ttl=60)
    _parallel(lambda i: cache.set(f"k{i % 10}", i), 100)
    assert all(cache.get(f"k{i}") is not None for i in range(10))


def test_concurrent_misses_on_one_key_cause_exactly_one_fetch():
    """The single-flight guarantee.

    This is the behaviour serialization used to give for free: the second
    request always found the first one's result already cached. Once requests
    genuinely overlap, N callers miss the same cold key together -- so without
    single-flight this is N provider calls, and the thread-pool "fix" would
    multiply provider load instead of just unblocking the loop.
    """
    calls = []
    calls_lock = threading.Lock()

    def slow_provider():
        with calls_lock:
            calls.append(1)
        time.sleep(0.15)          # long enough that all 20 threads overlap it
        return "quote"

    tier = ResilienceTier(cache=TTLCache(ttl=60), bucket=None, breaker=None)
    results = _parallel(lambda _: tier.call("quote:TQQQ", slow_provider), 20)

    assert results == ["quote"] * 20
    assert len(calls) == 1, f"expected 1 provider call, got {len(calls)}"


def test_single_flight_is_per_key_not_global():
    # Two different tickers must not serialize behind each other -- that would
    # trade the event-loop bottleneck for a cache-level one.
    calls = []
    calls_lock = threading.Lock()

    def slow(key):
        with calls_lock:
            calls.append(key)
        time.sleep(0.2)
        return key

    tier = ResilienceTier(cache=TTLCache(ttl=60), bucket=None, breaker=None)
    started = time.monotonic()
    _parallel(lambda i: tier.call(f"quote:T{i}", lambda: slow(f"T{i}")), 4)
    elapsed = time.monotonic() - started

    assert sorted(calls) == ["T0", "T1", "T2", "T3"]
    # Serialized would be ~0.8s; concurrent is ~0.2s. 0.5s separates them
    # without being tight enough to flake on a loaded CI box.
    assert elapsed < 0.5, f"different keys serialized: {elapsed:.2f}s"


def test_a_failed_leader_does_not_strand_its_followers():
    """A follower must never wait forever on a leader that raised."""
    attempts = []
    attempts_lock = threading.Lock()

    def flaky():
        with attempts_lock:
            attempts.append(1)
            n = len(attempts)
        time.sleep(0.05)
        if n <= 1:
            raise RuntimeError("provider down")
        return "recovered"

    tier = ResilienceTier(cache=TTLCache(ttl=60), bucket=None, breaker=None,
                          attempts=1)

    results = []
    errors = []

    def go(_):
        try:
            results.append(tier.call("quote:X", flaky))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    _parallel(go, 5)

    # The leader's failure is its own; the followers retried rather than
    # inheriting an exception they did not cause.
    assert len(errors) <= 1
    assert "recovered" in results


def test_a_failing_provider_still_drives_the_breaker_open():
    # If followers silently inherited the leader's exception, only one failure
    # would ever be recorded and a dead provider would never trip the breaker.
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=999)
    tier = ResilienceTier(cache=TTLCache(ttl=60), breaker=breaker, bucket=None,
                          attempts=1)

    def dead():
        time.sleep(0.02)
        raise RuntimeError("provider down")

    def go(_):
        try:
            tier.call("quote:DEAD", dead)
        except Exception:  # noqa: BLE001, S110
            pass

    _parallel(go, 8)
    assert breaker.state == "OPEN"


def test_single_flight_can_be_switched_off():
    calls = []
    calls_lock = threading.Lock()

    def slow():
        with calls_lock:
            calls.append(1)
        time.sleep(0.1)
        return "v"

    tier = ResilienceTier(cache=TTLCache(ttl=60), bucket=None, breaker=None,
                          single_flight=False)
    _parallel(lambda _: tier.call("k", slow), 5)
    assert len(calls) > 1
