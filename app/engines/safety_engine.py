"""Safety Layer (Section 8).

Independent guardrails over the portfolio + proposed actions:
  * concentration risk - a position (existing or post-trade) above the cap
  * liquidity failure   - liquid/cash ratio below the minimum
  * irrational decision - a proposed Buy whose own risk score is dangerously low

Verdict: 'block' if any high-severity flag, else 'warn' if any flag, else 'ok'.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.safety import SafetyFlag, SafetyReport


class SafetyEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def check(
        self,
        *,
        holdings: dict[str, float] | None = None,
        liquidity_ratio: float = 1.0,
        proposals: list[dict] | None = None,
    ) -> SafetyReport:
        holdings = holdings or {}
        proposals = proposals or []
        cap = self.settings.concentration_cap
        flags: list[SafetyFlag] = []

        # Existing over-concentration
        for tk, w in holdings.items():
            if w > cap:
                flags.append(SafetyFlag(
                    type="concentration", severity="medium", ticker=tk,
                    detail=f"{tk} already {w:.0%} of portfolio (> {cap:.0%} cap)",
                ))

        # Proposed trades
        for p in proposals:
            tk = p.get("ticker")
            action = p.get("action", "Buy")
            if action == "Buy":
                new_w = holdings.get(tk, 0.0) + float(p.get("weight_delta", 0.0))
                if new_w > cap:
                    flags.append(SafetyFlag(
                        type="concentration", severity="high", ticker=tk,
                        detail=f"Buying {tk} would reach {new_w:.0%} (> {cap:.0%} cap)",
                    ))
            risk_score = p.get("risk_score")
            if risk_score is not None and risk_score < 30:
                flags.append(SafetyFlag(
                    type="irrational", severity="high", ticker=tk,
                    detail=f"{tk} risk score {risk_score:.0f} is dangerously low for a {action}",
                ))

        if liquidity_ratio < self.settings.min_liquidity_ratio:
            flags.append(SafetyFlag(
                type="liquidity", severity="high",
                detail=f"Liquidity {liquidity_ratio:.0%} below "
                       f"{self.settings.min_liquidity_ratio:.0%} minimum",
            ))

        verdict = "block" if any(f.severity == "high" for f in flags) else \
                  ("warn" if flags else "ok")
        return SafetyReport(verdict=verdict, flags=flags)
