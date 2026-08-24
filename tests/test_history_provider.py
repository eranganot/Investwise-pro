"""Why `history` sat OPEN for a week while `market` served quotes the whole time.

Both tiers are the same Yahoo provider hitting the same
`/v8/finance/chart/{sym}` endpoint, one query parameter apart. The only
difference is WHICH KEYS each one asks for:

  market   quotes for tickers you HOLD  -- four names, all real
  history  the screener's CANDIDATE set -- 25 names per scan, uncurated

A plain `failures += 1` against a threshold of five cannot tell "five of these
symbols have no data" from "the provider is down", so a handful of unfetchable
candidates took history dark for every symbol, the four you own included.

These tests fix the semantics, and they are written so each one fails against
the old code for a different reason.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.providers.resilience import (  # noqa: E402
    CircuitBreaker, ResilienceTier, TTLCache,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, d):
        self.t += d


# --------------------------------------------------------------------------- #
# the bug: one bad key took down the keyspace
# --------------------------------------------------------------------------- #
def test_a_dead_symbol_does_not_take_the_tier_down_with_it():
    """THE PRODUCTION BUG, in five lines.

    Five candidate symbols Yahoo will not serve used to be indistinguishable
    from a dead provider. Meanwhile V, MSFT, SCHD and the benchmark were
    fetching perfectly -- and got cut off anyway.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, clock=clk)

    # Twenty good names are being served happily.
    for tk in [f"GOOD{i}" for i in range(20)]:
        cb.record_success(f"hist:{tk}")

    # Five junk symbols 404, repeatedly, across a scan.
    for _ in range(4):
        for tk in ("JUNK1", "JUNK2", "JUNK3", "JUNK4", "JUNK5"):
            cb.record_failure(f"hist:{tk}", ValueError("no history"))

    assert cb.state == "CLOSED", (
        "5 unfetchable symbols closed the circuit on 20 that work -- this is "
        "exactly how the history tier went dark while quotes kept flowing")
    assert cb.allow() is True


def test_b_a_provider_that_serves_nobody_still_opens():
    """The other half. A breaker that never opens is not a breaker.

    When failures outnumber successes across DISTINCT keys, that is the
    provider, not the symbols.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, clock=clk)
    for i in range(6):
        cb.record_failure(f"hist:TK{i}", OSError("429 Too Many Requests"))
    assert cb.state == "OPEN"
    assert cb.allow() is False


def test_c_total_blackout_opens_even_on_a_single_key():
    """If the only key anyone asks for is dead, the provider is dead to them.

    This is the case the original `failures >= threshold` counter got right,
    and it has to keep working -- otherwise the fix for one bug is a new one.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0, clock=clk)
    for _ in range(3):
        cb.record_failure("hist:ONLY", OSError("connection reset"))
    assert cb.state == "OPEN"


def test_d_old_failures_stop_counting():
    """Evidence has a shelf life. Yesterday's outage is not a fact about now."""
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, clock=clk,
                        window=100.0)
    for i in range(4):
        cb.record_failure(f"hist:TK{i}", OSError("boom"))
    clk.tick(200.0)                       # a long quiet spell
    cb.record_failure("hist:TK9", OSError("boom"))
    assert cb.state == "CLOSED", "four expired failures still counted toward the threshold"


def test_d2_an_empty_symbol_is_not_a_provider_failure():
    """CAUGHT BY REPRODUCING IT, not by reasoning about it.

    A sandbox scan against live Yahoo went OPEN on
    `ValueError: insufficient history for 'JJN'` -- JJN being a thin commodity
    ETN in the screener's own idea list, alongside NIB, COW and JO. Four such
    names, and the tier declared the provider down seconds after it had served
    twenty other tickers perfectly.

    The provider was never down. It answered every time and said "this ticker
    has nothing", which is a fact about the ticker. No number of empty symbols
    is evidence about the provider's health, so they cannot open the circuit at
    any threshold.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, clock=clk)
    for tk in ("JJN", "NIB", "COW", "JO", "CANE", "SOYB"):
        cb.record_failure(f"hist:{tk}", ValueError(f"insufficient history for '{tk}'"))

    assert cb.state == "CLOSED", "six empty symbols closed a healthy provider"
    assert cb.allow() is True
    snap = cb.snapshot()
    assert snap["no_data_key_count"] == 6, "they must still be VISIBLE, just not fatal"
    assert snap["failing_key_count"] == 0
    assert "JJN" in " ".join(snap["no_data_keys"])


def test_d3_transport_failures_still_count():
    """The distinction has to cut both ways or it is just a disabled breaker.

    A 429 or a reset socket is the provider, not the symbol.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0, clock=clk)
    for tk in ("AAPL", "MSFT", "GOOGL"):
        cb.record_failure(f"hist:{tk}", OSError("HTTP Error 429: Too Many Requests"))
    assert cb.state == "OPEN"


