"""Currency precision helpers (review C1).

All money math runs in Decimal; values are quantized to 2dp (ROUND_HALF_UP) at
the boundary and returned as JSON/JSONB-safe floats.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_CENTS = Decimal("0.01")


def D(x) -> Decimal:
    """Lossless Decimal from any numeric input."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def money(x) -> float:
    """Quantize to 2dp and return a float for serialization."""
    return float(D(x).quantize(_CENTS, rounding=ROUND_HALF_UP))
