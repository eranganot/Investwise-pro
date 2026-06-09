"""The Position Lifecycle state machine (Section 1).

Each stage is its own immutable Pydantic model, and every stage (after the
first) embeds the *typed* previous stage as ``source``. Because the type of
``source`` is fixed, a stage physically cannot be constructed out of order:
e.g. building an ``OptimizedSignal`` from a ``DetectedSignal`` raises a
``ValidationError``. This enforces the spec rule "No stage can be skipped".
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.scoring import ConfidenceBreakdown, ImpactScores


class Stage(str, Enum):
    DETECTED = "DETECTED"
    VETTED = "VETTED"
    OPTIMIZED = "OPTIMIZED"
    RANKED = "RANKED"
    DISPLAYED = "DISPLAYED"
    VETOED = "VETOED"


class Market(str, Enum):
    TASE = "TASE"          # Tel Aviv
    NYSE = "NYSE"          # New York
    NASDAQ = "NASDAQ"      # US (tech-heavy)
    TSX = "TSX"            # Toronto
    B3 = "B3"              # Sao Paulo
    LSE = "LSE"            # London
    XETRA = "XETRA"        # Frankfurt / Germany
    EURONEXT = "EURONEXT"  # Paris / Amsterdam
    SIX = "SIX"            # Zurich / Switzerland
    JPX = "JPX"            # Tokyo
    HKEX = "HKEX"          # Hong Kong
    SSE = "SSE"            # Shanghai
    NSE = "NSE"            # India
    ASX = "ASX"            # Australia
    SPOT = "SPOT"          # Commodities / crypto spot
    OTHER = "OTHER"        # Any other venue


# Crude region/currency proxies from listing venue (used by the risk &
# currency-concentration vectors and the FX scenario layer). Unknown -> defaults.
MARKET_REGION = {
    "NYSE": "US", "NASDAQ": "US", "TSX": "North America", "B3": "LatAm",
    "TASE": "Israel", "LSE": "UK", "XETRA": "Europe", "EURONEXT": "Europe",
    "SIX": "Europe", "JPX": "Asia-Pacific", "HKEX": "Asia-Pacific",
    "SSE": "Asia-Pacific", "NSE": "Asia-Pacific", "ASX": "Asia-Pacific",
    "SPOT": "Global", "OTHER": "Global",
}
MARKET_CURRENCY = {
    "NYSE": "USD", "NASDAQ": "USD", "TSX": "CAD", "B3": "BRL",
    "TASE": "ILS", "LSE": "GBP", "XETRA": "EUR", "EURONEXT": "EUR",
    "SIX": "CHF", "JPX": "JPY", "HKEX": "HKD", "SSE": "CNY", "NSE": "INR",
    "ASX": "AUD", "SPOT": "USD", "OTHER": "USD",
}


class ActionType(str, Enum):
    BUY = "Buy"
    SELL = "Sell"
    REBALANCE = "Rebalance"
    TAX = "Tax"
    RISK = "Risk"


class _StageBase(BaseModel):
    # Stages are immutable once created -> safe to pass through the pipeline.
    model_config = ConfigDict(frozen=True)


class DetectedSignal(_StageBase):
    """Stage 1 - emitted by the Lag Engine."""
    stage: Literal[Stage.DETECTED] = Stage.DETECTED
    ticker: str
    market: Market
    action_type: ActionType
    trigger: str  # "Why now"
    depth: int = Field(ge=1, le=3)  # Lag depth 1-3
    divergence_pct: float
    notes: str = "Awaiting Data"
    # Economic context for the Tax Engine (optional; None => Awaiting Data).
    gross_gain_ils: Optional[float] = None
    prior_taxable_income_ils: float = 0.0
    loss_carry_forward_ils: float = 0.0
    # Risk context for the Risk Engine (annual %, optional; None => Awaiting Data).
    expected_return_pct: Optional[float] = None
    volatility_pct: Optional[float] = None
    liquidity_score: Optional[float] = None  # 0-100 liquidity health, optional


class VettedSignal(_StageBase):
    """Stage 2 - Risk Engine attaches stress metrics + veto."""
    stage: Literal[Stage.VETTED] = Stage.VETTED
    source: DetectedSignal
    probability_of_ruin: Optional[float] = None
    max_drawdown: Optional[float] = None
    volatility: Optional[float] = None
    veto_flag: bool = False
    risk_critique: str = "Awaiting Data"


class OptimizedSignal(_StageBase):
    """Stage 3 - Tax Engine attaches net-after-tax economics."""
    stage: Literal[Stage.OPTIMIZED] = Stage.OPTIMIZED
    source: VettedSignal
    net_gain_delta: Optional[float] = None
    actual_tax_cost: Optional[float] = None
    tax_saved: Optional[float] = None
    tax_deferred: Optional[float] = None


class RankedSignal(_StageBase):
    """Stage 4 - Decision Engine scores impact + confidence (Section Z)."""
    stage: Literal[Stage.RANKED] = Stage.RANKED
    source: OptimizedSignal
    impact_score: float = 0.0
    confidence: float = 0.0
    scores: ImpactScores
    confidence_breakdown: ConfidenceBreakdown
    complexity_label: str = "Moderate"
    complexity_factor: float = 1.5
    urgency: int = Field(default=1, ge=1, le=100)


class DisplayedItem(_StageBase):
    """Stage 5 - UX-formatted item shown in the Decision Feed."""
    stage: Literal[Stage.DISPLAYED] = Stage.DISPLAYED
    source: RankedSignal
    path: str  # "Growth" | "Bulletproof"
    title: str


class VetoedSignal(_StageBase):
    """Terminal branch - Adversary/Risk veto, never displayed."""
    stage: Literal[Stage.VETOED] = Stage.VETOED
    source: VettedSignal
    reason: str