def test_d4_a_no_data_probe_does_not_reopen_the_circuit():
    """Recovery must not be decided by an empty symbol either.

    The HALF_OPEN probe is whichever key arrives first, and on the history tier
    that is often a candidate ticker. If a "no history" answer re-opened the
    circuit, a genuinely recovered provider could never be recognised.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, clock=clk)
    cb.record_failure("hist:X", OSError("down"))
    assert cb.state == "OPEN"
    clk.tick(31.0)
    assert cb.allow() is True and cb.state == "HALF_OPEN"
    cb.record_failure("hist:JJN", ValueError("insufficient history for 'JJN'"))
    assert cb.state != "OPEN", "an empty symbol re-opened a recovering circuit"


# --------------------------------------------------------------------------- #
# recovery was a coin flip
# --------------------------------------------------------------------------- #
def test_e_only_one_probe_is_admitted_while_half_open():
    """HALF_OPEN used to admit everyone.

    `allow()` only special-cases OPEN, so once the state flipped every caller
    sailed through -- and `record_failure` re-opened on the FIRST of them to
    fail. Recovery was therefore decided by whichever arbitrary key arrived
    first, which on the history tier is a candidate symbol that may well be the
    broken one. A 30-second breaker stayed open for days that way.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, clock=clk)
    cb.record_failure("hist:X", OSError("boom"))
    assert cb.state == "OPEN"

    clk.tick(31.0)
    assert cb.allow() is True, "the probe should be let through"
    assert cb.state == "HALF_OPEN"
    assert cb.allow() is False, "a second caller piled onto a provider we are unsure about"
    assert cb.allow() is False

    cb.record_success("hist:X")
    assert cb.state == "CLOSED"
    assert cb.allow() is True


def test_f_a_failed_probe_reopens_rather_than_closing():
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, clock=clk)
    cb.record_failure("hist:X", OSError("boom"))
    clk.tick(31.0)
    assert cb.allow() is True
    cb.record_failure("hist:X", OSError("still down"))
    assert cb.state == "OPEN"
    assert cb.allow() is False


# --------------------------------------------------------------------------- #
# stale-if-error: the change the user actually sees
# --------------------------------------------------------------------------- #
def test_g_an_expired_series_beats_no_series():
    """Daily closes do not change between fetches.

    Dropping an hour-old series the instant it expires and then failing the
    refetch means the app HAS the answer in memory and shows you nothing. For a
    200-day trend, the series from forty minutes ago is the same series.
    """
    clk = Clock()
    tier = ResilienceTier(cache=TTLCache(ttl=10.0, clock=clk, stale_ttl=1000.0),
                          breaker=None, bucket=None, attempts=1, clock=clk)

    assert tier.call("hist:V", lambda: [1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]
    clk.tick(50.0)                                   # fresh window gone

    def dead():
        raise OSError("provider down")

    assert tier.call("hist:V", dead) == [1.0, 2.0, 3.0], "stale stock was not served"
    assert tier.stale_served == 1, "serving stale must be counted, or it cannot be reported"


def test_h_stale_stock_expires_too():
    """"Old" is not "forever". Past the hard limit the failure is the answer."""
    clk = Clock()
    tier = ResilienceTier(cache=TTLCache(ttl=10.0, clock=clk, stale_ttl=100.0),
                          breaker=None, bucket=None, attempts=1, clock=clk)
    tier.call("hist:V", lambda: [1.0])
    clk.tick(5000.0)
    with pytest.raises(OSError):
        tier.call("hist:V", lambda: (_ for _ in ()).throw(OSError("down")))


def test_i_an_open_circuit_still_serves_stale():
    """The point of keeping stock is that it survives the outage that needs it."""
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=999.0, clock=clk)
    tier = ResilienceTier(cache=TTLCache(ttl=10.0, clock=clk, stale_ttl=1000.0),
                          breaker=cb, bucket=None, attempts=1, clock=clk)
    tier.call("hist:V", lambda: [1.0, 2.0])
    clk.tick(50.0)
    # Two distinct dead keys against one that worked: failures outnumber
    # successes, so this is the provider rather than the symbols, and it opens.
    for dead_key in ("hist:DEAD1", "hist:DEAD2"):
        with pytest.raises(OSError):
            tier.call(dead_key, lambda: (_ for _ in ()).throw(OSError("down")))
    assert cb.state == "OPEN"

    # V's series is expired and the circuit is open -- and it still answers.
    assert tier.call("hist:V", lambda: [9.0]) == [1.0, 2.0]


