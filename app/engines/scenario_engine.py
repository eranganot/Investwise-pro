"""Section X.5 - SCENARIO PLANNING (deterministic macro stress tests)."""
from __future__ import annotations

from app.core.config import Settings, get_settings

# Fixed (deterministic) shock parameters per macro scenario.
SCENARIOS: dict[str, dict] = {
    "MARKET_CRASH": {"delta": -0.30, "drawdown": 0.65, "recovery": 540},
    "INFLATION_SHOCK": {"delta": -0.042, "drawdown": 0.35, "recovery": 180},
    "INTEREST_RATE_INCREASE": {"delta": -0.06, "drawdown": 0.40, "recovery": 270},
    "FX_SHOCK": {"delta": -0.03, "drawdown": 0.30, "recovery": 120},
    "COMMODITY_SHOCK": {"delta": -0.05, "drawdown": 0.35, "recovery": 200},
}
SUPPORTED = list(SCENARIOS) + ["CUSTOM_SCENARIO"]


class ScenarioEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(
        self, scenario: str, nav: float, *,
        custom_delta_pct: float | None = None,
        custom_drawdown: float | None = None,
        custom_recovery_days: int | None = None,
    ) -> dict:
        if scenario == "CUSTOM_SCENARIO":
            params = {
                "delta": (custom_delta_pct or 0.0) / 100.0,
                "drawdown": custom_drawdown if custom_drawdown is not None else 0.3,
                "recovery": custom_recovery_days if custom_recovery_days is not None else 180,
            }
        elif scenario in SCENARIOS:
            params = SCENARIOS[scenario]
        else:
            raise ValueError(f"scenario must be one of {SUPPORTED}")

        value_delta = params["delta"] * nav
        # A loss crystallizes a tax effect (harvestable benefit) at the CGT rate.
        tax_impact = self.settings.cgt_rate * abs(value_delta)
        return {
            "scenario": scenario,
            "expected_portfolio_value_delta_pct": round(params["delta"] * 100.0, 2),
            "drawdown_probability": params["drawdown"],
            "projected_tax_impact_currency": round(tax_impact, 2),
            "estimated_recovery_timeline_days": int(params["recovery"]),
        }
