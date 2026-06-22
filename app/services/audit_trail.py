"""Phase 2.1 - 'Source Code & Data' audit trail for every recommendation.

Turns each final recommendation into a fully transparent record:
  * ``raw_data``  - the exact portfolio figures evaluated;
  * ``formulas``  - the deterministic formula applied, with the real numbers
                    substituted in and the result shown (no black box);
  * ``adversary`` - a deterministic red-team critique that challenges the
                    assumptions behind this specific recommendation.

The UI renders this inside a collapsible accordion on each card.
"""
from __future__ import annotations


def f(name: str, expr: str, *, substituted: str = "", result: str = "") -> dict:
    """One formula row: the symbolic expression, the substituted numbers, result."""
    return {"name": name, "expr": expr, "substituted": substituted, "result": result}


# Per-dimension red-team challenges - the blind spots a skeptic would raise.
_ADVERSARY = {
    "diversification": [
        "Assumes the quoted price is live - a stale quote would mis-size the trim.",
        "Selling crystallizes a taxable capital gain that this trim estimate does not net out.",
    ],
    "tax": [
        "Assumes the loss is still unrealized at the moment you execute.",
        "Re-buying inside the wash-sale window forfeits the loss - wait it out.",
        "Savings use the configured CGT rate; your marginal rate may differ - confirm with your accountant.",
    ],
    "allocation": [
        "Assumes the target weights are still your policy and haven't drifted intentionally.",
        "Tax drag and slippage (already netted) can outweigh the benefit when drift is small.",
    ],
    "fees": [
        "Switching can realize a taxable capital gain on the sold fund - weigh it against the fee saving.",
        "Assumes the index alternative tracks your holding's exposure; check tracking error and currency hedging.",
    ],
    "goal": [
        "Projection assumes a fixed annual return with no volatility - a poor sequence of returns widens the gap.",
        "Contribution math assumes you sustain the monthly amount for the full horizon.",
    ],
    "holding": [
        "The fundamental score is a point-in-time snapshot; one earnings miss can change the picture.",
        "A 'Buy more' verdict ignores your overall concentration - size any add against your limits.",
    ],
    "risk": [
        "Sector tags are best-effort; a mislabeled holding can over- or under-state the concentration.",
        "Diversifiers reduce single-sector risk but can drag returns in a strong bull market.",
    ],
    "momentum": [
        "Trend signals are price-only and say nothing about value - they can whipsaw.",
        "Acting on momentum can mean buying high or selling low; pair it with a fundamental view.",
    ],
    "income": [
        "Chasing yield can mean buying lower-quality payers - check dividend coverage.",
        "Cash isn't dead money if you genuinely need the liquidity soon.",
    ],
}


def adversary_review(dimension: str, extra: list[str] | None = None) -> dict:
    findings = list(_ADVERSARY.get(dimension, []))
    if extra:
        findings += extra
    return {
        "severity": "warn" if findings else "ok",
        "findings": findings,
        "critique": ("Red-team: " + " ".join(findings)) if findings
                    else "Red-team: no unstated assumptions flagged.",
    }


def audit_for(dimension: str, raw_data: dict, formulas: list[dict],
              extra_challenges: list[str] | None = None) -> dict:
    return {
        "raw_data": raw_data,
        "formulas": formulas,
        "adversary": adversary_review(dimension, extra_challenges),
    }
