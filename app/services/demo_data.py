"""Shared sample observations for demo/fallback feeds (decouples routes)."""
from __future__ import annotations

from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, Market

DEFAULT_OBSERVATIONS = [
    LagObservation(ticker="TEVA", market=Market.NYSE, depth=3, spot_price=100,
                   listing_price=108.2, expected_return_pct=10, volatility_pct=12,
                   action_type=ActionType.BUY),
    LagObservation(ticker="HYPE", market=Market.NYSE, depth=1, spot_price=100,
                   listing_price=112, expected_return_pct=15, volatility_pct=40,
                   action_type=ActionType.BUY),
    LagObservation(ticker="GOLD", market=Market.SPOT, depth=1, spot_price=100,
                   listing_price=103.1, expected_return_pct=6, volatility_pct=8,
                   action_type=ActionType.REBALANCE),
    LagObservation(ticker="NOISE", market=Market.TASE, depth=1, spot_price=100,
                   listing_price=100.6, action_type=ActionType.BUY),
]
