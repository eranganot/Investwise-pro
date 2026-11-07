"""The Plan tab lists three settings rows (Goal, Risk dials, Cards & cash); the cash redeploy
trigger is edited in the last one, not only the Cash overlay."""
from pathlib import Path

HTML = Path("app/static_app/index.html").read_text(encoding="utf-8")
ROWS = HTML.split("function goalRows")[1].split("function openGoalSheet")[0]


def test_plan_page_has_exactly_three_settings_rows():
    assert ROWS.count('row("') == 3
    assert 'row("Goal"' in ROWS and 'row("Risk dials"' in ROWS and 'row("Cards & cash"' in ROWS
    assert 'row("Target"' not in ROWS and 'row("Monthly deposit"' not in ROWS


def test_the_cash_trigger_is_saved_from_the_cards_and_cash_sheet():
    sheet = HTML.split("async function saveThresholdsSheet")[1][:2500]
    assert "cash_redeploy_at_ils=at" in sheet and "cash_target_ils=keep" in sheet
    assert 'id="cs_at"' in HTML.split("function openThresholdsSheet")[1][:1500]
