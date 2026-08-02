# PHASE 2 - Triggered rules drive real action, and leave a record.
#
# Backend only: the Rule history UI ships in phase 5 with the other frontend
# changes, so run phase 5 before QAing this on the phone.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase2-rules.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/services/rules_service.py",
    "app/services/recommendations.py",
    "app/api/routes/rules.py",
    "tests/test_rule_actions.py"
)

Invoke-Suite -Focus @("tests/test_rule_actions.py", "tests/test_accept_honesty.py", "tests/test_trading_rule_suggestions.py")

$msg = @(
    "feat(rules): triggered rules execute, and every firing is recorded",
    "",
    "Reported: 'if the app hit the trading rules I would expect to see a",
    "difference in my holdings but I can't see anything' and 'I see the trading",
    "rule triggered but I can't see the actions that were taken'.",
    "",
    "Both were true. triggered_rule_recs emitted cards with no apply key at all,",
    "so every firing rendered as 'Guidance - you act on this yourself' and Accept",
    "could never do anything; and a firing left only triggered/last_triggered_at",
    "on the rule, so nothing anywhere recorded what happened next.",
    "",
    "Execution (one-tap, never automatic):",
    "- execution_plan() maps a rule to the trade its order type actually implies:",
    "  stop-loss / trailing stop / take-profit are full exits by definition;",
    "  a max-weight breach trims back to the cap by exact arithmetic. Price",
    "  alerts and buy-the-dip stay advisory rather than inventing a size.",
    "- new sell_position apply-kind: removes the position and credits the",
    "  net-of-CGT proceeds to visible cash, the same path Accept already used",
    "  for trims. The card and the result state plainly that the tracked book",
    "  was updated and no brokerage order was placed.",
    "",
    "Audit trail:",
    "- new RuleEvent table: one row per firing (trigger price, target, whether a",
    "  push went out, the plan it implied), stamped with the outcome when the",
    "  user executes or declines. Only the still-open event is stamped, so a",
    "  regenerated card can't rewrite the history of an earlier firing.",
    "- GET /api/v1/rules/events",
    "",
    "+6 tests (test_rule_actions.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nNote: rule_events is created by auto_create_tables (same as" -ForegroundColor Yellow
Write-Host "trading_rules / push_subscriptions). No Alembic revision added - if" -ForegroundColor Yellow
Write-Host "AUTO_CREATE_TABLES is off in production, add one before deploying." -ForegroundColor Yellow
