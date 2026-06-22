"""Resilient middle tier (Section AE): retries, rate limiting, cache, breaker.

Pure and clock-injectable so it unit-tests deterministically. Composed in one
`ResilienceTier.call(key, fn)` guard wrapped around every provider fetch.
"""
from __future__ import annotations

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

    def allow(self) -> bool:
        if self.state == "OPEN":
            if self._clock() - self.opened_at >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = "CLOSED"

    def record_failure(self) -> None:
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

    def take(self, n: float = 1.0) -> bool:
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

    def get(self, key):
        item = self._store.get(key, _MISSING)
        if item is _MISSING:
            return _MISSING
        value, expires = item
        if self._clock() >= expires:
            self._store.pop(key, None)
            return _MISSING
        return value

    def set(self, key, value) -> None:
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
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.breaker = breaker
        self.bucket = bucket
        self.cache = cache
        self.attempts = attempts
        self.base_delay = base_delay
        self.sleep = sleep

    def call(self, key: str, fn: Callable):
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not _MISSING:
                return cached
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
