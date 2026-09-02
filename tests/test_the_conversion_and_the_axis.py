"""PR 30 - three things the app knew and did not say.

Each of these is a value the app already had and threw away before anyone could
read it. None of them is a new feature.

1. `fx_rate` returns the rate alone, so the 1.0 that means "no idea" reaches NAV
   looking exactly like a genuine peg.
2. The performance chart was passed `r.dates` and drew no x axis.
3. Pinning VAPID to the wrong keypair silently unreachable-s every subscriber.
"""
from __future__ import annotations

import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


# ---------------------------------------------------------------------------
# 1. An unresolved FX rate is 1.0, and NAV must say so.
# ---------------------------------------------------------------------------

def test_fx_rate_with_status_still_reports_unresolved_as_1_point_0():
    """Pinning the premise. `fx.py` returns 1.0 with the status "unresolved"
    rather than raising, because thirteen call sites do arithmetic on the
    result. That is a deliberate choice and this PR does not change it -- it
    changes who gets told."""
    from app.services import fx

    def _boom(*_a, **_k):
        raise RuntimeError("provider down")

    real = fx._LAST_GOOD_RATE.copy()
    fx._LAST_GOOD_RATE.clear()
    try:
        import app.providers.registry as reg
        keep = reg.guarded_fx
        reg.guarded_fx = _boom
        try:
            rate, status = fx.fx_rate_with_status("ZZZ", "ILS")
        finally:
            reg.guarded_fx = keep
    finally:
        fx._LAST_GOOD_RATE.clear()
        fx._LAST_GOOD_RATE.update(real)

    assert rate == 1.0
    assert status == "unresolved"
    # ...and the bare wrapper gives that 1.0 away with no way to tell.
    assert fx.fx_rate.__doc__ and "rate alone" in fx.fx_rate.__doc__


def test_the_portfolio_route_reads_the_status_not_just_the_rate():
    """The defect, stated as a property of the source.

    `/portfolio` is the one place that BUILDS NAV out of `quantity x price x
    rate`. Reading `fx_rate` there means an unresolved conversion is counted at
    par and nothing downstream can tell. A grep is the honest test here: the
    alternative is standing up a provider outage against a live route, and this
    is the fact that actually matters.
    """
    src = (APP / "api" / "routes" / "intake.py").read_text(encoding="utf-8")
    body = src[src.index("async def get_portfolio"):src.index("class AddHoldingRequest")]

    assert "fx_rate_with_status(" in body, (
        "the NAV route is reading the bare rate again; an unresolved conversion "
        "is back to being invisible")
    assert re.search(r"\brate\s*=\s*fx_rate\(", body) is None, (
        "a bare fx_rate() call is back on the NAV path")


def test_nav_reports_which_part_rests_on_a_conversion_that_failed():
    """The payload contract, mirroring `stale_positions`.

    NAV still INCLUDES the holding -- writing it down is not the app's call --
    and the reader is told what the total rests on. Split from `stale` on
    purpose: a stale rate is real and a few hours old; an unresolved one is not
    a rate at all, and the two need different responses.
    """
    src = (APP / "api" / "routes" / "intake.py").read_text(encoding="utf-8")
    for key in ("fx_unresolved_positions", "fx_unresolved_value_ils",
                "fx_stale_positions", '"fx_status": fx_status'):
        assert key in src, f"the portfolio payload no longer carries {key}"

    # The roll-up must select on the status, not on the rate being 1.0 --
    # testing `rate == 1.0` is exactly the broken signal that made the status
    # field necessary (see fx.fx_rate_with_status' docstring).
    assert 'o.get("fx_status") == "unresolved"' in src
    assert "rate == 1.0" not in src


# ---------------------------------------------------------------------------
# 2. The dates were always there; only the ticks were off.
# ---------------------------------------------------------------------------

INDEX = APP / "static_app" / "index.html"


def _perf_chart_call() -> str:
    html = INDEX.read_text(encoding="utf-8")
    i = html.index('drawChart("ch_perf"')
    return html[i:html.index("\n", i)]


