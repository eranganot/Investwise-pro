"""Section Y - ALLOCATION ENGINE (isolated domain service).

Tracks SAA/TAA targets, detects absolute drift per asset class, and sizes
rebalancing trades against total NAV. Every action deducts projected tax drag
(CGT crystallization on sells), transaction cost, and slippage before the data
moves downstream. Runs and declares state BEFORE the Decision Engine.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.money import D, money
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
            gross = abs(D(d)) * D(nav)
            is_sell = d > 0  # overweight -> trim
            tax_drag = (D(s.cgt_rate) * D(s.rebalance_assumed_gain_ratio) * gross) if is_sell else D(0)
            txn_cost = D(s.rebalance_txn_cost_bps) / D(10_000) * gross
            slippage = D(s.rebalance_slippage_bps) / D(10_000) * gross
            actions.append(RebalanceAction(
                asset_class=c,
                action_type="SELL" if is_sell else "BUY",
                target_weight=target_allocation.get(c, 0.0),
                estimated_trade_value_currency=money(gross),
                tax_drag_currency=money(tax_drag),
                transaction_cost_currency=money(txn_cost),
                slippage_cost_currency=money(slippage),
                net_trade_value_currency=money(gross - tax_drag - txn_cost - slippage),
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
