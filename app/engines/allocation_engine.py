"""Section Y - ALLOCATION ENGINE (isolated domain service).

Tracks SAA/TAA targets, detects absolute drift per asset class, and sizes
rebalancing trades against total NAV. Every action deducts projected tax drag
(CGT crystallization on sells), transaction cost, and slippage before the data
moves downstream. Runs and declares state BEFORE the Decision Engine.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.allocation import AllocationReport, RebalanceAction


class AllocationEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def compute(
        self, *, target_allocation: dict[str, float],
        current_allocation: dict[str, float], nav: float,
    ) -> AllocationReport:
        s = self.settings
        classes = sorted(set(target_allocation) | set(current_allocation))
        drift = {c: round(current_allocation.get(c, 0.0) - target_allocation.get(c, 0.0), 6)
                 for c in classes}

        actions: list[RebalanceAction] = []
        for c in classes:
            d = drift[c]
            if abs(d) <= s.allocation_drift_threshold:
                continue
            gross = abs(d) * nav
            is_sell = d > 0  # overweight -> trim
            tax_drag = (s.cgt_rate * s.rebalance_assumed_gain_ratio * gross) if is_sell else 0.0
            txn_cost = s.rebalance_txn_cost_bps / 10_000.0 * gross
            slippage = s.rebalance_slippage_bps / 10_000.0 * gross
            actions.append(RebalanceAction(
                asset_class=c,
                action_type="SELL" if is_sell else "BUY",
                target_weight=target_allocation.get(c, 0.0),
                estimated_trade_value_currency=round(gross, 2),
                tax_drag_currency=round(tax_drag, 2),
                transaction_cost_currency=round(txn_cost, 2),
                slippage_cost_currency=round(slippage, 2),
                net_trade_value_currency=round(gross - tax_drag - txn_cost - slippage, 2),
            ))

        # largest drift first
        actions.sort(key=lambda a: a.estimated_trade_value_currency, reverse=True)
        return AllocationReport(
            nav=nav, target_allocation=target_allocation,
            current_allocation=current_allocation, drift_percentage=drift,
            rebalance_required=bool(actions), rebalance_actions=actions,
        )

    def overweight_classes(self, report: AllocationReport) -> set[str]:
        return {c for c, d in report.drift_percentage.items()
                if d > self.settings.allocation_drift_threshold}
