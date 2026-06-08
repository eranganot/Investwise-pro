"""Lag Engine tests (Section 4.2) - divergence, depth priority, noise floor."""
import pytest

from app.engines.lag_engine import LagEngine
from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, Market

A = pytest.approx


def obs(ticker, depth, spot, listing, market=Market.NYSE, vol=None):
    return LagObservation(
        ticker=ticker, market=market, depth=depth,
        spot_price=spot, listing_price=listing, volatility_pct=vol,
    )


def test_divergence_pct():
    assert LagEngine.divergence_pct(100, 108.2) == A(8.2)
    assert LagEngine.divergence_pct(100, 95) == A(-5.0)


def test_noise_floor_filters_small_divergence():
    eng = LagEngine()
    assert eng.detect(obs("NOISE", 1, 100, 100.6)) is None      # 0.6% < 2%
    assert eng.detect(obs("REAL", 1, 100, 103)) is not None     # 3% >= 2%


def test_detect_passes_through_fields():
    s = LagEngine().detect(obs("TEVA", 3, 100, 108.2, vol=12))
    assert s is not None
    assert s.depth == 3
    assert s.divergence_pct == A(8.2)
    assert s.volatility_pct == 12
    assert "backbone" in s.trigger


def test_depth3_backbone_outranks_larger_depth1_hype():
    eng = LagEngine()
    # Depth 1 has a BIGGER raw divergence, but Depth 3 must rank first (ALPHA).
    signals = eng.scan([
        obs("HYPE", 1, 100, 112),     # 12% * 1.0 = 12.0
        obs("BACKBONE", 3, 100, 108),  # 8%  * 2.0 = 16.0
    ])
    assert [s.ticker for s in signals] == ["BACKBONE", "HYPE"]


def test_scan_filters_then_sorts():
    eng = LagEngine()
    signals = eng.scan([
        obs("NOISE", 1, 100, 100.5),   # filtered
        obs("A", 1, 100, 103),         # 3.0
        obs("B", 3, 100, 104),         # 4*2 = 8.0
    ])
    assert [s.ticker for s in signals] == ["B", "A"]


def test_backbone_vs_hype_ratio():
    eng = LagEngine()
    observations = [obs("BB", 3, 100, 108), obs("H1", 1, 100, 112), obs("H2", 1, 100, 104)]
    # backbone 8 / hype (12+4)=16 -> 0.5
    assert eng.backbone_vs_hype(observations) == A(0.5)


def test_backbone_vs_hype_none_without_hype_baseline():
    eng = LagEngine()
    assert eng.backbone_vs_hype([obs("BB", 3, 100, 108)]) is None


def test_min_divergence_is_config_driven():
    from app.core.config import Settings
    eng = LagEngine(Settings(lag_min_divergence_pct=10.0))
    assert eng.detect(obs("X", 1, 100, 105)) is None   # 5% < 10% floor now
