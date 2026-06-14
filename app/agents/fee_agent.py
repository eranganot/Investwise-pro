"""Phase 3.2 - FEE AGENT: turns the Fee Engine's findings into actions.

A thin specialist that converts high-fee holdings into FEE_OPTIMIZATION
recommendations (with a full audit trail) and powers the /fees endpoint.
"""
from __future__ import annotations

import hashlib

from app.engines.fee_engine import FeeEngine
from app.schemas.fees import FeeReport
from app.services.audit_trail import audit_for, f


def positions_to_holdings(pdicts: list[dict]) -> list[dict]:
    """Map portfolio positions to fee-scan holdings (value + expense ratio)."""
    out = []
    for p in pdicts:
        value = float(p.get("quantity") or 0) * float(p.get("current_price") or 0)
        out.append({"ticker": p["ticker"], "asset_class": p.get("asset_class") or "Equities",
                    "value_ils": value, "expense_ratio_pct": p.get("expense_ratio_pct")})
    return out


class FeeAgent:
    def __init__(self, engine: FeeEngine | None = None) -> None:
        self.engine = engine or FeeEngine()

    def report(self, pdicts: list[dict]) -> FeeReport:
        return self.engine.scan(positions_to_holdings(pdicts))

    def recommendations(self, pdicts: list[dict]) -> list[dict]:
        report = self.report(pdicts)
        recs: list[dict] = []
        for fd in report.findings:
            rid = "rec_fee_" + hashlib.sha1(fd.ticker.encode()).hexdigest()[:6]
            alt = fd.alternative
            recs.append({
                "id": rid, "dimension": "fees",
                "severity": "HIGH" if fd.annual_saving_ils >= 1000 else "MEDIUM",
                "title": f"Cut fees on {fd.ticker}",
                "action": (f"{fd.ticker} charges {fd.current_expense_ratio_pct:.2f}%/yr "
                           f"(₪{fd.current_annual_fee_ils:,.0f}). Switching to {alt.ticker} "
                           f"({alt.name}, {alt.expense_ratio_pct:.2f}%) saves about "
                           f"₪{fd.annual_saving_ils:,.0f}/yr."),
                "how": [f"Review {alt.ticker} - a low-fee, highly-liquid {fd.asset_class} index option",
                        f"If it fits your exposure, sell {fd.ticker} and buy {alt.ticker}",
                        "Mind any capital-gains tax on the sale before switching"],
                "est_amount": fd.annual_saving_ils,
                "apply": {"kind": "none"},
                "audit_trail": audit_for("fees",
                    raw_data={"ticker": fd.ticker, "asset_class": fd.asset_class,
                              "value_ils": fd.value_ils,
                              "current_expense_ratio_pct": fd.current_expense_ratio_pct,
                              "alternative": alt.ticker,
                              "alternative_expense_ratio_pct": alt.expense_ratio_pct},
                    formulas=[
                        f("Current annual fee", "fee = value x expense_ratio",
                          substituted=f"{fd.value_ils:,.0f} x {fd.current_expense_ratio_pct:.2f}%",
                          result=f"₪{fd.current_annual_fee_ils:,.0f}"),
                        f("Alternative annual fee", "alt_fee = value x alt_expense_ratio",
                          substituted=f"{fd.value_ils:,.0f} x {alt.expense_ratio_pct:.2f}%",
                          result=f"₪{fd.alternative_annual_fee_ils:,.0f}"),
                        f("Annual saving", "saving = fee - alt_fee",
                          result=f"₪{fd.annual_saving_ils:,.0f} ({fd.saving_pct_of_fee:.0f}% of the fee)")]),
            })
        return recs