def test_the_performance_chart_draws_its_date_axis():
    call = _perf_chart_call()
    assert "labels:r.dates" in call, "the dates stopped being passed as labels"
    assert "ticks:{display:true" in call, (
        "the x ticks are switched off again; the chart has no date axis")
    # Scoped to the TICKS. A bare `"display:false" not in call` also matches
    # `grid:{display:false}`, which is deliberate -- this chart has never drawn
    # gridlines and should not start. The first version of this assertion failed
    # on exactly that and would have had me "fixing" the wrong thing.
    assert "ticks:{display:false" not in call
    assert "grid:{display:false}" in call, "gridlines came back with the axis"


def test_the_axis_is_thinned_so_a_ten_year_range_stays_legible():
    """`display:true` alone is not the fix. MAX is ~2600 daily labels and
    Chart.js will draw every one of them into a smear. The same canvas has to
    read at 1W and at MAX."""
    call = _perf_chart_call()
    assert "autoSkip:true" in call
    assert "maxTicksLimit:6" in call
    assert "maxRotation:0" in call


def test_it_matches_the_tick_config_the_file_already_uses_elsewhere():
    """There was already a thinned axis in this file. Two charts on one screen
    with different tick behaviour is the kind of drift that gets noticed and
    never fixed."""
    html = INDEX.read_text(encoding="utf-8")
    others = [ln for ln in html.splitlines()
              if "maxTicksLimit:6" in ln and 'drawChart("ch_perf"' not in ln]
    assert others, "the house tick config vanished; re-check both charts together"


# ---------------------------------------------------------------------------
# 3. Pinning the wrong VAPID pair is silent.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_pinned_key_that_is_not_the_db_pair_is_reported(caplog):
    """The failure this catches has already been documented in this file, one
    layer downstream: `DEAD_CODES` includes 403 precisely because "the keypair
    no longer matches the one the subscription was created with", and the
    comment there calls the un-pruned version "a permanent, completely silent
    outage". This names the cause at the moment of the pin, before any 403s.
    """
    import logging

    from app.core.config import get_settings
    from app.services import push_service as ps

    class _FakeSession:
        async def get(self, _model, key):
            return type("R", (), {"value": "DB_PUBLIC_KEY"})() if key == ps._KV_PUB else None

    s = get_settings()
    keep_pub, keep_priv = s.vapid_public_key, s.vapid_private_key
    object.__setattr__(s, "vapid_public_key", "A_DIFFERENT_PUBLIC_KEY")
    object.__setattr__(s, "vapid_private_key", "whatever")
    try:
        with caplog.at_level(logging.ERROR, logger="investwise.push"):
            pub, _priv, _sub = await ps.get_vapid(_FakeSession())
    finally:
        object.__setattr__(s, "vapid_public_key", keep_pub)
        object.__setattr__(s, "vapid_private_key", keep_priv)

    # The env still wins -- this reports, it does not override the operator.
    assert pub == "A_DIFFERENT_PUBLIC_KEY"
    assert any("VAPID MISMATCH" in r.message for r in caplog.records), (
        "pinning a keypair the database never minted said nothing at all")


@pytest.mark.asyncio
async def test_a_pin_that_matches_the_db_says_nothing(caplog):
    """The mirror. A correct pin is the goal, not an event."""
    import logging

    from app.core.config import get_settings
    from app.services import push_service as ps

    class _FakeSession:
        async def get(self, _model, key):
            return type("R", (), {"value": "SAME_KEY"})() if key == ps._KV_PUB else None

    s = get_settings()
    keep_pub, keep_priv = s.vapid_public_key, s.vapid_private_key
    object.__setattr__(s, "vapid_public_key", "SAME_KEY")
    object.__setattr__(s, "vapid_private_key", "whatever")
    try:
        with caplog.at_level(logging.ERROR, logger="investwise.push"):
            await ps.get_vapid(_FakeSession())
    finally:
        object.__setattr__(s, "vapid_public_key", keep_pub)
        object.__setattr__(s, "vapid_private_key", keep_priv)

    assert not any("VAPID MISMATCH" in r.message for r in caplog.records)


def test_push_status_exposes_the_match_so_it_is_not_log_only():
    """A log line nobody greps is not a signal. `push/status` is what the smoke
    scripts read."""
    src = (APP / "services" / "push_service.py").read_text(encoding="utf-8")
    assert '"vapid_env_matches_db"' in src
    # None when nothing is pinned: "not pinned" and "pinned wrongly" are
    # different states and a bool cannot hold three.
    assert "(None if not _env_pub else not _mismatch)" in src
