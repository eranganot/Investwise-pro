"""Resilience tier tests (Section AE) - breaker / bucket / cache / retry."""
import pytest

from app.providers.resilience import (
    CircuitBreaker, CircuitOpenError, RateLimitedError, ResilienceTier,
    TokenBucket, TTLCache, retry,
)


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, d): self.t += d


def test_circuit_breaker_opens_and_recovers():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0, clock=clk)
    cb.record_failure(); cb.record_failure()
    assert cb.state == "OPEN" and cb.allow() is False
    clk.advance(10.0)
    assert cb.allow() is True and cb.state == "HALF_OPEN"
    cb.record_success()
    assert cb.state == "CLOSED"


def test_token_bucket_limits_and_refills():
    clk = Clock()
    b = TokenBucket(capacity=2, refill_per_sec=1.0, clock=clk)
    assert b.take() and b.take()
    assert b.take() is False
    clk.advance(1.0)
    assert b.take() is True


def test_ttl_cache_expires():
    clk = Clock()
    c = TTLCache(ttl=5.0, clock=clk)
    c.set("k", 123)
    assert c.get("k") == 123
    clk.advance(5.0)
    from app.providers.resilience import _MISSING
    assert c.get("k") is _MISSING


def test_retry_succeeds_then_gives_up():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"
    assert retry(flaky, attempts=3, base_delay=0.0, sleep=lambda d: None) == "ok"

    def always_fail():
        raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        retry(always_fail, attempts=2, base_delay=0.0, sleep=lambda d: None)


def test_tier_caches_result():
    tier = ResilienceTier(cache=TTLCache(ttl=100.0, clock=Clock()))
    assert tier.call("k", lambda: 1) == 1
    assert tier.call("k", lambda: 2) == 1  # served from cache, fn not used


def test_tier_rate_limits():
    tier = ResilienceTier(bucket=TokenBucket(capacity=0, refill_per_sec=0, clock=Clock()))
    with pytest.raises(RateLimitedError):
        tier.call("k", lambda: 1)


def test_tier_opens_circuit_after_failures():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0, clock=clk)
    tier = ResilienceTier(breaker=cb, attempts=1, sleep=lambda d: None)
    with pytest.raises(RuntimeError):
        tier.call("k", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cb.state == "OPEN"
    with pytest.raises(CircuitOpenError):
        tier.call("k", lambda: 1)
