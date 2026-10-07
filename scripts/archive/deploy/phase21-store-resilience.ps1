# PHASE 21 - a missing column in an optional table must not 500 the Plan page.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase21-store-resilience.ps1
#
# ############################################################################
# RUN THIS FIRST, IT IS THE ACTUAL FIX:
#
#     alembic upgrade head
#
# Migrations 0010 and 0011 have not been applied in production. That is why
# /strategies and /strategies/backtests return 500 and why strategy_signals
# degrades. This deploy stops a schema gap being FATAL; the migration is what
# makes the measurements work again.
# ############################################################################
#
# No schema change in this commit. No UI change.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/backtest_service.py",
    "app/api/routes/strategy.py",
    "tests/test_backtest_store_unavailable.py",
    "scripts/deploy/phase21-store-resilience.ps1"
)

Invoke-Suite -Focus @("tests/test_backtest_store_unavailable.py", "tests/test_backtest_service.py",
                      "tests/test_strategy_signals.py", "tests/test_tx_isolation.py",
                      "tests/test_strategy_profile.py", "tests/test_plan_explore.py")

$msg = @(
    "fix(strategy): an unreadable backtest store must not take down the Plan page",
    "",
    "Reproduced, not inferred. Recreating production's exact schema -- the",
    "strategy_backtests table as migration 0009 left it, without the",
    "last_error / last_error_at columns migration 0011 adds:",
    "",
    "    GET /api/v1/strategies            -> 500",
    "    GET /api/v1/strategies/backtests  -> 500",
    "",
    "which is precisely what the live smoke reported, and it also explains",
    "``degraded: strategy_signals``, since discipline_recs reads the same table.",
    "",
    "The real cause is operational: the deploy landed before",
    "``alembic upgrade head`` ran. But an OPTIONAL side table should never be",
    "able to do this. The four original strategy families need no backtest at",
    "all, and a measured strategy can say `"not measured yet`" perfectly well.",
    "",
    "get_many now reads inside a SAVEPOINT and returns {} when the table",
    "cannot be read, exposing ``backtest_store_error`` / ``store_error`` so a",
    "caller can tell `"could not read the measurements`" -- a deploy fault --",
    "from `"nothing computed yet`", which is just Tuesday. The savepoint means",
    "a failure here cannot abort the caller's transaction on Postgres and",
    "take down everything after it.",
    "",
    "After the fix, against that same broken schema:",
    "",
    "    /api/v1/strategies            -> 200",
    "    /api/v1/strategies/backtests  -> 200",
    "    /api/v1/recommendations       -> 200",
    "",
    "+4 tests that rebuild the pre-0011 table and assert all three still",
    "answer. They also drop it on teardown: create_all adds missing TABLES",
    "but never a column to one that already exists, so a sabotaged table",
    "would leak into every later test in the session -- the same schema-drift",
    "failure this module exists to reproduce, which was easy to cause twice",
    "in one afternoon.",
    "",
    "ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'ORDER MATTERS:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head        <- the actual fix for the 500s' -ForegroundColor Yellow
Write-Host '  2. .\scripts\smoke\smoke-beat-market.ps1 -Refresh' -ForegroundColor Gray
Write-Host '  3. .\scripts\smoke\smoke-all.ps1' -ForegroundColor Gray
Write-Host ''
Write-Host 'If /strategies still reports backtest_store_error after the migration,' -ForegroundColor Gray
Write-Host 'that string names the real database error - send it to me.' -ForegroundColor Gray
