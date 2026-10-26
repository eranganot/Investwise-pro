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
    """One breaker guards a whole KEYSPACE, so it has to tell the difference
    between "this symbol has no data" and "the provider is down".

    It could not, and that is how `history` sat OPEN in production for a week
    while `market` -- the same Yahoo provider, the same
    `/v8/finance/chart/{sym}` endpoint, one query parameter apart -- stayed
    CLOSED and served quotes the whole time.

    The asymmetry is entirely in WHICH KEYS each one asks for. Quotes are
    fetched for tickers you HOLD: four names, all real, all fetchable. History
    is fetched across the screener's candidate set -- 25 names per scan, drawn
    from a universe the user never curated. Any handful of those that Yahoo
    will not serve used to count, on a plain `failures += 1`, toward a
    threshold of five. Five unfetchable symbols took history dark for every
    symbol, including the four you own and the benchmark.

    So the count alone was the wrong question. Two rules replace it.

    FIRST, and this is the one that actually fixes it: a symbol the provider
    ANSWERED about and had no data for is not a provider failure at all. See
    `is_no_data`. Those are recorded and shown in health, and they cannot open
    anything, however many of them there are.

    SECOND, among real transport failures, what matters is whether the provider
    is still serving ANYONE:

      * broad outage -- `failure_threshold` DISTINCT keys failing, and more
        keys failing than succeeding. That is the provider, not the symbols.
      * total blackout -- `failure_threshold` failures with not one key
        succeeding. Covers the case where the only key anyone asks for is dead,
        which from the caller's side is indistinguishable from a dead provider.

    Everything is measured over a rolling `window`; a failure from an hour ago
    is not evidence about now.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic,
                 window: float | None = None) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        # Long enough that ONE SCAN is judged as one piece of evidence.
        #
        # This was 4x the recovery timeout -- 120s -- and a cold candidate scan
        # takes longer than that: 25 tickers of history plus fundamentals,
        # serially, over the network. So the successes from the start of a scan
        # aged out before the failures at the end of it arrived, and the tier
        # concluded it had never served anybody. Caught in a sandbox run against
        # the real provider, where `succeeding_key_count` read 0 immediately
        # after twenty-odd tickers had fetched perfectly.
        self.window = window if window is not None else max(300.0, recovery_timeout * 10)
        self._clock = clock
        self.failures = 0
        self.state = "CLOSED"        # CLOSED | OPEN | HALF_OPEN
        self.opened_at = 0.0
        self._lock = threading.Lock()

        # Rolling evidence, key -> last-seen timestamp.
        self._fail_keys: dict[str, float] = {}
        self._ok_keys: dict[str, float] = {}
        # Failure TIMESTAMPS, so `failures` is a count within the window rather
        # than a number that only ever grows. Left as a monotonic counter, an
        # outage from an hour ago still counted toward today's threshold and the
        # breaker could re-open on a single fresh failure with no live evidence
        # behind it.
        self._fail_events: list[float] = []
        # Symbols the provider answered about and had nothing for. Visible in
        # health, deliberately excluded from the open/close decision.
        self._no_data_keys: dict[str, float] = {}
        self._anon = 0
        # Only ONE probe is admitted while HALF_OPEN. Without this every caller
        # that arrives during the probe window is let through, and the first of
        # them to fail re-opens -- so recovery was a coin flip weighted by
        # whichever key happened to arrive first.
        self._probing = False

        # Observability. provider_health() returned `circuit_state` and nothing
        # else, so a breaker that was OPEN could not say what opened it. That is
        # the difference between diagnosing this in five minutes and inferring
        # it from source.
        self.last_error: str | None = None
        self.last_error_key: str | None = None
        self.last_error_at: float | None = None
        self.last_success_at: float | None = None
        self.opened_count = 0

    # -- internals (call with the lock held) -------------------------------- #
    def _prune(self) -> None:
        cut = self._clock() - self.window
        for d in (self._fail_keys, self._ok_keys, self._no_data_keys):
            for k in [k for k, ts in d.items() if ts < cut]:
                d.pop(k, None)
        if self._fail_events and self._fail_events[0] < cut:
            self._fail_events = [ts for ts in self._fail_events if ts >= cut]
        self.failures = len(self._fail_events)

    def _should_open(self) -> bool:
        nf, nok = len(self._fail_keys), len(self._ok_keys)
        if nf >= self.failure_threshold and nf > nok:
            return True                                  # broad outage
        return self.failures >= self.failure_threshold and nok == 0   # blackout

    # -- api ---------------------------------------------------------------- #
    def allow(self) -> bool:
        with self._lock:
            if self.state == "OPEN":
                if self._clock() - self.opened_at >= self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    self._probing = True
                    # The probe IS the new evidence, so the old evidence goes.
                    # Carried across the transition, the failures that opened
                    # the circuit were still sitting there when the probe
                    # reported back -- so a probe whose answer was merely "this
                    # symbol is empty" re-opened a provider that had recovered,
                    # on the strength of failures from before the outage was
                    # being retested at all. A failed probe still re-opens; it
                    # just has to do it on its own evidence.
                    self._fail_events.clear()
                    self._fail_keys.clear()
                    self.failures = 0
                    return True
                return False
            if self.state == "HALF_OPEN":
                # A probe is already out. Everyone else waits rather than
                # piling onto a provider we are not yet confident about.
                if self._probing:
                    return False
                self._probing = True
            return True

    def record_success(self, key: str | None = None) -> None:
        with self._lock:
            self._prune()
            self.last_success_at = self._clock()
            self._ok_keys[key if key is not None else "__anon_ok"] = self.last_success_at
            if key is not None:
                self._fail_keys.pop(key, None)
            # `failures` has always meant CONSECUTIVE failures, so a success
            # clears it. The per-key evidence above is not cleared wholesale:
            # one symbol working says nothing about another that is still 404ing.
            self._fail_events.clear()
            self.failures = 0
            self.state = "CLOSED"
            self._probing = False

    @staticmethod
    def is_no_data(error: BaseException | None) -> bool:
        """Did the provider ANSWER and tell us this symbol has nothing?

        `ValueError` is what our own adapters raise once a response has been
        received and parsed -- "no history for 'JJN'", "insufficient history",
        "unknown symbol". The provider is working perfectly; the symbol is
        empty. That is a fact about the ticker, and it must never count toward
        declaring the provider down, however many tickers it happens to.

        This is the distinction the original counter did not have, and it is the
        whole bug. A sandbox scan against live Yahoo reproduced it exactly:
        four thin commodity ETNs (JJN among them) raised "insufficient history",
        the tier called that four failures, and it went OPEN on a provider that
        had just served twenty other tickers without complaint.

        Transport failures -- OSError, URLError, timeouts, HTTP 429 -- are the
        opposite and still count for everything.
        """
        return isinstance(error, ValueError)

    def record_failure(self, key: str | None = None, error: BaseException | None = None) -> None:
        with self._lock:
            now = self._clock()
            self._prune()
            if key is None:
                # Preserves the original semantics for callers that do not name
                # a key: every anonymous failure is its own key.
                key = f"__anon_{self._anon}"
                self._anon += 1
            no_data = self.is_no_data(error)
            if no_data:
                # Recorded so it is VISIBLE in health -- an empty symbol is
                # still worth seeing -- but kept out of the evidence that opens
                # the circuit.
                self._no_data_keys[key] = now
            else:
                self._fail_keys[key] = now
                self._fail_events.append(now)
                self.failures = len(self._fail_events)
            if error is not None:
                self.last_error = f"{type(error).__name__}: {error}"[:300]
                self.last_error_key = key
                self.last_error_at = now

            was_probing = self.state == "HALF_OPEN" and not no_data
            self._probing = False
            if was_probing or self._should_open():
                if self.state != "OPEN":
                    self.opened_count += 1
                self.state = "OPEN"
                self.opened_at = now

    def snapshot(self) -> dict:
        """What is wrong, not just that something is."""
        with self._lock:
            self._prune()
            now = self._clock()
            return {
                "circuit_state": self.state,
                "consecutive_failures": self.failures,
                "failing_keys": sorted(self._fail_keys)[:10],
                "failing_key_count": len(self._fail_keys),
                # Named separately, because "these tickers are empty" and "the
                # provider is down" are the two things this breaker used to
                # confuse, and the health endpoint is where you find that out.
                "no_data_keys": sorted(self._no_data_keys)[:10],
                "no_data_key_count": len(self._no_data_keys),
                "succeeding_key_count": len(self._ok_keys),
                "last_error": self.last_error,
                "last_error_key": self.last_error_key,
                # `is not None`, not truthiness: with an injected clock the very
                # first success lands at t=0.0, which is falsy, and the field
                # would read "never succeeded" for the one case that did.
                "seconds_since_last_error": (round(now - self.last_error_at, 1)
                                             if self.last_error_at is not None else None),
                "seconds_since_last_success": (round(now - self.last_success_at, 1)
                                               if self.last_success_at is not None else None),
                "opened_count": self.opened_count,
            }


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
    """Fresh for `ttl`, then kept for `stale_ttl` longer as emergency stock.

    The second window is what keeps the Today screen alive through a provider
    wobble. Daily closes go stale slowly -- a series fetched forty minutes ago
    is byte-identical to the one you would fetch now -- so throwing it away the
    instant it expires and then failing the fetch means the app has the answer
    in memory and shows you nothing. `get_stale` is only ever read on the error
    path, and the caller is told it was stale so it can say so.
    """

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic,
                 stale_ttl: float = 0.0) -> None:
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self._clock = clock
        self._store: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._store.get(key, _MISSING)
            if item is _MISSING:
                return _MISSING
            value, expires, hard = item
            if self._clock() >= expires:
                # Kept, not dropped, until the hard limit -- see get_stale.
                if self._clock() >= hard:
                    self._store.pop(key, None)
                return _MISSING
            return value

    def get_stale(self, key):
        """Expired but still usable. Error path only."""
        with self._lock:
            item = self._store.get(key, _MISSING)
            if item is _MISSING:
                return _MISSING
            value, _expires, hard = item
            if self._clock() >= hard:
                self._store.pop(key, None)
                return _MISSING
            return value

    def set(self, key, value) -> None:
        with self._lock:
            now = self._clock()
            self._store[key] = (value, now + self.ttl, now + self.ttl + self.stale_ttl)


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
                 single_flight: bool = True, failure_cooloff: float = 0.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.breaker = breaker
        self.bucket = bucket
        self.cache = cache
        self.attempts = attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self.single_flight = single_flight
        # How long a key's failure is remembered so that followers waiting on it
        # inherit the error rather than each launching their own attempt. Zero
        # keeps the original stampede, which some tests deliberately rely on.
        self.failure_cooloff = failure_cooloff
        self._now = clock
        # key -> Event, signalled when the leader for that key finishes.
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self._recent_failure: dict[str, tuple[float, BaseException]] = {}
        self._stale_lock = threading.Lock()
        # Every stale read, lifetime. Kept for the health page and the smokes.
        self.stale_served = 0
        # PR 153: only the stale reads a FAILURE forced, and when the last one
        # was. Today warned on `stale_served > 0`, a number that only grows, so
        # one throttled read after a deploy kept "history_stale" up until the
        # next deploy. A rate-limit pause served from stock is the cache doing
        # its job (identical daily bars) and is not news.
        self.stale_served_on_failure = 0
        self.last_stale_on_failure_at: float | None = None

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

    def cached(self, key: str):
        """A fresh cached value, or None. Read-only; never fetches."""
        if self.cache is None:
            return None
        value = self.cache.get(key)
        return None if value is _MISSING else value

    def _serve_stale(self, key: str, *, failure: bool = True):
        """Last resort before raising: yesterday's answer beats no answer.

        `failure=False` is the rate-limit path: nothing failed, the bucket was
        empty. Counted, but never a warning (PR 153).
        """
        if self.cache is None:
            return _MISSING
        value = self.cache.get_stale(key)
        if value is not _MISSING:
            with self._stale_lock:
                self.stale_served += 1
                if failure:
                    self.stale_served_on_failure += 1
                    self.last_stale_on_failure_at = self._now()
        return value

    def stale_snapshot(self, window_sec: float) -> dict:
        """Stale reads, and whether a FAILURE forced one within `window_sec`."""
        with self._stale_lock:
            at = self.last_stale_on_failure_at
            ago = None if at is None else round(self._now() - at, 1)
            return {"stale_served": self.stale_served,
                    "stale_served_on_failure": self.stale_served_on_failure,
                    "seconds_since_stale_on_failure": ago,
                    "stale_warning": ago is not None and ago <= window_sec}

    def _fetch(self, key: str, fn: Callable):
        if self.bucket is not None and not self.bucket.take():
            stale = self._serve_stale(key, failure=False)
            if stale is not _MISSING:
                return stale
            raise RateLimitedError(f"rate limit exceeded for {key}")
        if self.breaker is not None and not self.breaker.allow():
            stale = self._serve_stale(key)
            if stale is not _MISSING:
                return stale
            raise CircuitOpenError(f"circuit open for {key}")
        try:
            value = retry(fn, self.attempts, self.base_delay, self.sleep)
        except Exception as exc:
            if self.breaker is not None:
                self.breaker.record_failure(key, exc)
            # Remember the failure briefly so the followers queued behind this
            # key inherit it instead of each mounting their own attempt.
            with self._stale_lock:
                self._recent_failure[key] = (self._now(), exc)
            stale = self._serve_stale(key)
            if stale is not _MISSING:
                return stale
            raise
        if self.breaker is not None:
            self.breaker.record_success(key)
        with self._stale_lock:
            self._recent_failure.pop(key, None)
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

        # Follower: wait for the leader, then read what it stored.
        waiting.wait()
        cached = self.cache.get(key)
        if cached is not _MISSING:
            return cached

        # The leader failed. Followers used to each mount their own attempt --
        # which is how ONE dead key recorded eight failures from eight waiters
        # and tripped a threshold-five breaker by itself. With a cool-off they
        # inherit the leader's error instead: one bad key, one failure recorded,
        # one request sent upstream. The breaker still opens on a genuinely dead
        # provider, because then every key fails, not just this one.
        if self.failure_cooloff > 0:
            with self._stale_lock:
                recent = self._recent_failure.get(key)
                fresh = recent is not None and (self._now() - recent[0]) < self.failure_cooloff
            if fresh:
                stale = self._serve_stale(key)
                if stale is not _MISSING:
                    return stale
                raise recent[1]
        return self._fetch(key, fn)
