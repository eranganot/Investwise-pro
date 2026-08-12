"""Resilient middle tier (Section AE): retries, rate limiting, cache, breaker.

Pure and clock-injectable so it unit-tests deterministically. Composed in one
`ResilienceTier.call(key, fn)` guard wrapped around every provider fetch.

THREAD SAFETY (added with the Phase B thread-pool offload)
----------------------------------------------------------
Until provider I/O moved off the event loop, every one of these objects was
touched by exactly one thread, so none of them needed a lock and none had one.
`asyncio.to_thread` makes that assumption false. What that actually costs was
MEASURED rather than assumed, because the textbook answer and the observed one
disagree:

  * TTLCache -- THE REAL DEFECT, and the only one reproducible at ordinary
    concurrency. It never corrupted (dict ops hold the GIL), but it
    deduplicated concurrent work only BY ACCIDENT: requests were serialized,
    so the second request always found the first one's result already stored.
    Overlap the requests and N callers miss the same cold key together and all
    fetch it. Two tests in test_resilience_threading.py fail on the old code.
  * TokenBucket.take -- a genuine check-then-act race, and it does over-admit:
    a capacity-5 bucket granted 12, and a capacity-50 bucket granted 55. But
    only at ~1000 threads with sys.setswitchinterval(1e-9). At the concurrency
    a thread pool actually produces it was not reproducible, so the lock here
    is correctness insurance, not a fix for something observed in production.
  * CircuitBreaker.record_failure -- `failures += 1` is the classic non-atomic
    read-modify-write, and it could NOT be made to lose a single update in
    1.28 million concurrent increments across 64 threads. CPython's eval loop
    does not preempt that sequence in practice. The lock stays because that is
    an implementation detail rather than a guarantee (free-threaded builds
    remove it outright), but no bug was demonstrated and this docstring is not
    going to imply one was.

The cache point is the one that changed this phase's plan. The plan said the
TTL caching would be left exactly as it is -- and leaving the CODE exactly as
it is would have changed the BEHAVIOUR, turning a latency problem into a
provider fan-out problem. Preserving the behaviour is what needed new code:
`_inflight` below makes the deduplication explicit and deliberate rather than a
side effect of being single-threaded.

Locks are per-object and held only around bookkeeping, never around `fn()` --
the whole point of this phase is that the slow call runs concurrently.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

_MISSING = object()


class CircuitOpenError(Exception):
    pass


class RateLimitedError(Exception):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self.failures = 0
        self.state = "CLOSED"        # CLOSED | OPEN | HALF_OPEN
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == "OPEN":
                if self._clock() - self.opened_at >= self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = "CLOSED"

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold or self.state == "HALF_OPEN":
                self.state = "OPEN"
                self.opened_at = self._clock()


class TokenBucket:
    def __init__(self, capacity: float, refill_per_sec: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._clock = clock
        self.tokens = capacity
        self.updated = clock()
        self._lock = threading.Lock()

    def take(self, n: float = 1.0) -> bool:
        with self._lock:
            now = self._clock()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_sec)
            self.updated = now
            if self.tokens >= n:
                self.tokens -= n
                return True
            return False


class TTLCache:
    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._store: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._store.get(key, _MISSING)
            if item is _MISSING:
                return _MISSING
            value, expires = item
            if self._clock() >= expires:
                self._store.pop(key, None)
                return _MISSING
            return value

    def set(self, key, value) -> None:
        with self._lock:
            self._store[key] = (value, self._clock() + self.ttl)


def retry(fn: Callable, attempts: int = 3, base_delay: float = 0.0,
          sleep: Callable[[float], None] = time.sleep):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < attempts - 1:
                sleep(base_delay * (2 ** i))  # exponential backoff
    raise last


class ResilienceTier:
    def __init__(self, *, breaker: CircuitBreaker | None = None,
                 bucket: TokenBucket | None = None, cache: TTLCache | None = None,
                 attempts: int = 3, base_delay: float = 0.0,
                 sleep: Callable[[float], None] = time.sleep,
                 single_flight: bool = True) -> None:
        self.breaker = breaker
        self.bucket = bucket
        self.cache = cache
        self.attempts = attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self.single_flight = single_flight
        # key -> Event, signalled when the leader for that key finishes.
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()

    def _claim(self, key: str) -> tuple[bool, threading.Event | None]:
        """Become the leader for `key`, or get the event to wait on."""
        with self._inflight_lock:
            waiting = self._inflight.get(key)
            if waiting is not None:
                return False, waiting
            self._inflight[key] = threading.Event()
            return True, None

    def _release(self, key: str) -> None:
        with self._inflight_lock:
            done = self._inflight.pop(key, None)
        if done is not None:
            done.set()

    def _fetch(self, key: str, fn: Callable):
        if self.bucket is not None and not self.bucket.take():
            raise RateLimitedError(f"rate limit exceeded for {key}")
        if self.breaker is not None and not self.breaker.allow():
            raise CircuitOpenError(f"circuit open for {key}")
        try:
            value = retry(fn, self.attempts, self.base_delay, self.sleep)
        except Exception:
            if self.breaker is not None:
                self.breaker.record_failure()
            raise
        if self.breaker is not None:
            self.breaker.record_success()
        if self.cache is not None:
            self.cache.set(key, value)
        return value

    def call(self, key: str, fn: Callable):
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not _MISSING:
                return cached

        # No cache means nothing to share, so there is nothing to deduplicate:
        # every caller genuinely wants its own fetch.
        if not self.single_flight or self.cache is None:
            return self._fetch(key, fn)

        leader, waiting = self._claim(key)
        if leader:
            try:
                return self._fetch(key, fn)
            finally:
                # Released even on failure, so a follower is never left waiting
                # on a leader that already gave up.
                self._release(key)

        # Follower: wait for the leader, then read what it stored. If the
        # leader FAILED there is nothing cached, and the follower falls through
        # to its own attempt rather than inheriting an exception it did not
        # cause. A dead provider therefore still drives the breaker open, which
        # is the behaviour we want.
        waiting.wait()
        cached = self.cache.get(key)
        if cached is not _MISSING:
            return cached
        return self._fetch(key, fn)
