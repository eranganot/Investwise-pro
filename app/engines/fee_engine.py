"""Phase 3.2 - FEE & EXPENSE-RATIO OPTIMIZER (specialized sub-agent).

Scans holdings for high management-fee / high-expense-ratio assets and, for each,
suggests an equivalent **low-fee, highly-liquid index** alternative in the same
asset class, with the annual fee saving computed deterministically:

    annual_fee      = value * expense_ratio
    alt_annual_fee  = value * alternative_expense_ratio
    annual_saving   = annual_fee - alt_annual_fee   (only reported when > 0)

The alternatives table is a single editable mapping (config-driven) - swap in
your preferred index funds / ETFs. Holdings with no fee data are skipped (no
hallucinated numbers); only assets whose expense ratio exceeds the configured
high-fee threshold are flagged.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.fees import FeeAlternative, FeeFinding, FeeReport

# Editable: equivalent low-fee, highly-liquid index option per asset class.
LOW_FEE_ALTERNATIVES: dict[str, dict] = {
    "Equities": {"ticker": "VWRA", "name": "Vanguard FTSE All-World UCITS", "expense_ratio_pct": 0.22},
    "Fixed Income": {"ticker": "AGGG", "name": "iShares Core Global Aggregate Bond", "expense_ratio_pct": 0.10},
    "Commodities": {"ticker": "SGLN", "name": "iShares Physical Gold", "expense_ratio_pct": 0.12},
    "Real Estate": {"ticker": "IWDP", "name": "iShares Developed Mkts Property Yield", "expense_ratio_pct": 0.24},
    "Alternatives": {"ticker": "MNA", "name": "IQ Merger Arbitrage (liquid alt)", "expense_ratio_pct": 0.77},
}


class FeeEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def scan(self, holdings: list[dict]) -> FeeReport:
        """holdings: dicts with ticker, asset_class, value_ils, expense_ratio_pct (optional)."""
        thr = self.settings.fee_high_threshold_pct
        findings: list[FeeFinding] = []
        scanned = 0
        for h in holdings:
            er = h.get("expense_ratio_pct")
            value = float(h.get("value_ils") or 0.0)
            if er is None or value <= 0:
                continue
            scanned += 1
            er = float(er)
            if er < thr:
                continue
            alt = LOW_FEE_ALTERNATIVES.get(h.get("asset_class"))
            if not alt or alt["expense_ratio_pct"] >= er:
                continue
            current_fee = value * er / 100.0
            alt_fee = value * alt["expense_ratio_pct"] / 100.0
            saving = current_fee - alt_fee
            if saving <= 0:
                continue
            findings.append(FeeFinding(
                ticker=h["ticker"], asset_class=h["asset_class"], value_ils=round(value, 2),
                current_expense_ratio_pct=er, current_annual_fee_ils=round(current_fee, 2),
                alternative=FeeAlternative(ticker=alt["ticker"], name=alt["name"],
                                           expense_ratio_pct=alt["expense_ratio_pct"]),
                alternative_annual_fee_ils=round(alt_fee, 2),
                annual_saving_ils=round(saving, 2),
                saving_pct_of_fee=round((saving / current_fee) * 100.0, 1) if current_fee else 0.0,
            ))
        findings.sort(key=lambda x: x.annual_saving_ils, reverse=True)
        return FeeReport(threshold_pct=thr, scanned=scanned, findings=findings,
                         total_annual_saving_ils=round(sum(f.annual_saving_ils for f in findings), 2))