def test_j_quotes_deliberately_get_no_stale_window():
    """A stale PRICE misvalues the book. That is the one failure this cannot have.

    `stale_ttl` defaults to zero, so the market and fx tiers -- which are built
    without one -- raise rather than hand back yesterday's price.
    """
    clk = Clock()
    tier = ResilienceTier(cache=TTLCache(ttl=10.0, clock=clk), breaker=None,
                          bucket=None, attempts=1, clock=clk)
    tier.call("quote:V", lambda: 371.04)
    clk.tick(50.0)
    with pytest.raises(OSError):
        tier.call("quote:V", lambda: (_ for _ in ()).throw(OSError("down")))


# --------------------------------------------------------------------------- #
# the follower stampede
# --------------------------------------------------------------------------- #
def test_k_followers_inherit_a_failure_instead_of_repeating_it():
    """Eight waiters on one dead key used to record eight failures.

    That alone tripped a threshold-five breaker -- one bad symbol, no other
    provider trouble required.
    """
    import threading

    clk = Clock()
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=999.0, clock=clk)
    tier = ResilienceTier(cache=TTLCache(ttl=60.0), breaker=cb, bucket=None,
                          attempts=1, failure_cooloff=30.0, clock=clk)

    calls = []
    lock = threading.Lock()

    def dead():
        with lock:
            calls.append(1)
        import time as _t
        _t.sleep(0.05)
        raise OSError("no history for JUNK")

    threads = [threading.Thread(target=lambda: _swallow(tier, dead)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"the dead key was fetched {len(calls)} times, not once"
    assert cb.state == "CLOSED", "one bad symbol tripped the breaker by itself"


def _swallow(tier, fn):
    try:
        tier.call("hist:JUNK", fn)
    except Exception:  # noqa: BLE001, S110
        pass


# --------------------------------------------------------------------------- #
# one fetch per upstream request
# --------------------------------------------------------------------------- #
def test_l_history_windows_that_produce_one_upstream_call_share_one_cache_entry():
    """130, 200 and 252 days are the SAME Yahoo request (`range=1y`).

    They were three cache keys, so one ticker cost three fetches of identical
    bytes. Across eight call sites asking 130/200/252/2600, a 25-name scan sent
    up to 100 requests where 50 would do -- which matters a great deal if the
    real cause of the outage is being rate-limited.
    """
    from app.providers.registry import _history_bucket

    assert _history_bucket(130) == _history_bucket(200) == _history_bucket(252) == 260
    assert _history_bucket(500) == 520
    assert _history_bucket(2600) == 2600
    assert _history_bucket(99999) == 2600, "never ask for more than the provider serves daily"


def test_m_a_longer_cached_series_answers_a_shorter_request(monkeypatch):
    """A backtest that just pulled ten years makes the 200-day trend free."""
    from app.providers import registry

    calls = []

    class _P:
        name = "fake"

        def get_history(self, ticker, days=252):
            calls.append((ticker, days))
            return [(f"d{i}", float(i)) for i in range(2600)]

    registry._tiers.cache_clear()
    monkeypatch.setattr(registry, "market_provider", lambda: _P())
    try:
        ten_year = registry.guarded_history("V", 2600)
        assert len(ten_year) == 2600
        two_hundred = registry.guarded_history("V", 200)
        assert len(two_hundred) == 200
        assert two_hundred == ten_year[-200:], "the slice must come off the same series"
        assert len(calls) == 1, f"expected one upstream fetch, got {calls}"
    finally:
        registry._tiers.cache_clear()


# --------------------------------------------------------------------------- #
# the breaker can now say what is wrong with it
# --------------------------------------------------------------------------- #
def test_n_health_reports_the_cause_not_just_the_state():
    """`{"circuit_state": "OPEN"}` and nothing else.

    That is what the endpoint returned while history was down, so it could not
    say which key failed, what the error was, or when anything last worked --
    the difference between a five-minute diagnosis and reading the source.
    """
    clk = Clock()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, clock=clk)
    cb.record_success("hist:V")
    cb.record_failure("hist:JUNK", OSError("HTTP Error 429: Too Many Requests"))

    snap = cb.snapshot()
    assert snap["circuit_state"] == "CLOSED"
    assert snap["last_error_key"] == "hist:JUNK"
    assert "429" in snap["last_error"]
    assert snap["failing_keys"] == ["hist:JUNK"]
    assert snap["succeeding_key_count"] == 1
    assert snap["seconds_since_last_success"] is not None


def test_o_provider_health_keeps_the_field_the_smoke_scripts_read():
    """`circuit_state` stays at the top of each tier's dict.

    smoke-cards.ps1 and smoke-p0.ps1 both key off it. Enriching a payload must
    not quietly break the scripts whose whole job is noticing breakage.
    """
    from app.providers.registry import provider_health

    ph = provider_health()
    for tier in ("market", "fx", "history", "fundamentals"):
        assert "circuit_state" in ph[tier], f"{tier} lost circuit_state"
    assert ph["market_data_provider"]
    assert ph["fx_provider"]
