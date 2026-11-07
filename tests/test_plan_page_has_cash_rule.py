"""The cash redeploy trigger is editable from the Plan page, not only the Cash overlay."""
from pathlib import Path

HTML = Path("app/static_app/index.html").read_text(encoding="utf-8")


def test_plan_page_has_a_cash_rule_row_and_sheet():
    assert 'row("Cash"' in HTML and 'openCashRulesSheet()' in HTML
    assert "function openCashRulesSheet" in HTML and "function saveCashRulesSheet" in HTML
    assert "cash_redeploy_at_ils:at" in HTML.split("function saveCashRulesSheet")[1][:900]
