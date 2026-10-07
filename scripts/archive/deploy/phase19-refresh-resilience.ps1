# PHASE 19 - a failed refresh keeps the measurement; banner check actually runs.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase19-refresh-resilience.ps1
#
# DB: alembic 0011 adds last_error / last_error_at. Run `alembic upgrade head`.
#
# YOUR STORED BACKTESTS ARE CURRENTLY EMPTY -- the last -Refresh wiped them.
# After deploying, re-run the refresh ONCE and wait for it to succeed:
#     .\scripts\smoke\smoke-beat-market.ps1 -Refresh
# If it reports provider_outage, the circuit breaker is open: wait a few
# minutes and run it again. From now on a failure cannot erase what it finds.
#
# ALSO FIXES THE RED SUITE. The 12 "no such column: strategy_backtests.last_error"
# failures were a leftover sqlite test database, not the new code: create_all
# adds missing tables but never a column to a table that already exists. The
# test DB is now deleted at the start of every run, so a model change can no
# longer poison the next suite.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/services/backtest_service.py",
    "app/services/recommendations.py",
    "alembic/versions/0011_backtest_last_error.py",
    "tests/test_backtest_service.py",
    "tests/test_conftest_schema_reset.py",
    "tests/conftest.py",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/deploy/phase19-refresh-resilience.ps1"
)

Invoke-Suite -Focus @("tests/test_conftest_schema_reset.py",
                      "tests/test_backtest_service.py", "tests/test_strategy_signals.py",
                      "tests/test_strategy_backtest.py", "tests/test_recommendations.py",
                      "tests/test_done_vs_ignored.py")

$msg = @(
    "fix(backtest): a failed refresh must not erase the measurement",
    "",
    "Reported from a live smoke run: `"refresh: 0 computed, 7 abstained`",",
    "every strategy MISSING_TICKER on QQQ, SPY, TQQQ. Ten years of computed",
    "history replaced by an error string in one pass.",
    "",
    "MY DESIGN ERROR. store() overwrote the row whenever a run abstained,",
    "reasoning that a figure which can no longer be reproduced should not",
    "present itself as current. That is right for `"this strategy is no longer",
    "measurable`" and badly wrong for `"the price feed was down for a minute`" --",
    "and the resilience tier's circuit breaker makes the second the NORMAL",
    "shape of a bad minute, because it opens for the whole history tier at",
    "once, so all seven fail together. The honest-looking rule destroyed the",
    "data it was meant to keep honest.",
    "",
    "* A failed refresh now records last_error / last_error_at (alembic 0011)",
    "  and leaves metrics, period and computed_at alone. The row reads",
    "  `"measured on X, refresh failing since Y`" -- recoverable. A wiped row",
    "  is not.",
    "* refresh_all reports provider_outage when every strategy fails on",
    "  prices at once: that is one outage, not seven broken strategies, and",
    "  a run that should simply be retried should not read as a catalog that",
    "  fell apart.",
    "* Payload carries refresh_failing so a consumer can show good numbers",
    "  and a warning at the same time instead of collapsing both into `"ok`".",
    "",
    "BANNER RECONCILIATION, ROUND TWO. The check I added last round was",
    "nested inside the `"a strategy is applied`" branch, so on a book with no",
    "Beat the Market strategy it never ran at all -- I shipped a guard that",
    "could not fire. It is now its own section, unconditional, and it asserts",
    "the end state that actually matters: after /recommendations, /rules must",
    "agree. rule_banner also carries skipped_reason, because `"the rules agent",
    "degraded so nothing was retired`" and `"the reconciliation ran and failed`"",
    "look identical from outside, and that ambiguity is what produced several",
    "wrong hypotheses about this exact failure.",
    "",
    "The smoke now reports ``degraded`` explicitly for the same reason: no",
    "cards because nothing fired, and no cards because the agent raised, are",
    "indistinguishable without it.",
    "",
    "THE RED SUITE was a leftover sqlite test database, not the new column:",
    "create_all adds missing TABLES but never a column to a table that",
    "already exists, so 12 tests died with 'no such column' on a schema the",
    "code was right about. Deleting the file locally fixed the symptom and",
    "left the cause, which then went red again on the dev machine. conftest",
    "now removes the throwaway sqlite DB at the start of every run; Postgres",
    "URLs are untouched, since CI's test-postgres job gets its schema from",
    "migrations.",
    "",
    "+5 tests. ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head' -ForegroundColor Gray
Write-Host '  2. .\scripts\smoke\smoke-beat-market.ps1 -Refresh' -ForegroundColor Gray
Write-Host '     (retry if it reports provider_outage - the breaker needs to close)' -ForegroundColor DarkGray
Write-Host '  3. .\scripts\smoke\smoke-all.ps1' -ForegroundColor Gray
Write-Host ''
Write-Host 'Section 14 now reports `degraded` and skipped_reason - if the banner' -ForegroundColor Gray
Write-Host 'still disagrees, that output names the cause instead of us guessing.' -ForegroundColor Gray
