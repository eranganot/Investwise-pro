"""Section X.5 - SCENARIO PLANNING (deterministic macro stress tests).

Headline shocks are blended across asset classes when an allocation is supplied
(a rate shock hits bonds and equities differently); otherwise the portfolio-level
delta is used. Outputs carry an explicit model_assumptions block (review C2).
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.money import D, money

SCENARIOS: dict[str, dict] = {
    "MARKET_CRASH": {"delta": -0.30, "drawdown": 0.65, "recovery": 540},
    "INFLATION_SHOCK": {"delta": -0.042, "drawdown": 0.35, "recovery": 180},
    "INTEREST_RATE_INCREASE": {"delta": -0.06, "drawdown": 0.40, "recovery": 270},
    "FX_SHOCK": {"delta": -0.03, "drawdown": 0.30, "recovery": 120},
    "COMMODITY_SHOCK": {"delta": -0.05, "drawdown": 0.35, "recovery": 200},
}
SUPPORTED = list(SCENARIOS) + ["CUSTOM_SCENARIO"]

# Per-asset-class sensitivity (delta) for each scenario - deterministic, illustrative.
SHOCK_BY_CLASS: dict[str, dict[str, float]] = {
    "MARKET_CRASH": {"Equities": -0.40, "Fixed Income": -0.05, "Cash": 0.0,
                     "Commodities": -0.15, "Alternatives": -0.20, "Real Estate": -0.25,
                     "Private Investments": -0.30},
    "INFLATION_SHOCK": {"Equities": -0.05, "Fixed Income": -0.08, "Cash": -0.03,
                        "Commodities": 0.06, "Alternatives": -0.02, "Real Estate": 0.02,
                        "Private Investments": -0.03},
    "INTEREST_RATE_INCREASE": {"Equities": -0.07, "Fixed Income": -0.12, "Cash": 0.01,
                               "Commodities": -0.03, "Alternatives": -0.04,
                               "Real Estate": -0.10, "Private Investments": -0.05},
    "FX_SHOCK": {"Equities": -0.02, "Fixed Income": -0.01, "Cash": 0.0,
                 "Commodities": -0.04, "Alternatives": -0.02, "Real Estate": -0.01,
                 "Private Investments": -0.02},
    "COMMODITY_SHOCK": {"Equities": -0.04, "Fixed Income": -0.01, "Cash": 0.0,
                        "Commodities": -0.20, "Alternatives": -0.05, "Real Estate": -0.03,
                        "Private Investments": -0.04},
}


class ScenarioEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(
        self, scenario: str, nav: float, *,
        custom_delta_pct: float | None = None,
        custom_drawdown: float | None = None,
        custom_recovery_days: int | None = None,
        allocation: dict[str, float] | None = None,
    ) -> dict:
        if scenario == "CUSTOM_SCENARIO":
            params = {"delta": (custom_delta_pct or 0.0) / 100.0,
                      "drawdown": custom_drawdown if custom_drawdown is not None else 0.3,
                      "recovery": custom_recovery_days if custom_recovery_days is not None else 180}
            asset_class_aware = False
        elif scenario in SCENARIOS:
            params = dict(SCENARIOS[scenario])
            asset_class_aware = bool(allocation)
            if allocation:
                shocks = SHOCK_BY_CLASS.get(scenario, {})
                total_w = sum(allocation.values()) or 1.0
                blended = sum(w * shocks.get(cls, params["delta"]) for cls, w in allocation.items()) / total_w
                params["delta"] = blended
        else:
            raise ValueError(f"scenario must be one of {SUPPORTED}")

        value_delta = D(params["delta"]) * D(nav)
        tax_impact = D(self.settings.cgt_rate) * abs(value_delta)
        return {
            "scenario": scenario,
            "expected_portfolio_value_delta_pct": round(params["delta"] * 100.0, 2),
            "drawdown_probability": params["drawdown"],
            "projected_tax_impact_currency": money(tax_impact),
            "estimated_recovery_timeline_days": int(params["recovery"]),
            "asset_class_aware": asset_class_aware,
            "model_assumptions": [
                "deterministic macro shock (no stochastic paths)",
                "asset-class-weighted delta" if asset_class_aware else "portfolio-level blended delta",
                "tax impact = CGT rate x |value change| (harvestable/realized estimate)",
            ],
        }
