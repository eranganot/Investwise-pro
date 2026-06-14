"""Phase 3.3 - HISTORICAL BACKTESTING ENGINE.

Replays a proposed portfolio against bundled historical market events (2008 GFC,
2020 COVID crash, 2022 bear) to mathematically validate the Risk Agent's beta
*before* final approval.

Method (deterministic, offline - no external data):
  * Each event ships a monthly market-index return series (illustrative, labelled).
  * The book's **structural beta** = sum(weight_i * asset_class_beta_i).
  * Apply that beta to the market series (single-factor model) to get the
    portfolio's realized path, drawdown and total return for each event.
  * If the Risk Agent supplies a volatility, derive its **volatility-implied beta**
    (portfolio_vol / market_vol, assuming correlation ~1) and compare it to the
    structural/historical beta. A divergence beyond ``backtest_beta_tolerance``
    flags the Risk Agent's beta as needing recalibration.

The bundled series are illustrative magnitudes for the named episodes - swap in a
real vendor series later without changing the interface.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.backtest import BacktestReport, EventResult

# Editable: each asset class's sensitivity to the broad equity market.
ASSET_CLASS_BETA: dict[str, float] = {
    "Equities": 1.00, "Fixed Income": 0.15, "Cash": 0.0, "Commodities": 0.45,
    "Real Estate": 0.75, "Alternatives": 0.50, "Private Investments": 0.90,
}

# Bundled monthly market-index return series for each episode (decimals).
HISTORICAL_EVENTS: dict[str, dict] = {
    "GFC_2008": {"label": "2008 Global Financial Crisis",
                 "returns": [-0.09, -0.17, -0.075, -0.04, 0.01, -0.085, -0.11, -0.02, 0.09, 0.085]},
    "COVID_2020": {"label": "2020 COVID crash",
                   "returns": [-0.085, -0.30, 0.13, 0.045]},
    "BEAR_2022": {"label": "2022 rate-shock bear market",
                  "returns": [-0.05, -0.03, 0.037, -0.088, 0.001, -0.084, 0.092, -0.042, -0.093, 0.08, 0.055, -0.059]},
}


def _max_drawdown(path: list[float]) -> float:
    peak, mdd = path[0], 0.0
    for v in path:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    return mdd


def _cumulative(returns: list[float]) -> list[float]:
    path, v = [1.0], 1.0
    for r in returns:
        v *= (1.0 + r)
        path.append(v)
    return path


class BacktestEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def structural_beta(self, holdings: list[dict]) -> float:
        total = sum(float(h.get("value_ils") or 0) for h in holdings)
        if total <= 0:
            n = len(holdings) or 1
            return round(sum(ASSET_CLASS_BETA.get(h.get("asset_class"), 1.0) for h in holdings) / n, 4)
        beta = sum((float(h.get("value_ils") or 0) / total) * ASSET_CLASS_BETA.get(h.get("asset_class"), 1.0)
                   for h in holdings)
        return round(beta, 4)

    def run(self, holdings: list[dict], *, portfolio_vol_pct: float | None = None) -> BacktestReport:
        s = self.settings
        beta = self.structural_beta(holdings)

        events: list[EventResult] = []
        for name, data in HISTORICAL_EVENTS.items():
            mkt = data["returns"]
            mkt_dd = _max_drawdown(_cumulative(mkt))
            port_path = _cumulative([beta * r for r in mkt])
            events.append(EventResult(
                event=name, label=data["label"],
                market_drawdown_pct=round(mkt_dd * 100, 2),
                portfolio_drawdown_pct=round(_max_drawdown(port_path) * 100, 2),
                portfolio_return_pct=round((port_path[-1] - 1.0) * 100, 2),
            ))

        worst = max(events, key=lambda e: e.portfolio_drawdown_pct)

        implied = None
        divergence = None
        validated = True
        if portfolio_vol_pct is not None and s.backtest_market_vol_pct > 0:
            implied = round(portfolio_vol_pct / s.backtest_market_vol_pct, 4)
            divergence = round(abs(implied - beta), 4)
            validated = divergence <= s.backtest_beta_tolerance

        if not validated:
            critique = (f"BETA DIVERGENCE: the Risk Agent's volatility-implied beta {implied} differs from the "
                        f"portfolio's structural/historical beta {beta} by {divergence} "
                        f"(> {s.backtest_beta_tolerance} tolerance). Recalibrate the risk model before approval.")
        elif implied is not None:
            critique = (f"Beta validated: volatility-implied beta {implied} is within "
                        f"{s.backtest_beta_tolerance} of the structural beta {beta}.")
        else:
            critique = (f"Structural beta {beta}; supply a portfolio volatility to validate the Risk Agent's "
                        f"implied beta against history.")

        return BacktestReport(
            structural_beta=beta, risk_implied_beta=implied, beta_divergence=divergence,
            beta_tolerance=s.backtest_beta_tolerance, beta_validated=validated,
            worst_event=worst.event, worst_portfolio_drawdown_pct=worst.portfolio_drawdown_pct,
            events=events, critique=critique,
        )
