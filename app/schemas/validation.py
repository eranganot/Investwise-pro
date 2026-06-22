"""Strict cross-agent validation primitives (Phase 1.2).

Goal: make the state objects that move *between* the Risk, Tax, Decision and
Allocation agents impossible to drift. We do that with three layers:

1. **Constrained numeric types** (below) that reject NaN/Inf and enforce the
   real-world domain of each quantity (a probability is in [0, 1]; a normalized
   score is in [0, 100]; a volatility can't be negative).
2. **A strict, closed model config** (`STRICT_FROZEN`) applied to every internal
   stage model: ``strict=True`` (no silent str/loose coercion - though int->float
   is still allowed, which is safe), ``extra="forbid"`` (an unexpected field is a
   bug, not something to swallow), and ``frozen=True`` (a stage can't be mutated
   mid-pipeline).
3. **`assert_handoff`** - an explicit contract check the orchestrator calls after
   every transition to prove that the object one agent produced literally
   satisfies the input the next agent expects (the right stage type, with its
   typed ``source`` chain intact).

Input-boundary schemas (CSV intake, API request bodies) deliberately do NOT use
these - they must still coerce strings/JSON. Strictness is for the *internal*
agent-to-agent surface only.
"""
from __future__ import annotations

from typing import Annotated, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# --- constrained numeric domains (all reject NaN and +/-Inf) ---
# A finite real number (may be negative: P&L deltas, returns, divergence).
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
# A finite, non-negative amount (taxes, costs, volatility, ILS magnitudes).
NonNegFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
# A probability or fraction in [0, 1] (probability of ruin, drawdown fraction).
UnitFraction = Annotated[float, Field(ge=0.0, le=1.0)]
# A normalized 0-100 score (impact, confidence, sub-scores, liquidity health).
Score = Annotated[float, Field(ge=0.0, le=100.0)]
# The complexity divisor used by the Impact formula (Trivial 1.0 .. Complex 2.0).
ComplexityFactor = Annotated[float, Field(ge=1.0, le=2.0)]

# Strict, closed, immutable config for every internal stage/state model.
STRICT_FROZEN = ConfigDict(frozen=True, strict=True, extra="forbid")
# Strict + closed but mutable (for computed result DTOs that aren't pipeline stages).
STRICT = ConfigDict(strict=True, extra="forbid")


class HandoffError(TypeError):
    """Raised when one agent's output does not satisfy the next agent's input."""


_M = TypeVar("_M", bound=BaseModel)


def assert_handoff(produced: BaseModel, expected_type: type[_M]) -> _M:
    """Assert that ``produced`` is exactly the stage the next agent expects.

    Returns the object (narrowed) so call sites can chain. Raises
    ``HandoffError`` if the type is wrong - which, combined with the typed
    ``source`` field on each stage, guarantees the whole predecessor chain is
    intact (you can't build a ``VettedSignal`` without a real ``DetectedSignal``
    inside it).
    """
    if not isinstance(produced, expected_type):
        raise HandoffError(
            f"Cross-agent contract violation: expected "
            f"{expected_type.__name__}, got {type(produced).__name__}."
        )
    return produced
