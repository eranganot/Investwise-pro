# PHASE 17 - discipline rules, sized from the strategy's own measured numbers.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase17-discipline.ps1
#
# No migration. Requires phases 12-15 already deployed.
#
# USER-VISIBLE, only with a Beat the Market strategy applied AND a stored
# backtest AND actually holding the sleeve ticker: a card offers a trailing stop
# and a sleeve cap. Accept arms them as normal trading rules. Nothing is ever
# armed without a tap.
#
# Also corrects the phase 15 signal card, which claimed Accept would act.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/strategy_catalog.py",
    "app/services/strategy_signal_service.py",
    "app/services/recommendations.py",
    "tests/test_strategy_signals.py",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/deploy/phase17-discipline.ps1"
)

# The smoke script was renamed; stage the old path's deletion so git does not
# keep tracking a file that is gone. Skipping this is what left
# smoke-phase10-13.ps1 tracked-but-absent and broke a phase re-run.
git rm --quiet --ignore-unmatch -- "scripts/smoke/smoke-phase10-15.ps1" "scripts/smoke/smoke-phase10-13.ps1"

Invoke-Suite -Focus @("tests/test_strategy_signals.py", "tests/test_backtest_service.py",
                      "tests/test_strategy_backtest.py", "tests/test_trading_rule_suggestions.py",
                      "tests/test_accept_honesty.py", "tests/test_recommendations.py")

$msg = @(
    "feat(strategy): arm the discipline a strategy needs, sized from its own numbers",
    "",
    "Phase E. The gap between a strategy's backtested return and its lived one",
    "is mostly the days the rule was not followed. These are the standing",
    "orders that keep firing when the user is not looking.",
    "",
    "* discipline_rules() derives a trailing stop and a sleeve cap from the",
    "  strategy's MEASURED volatility and its suggested sleeve size, not from",
    "  a round number. A stop inside the strategy's ordinary noise is churn,",
    "  not discipline -- a 49%-volatility basket cannot wear the same stop as",
    "  an 18% one.",
    "* With no stored backtest it returns NOTHING rather than guessing. An",
    "  invented stop level is worse than no stop, because it looks calculated.",
    "* Rules target the aggressive sleeve only. A stop on the core holding is",
    "  a stop on the thing you fall back TO -- it would exit you from safety.",
    "* A cap the book already breaches is skipped: arming it fires instantly,",
    "  which reads as the app deciding to sell rather than a guard being set.",
    "* Offered as a one-tap card, never armed automatically. A stop the user",
    "  did not choose is a stop that surprises them into a sale.",
    "",
    "ALSO FIXES A PHASE D BUG. The signal card shipped with",
    "apply.kind `"set_plan`", whose handler reads spec[`"fields`"] -- which the",
    "card never set. Accept would have called upsert_plan() with nothing,",
    "moved no holding, and still reported success. That is precisely the",
    "`"Accept pretends to act`" failure the actionable/guidance split was",
    "introduced to kill. The card is now honest guidance: it says what to do",
    "and states that the app is not doing it. Moving a sleeve properly means",
    "sized, funded trades; when that exists the card can become actionable.",
    "",
    "+7 tests (16 in the signals module). ruff clean.",
    "",
    "Renames the smoke script to smoke-beat-market.ps1 -- it had been",
    "smoke-phase10-13 then -15, and a phase-numbered name goes stale every",
    "time the smoke grows, which already broke one deploy script's file list."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'Then run the full smoke:' -ForegroundColor Cyan
Write-Host '  .\scripts\smoke\smoke-beat-market.ps1 -Refresh' -ForegroundColor Gray
Write-Host '  .\scripts\smoke\smoke-all.ps1' -ForegroundColor Gray
