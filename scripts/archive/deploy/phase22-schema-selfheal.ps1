# PHASE 22 - add the strategy_backtests columns on startup, since alembic cannot
#            be reached from a laptop.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase22-schema-selfheal.ps1
#
# This closes the loop on the 500s. No manual migration needed: the app adds the
# two missing columns itself on the next boot, exactly as it already does for the
# six `plans` columns.
#
# After Railway reports Active, the refresh should compute all 7 strategies.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/main.py",
    "scripts/get-error-log.ps1",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/deploy/phase22-schema-selfheal.ps1"
)

Invoke-Suite -Focus @("tests/test_backtest_store_unavailable.py", "tests/test_backtest_service.py",
                      "tests/test_strategy_signals.py", "tests/test_tx_isolation.py")

$msg = @(
    "fix(db): self-heal the strategy_backtests columns on startup",
    "",
    "Production traceback:",
    "",
    "    asyncpg.exceptions.UndefinedColumnError:",
    "    column strategy_backtests.last_error does not exist",
    "",
    "create_all builds missing TABLES but never adds a column to one that",
    "already exists, so migration 0011's fields were absent in Postgres while",
    "the code selected them. That took out GET /strategies,",
    "/strategies/backtests and the backtest refresh, and it is the same",
    "schema-drift failure that had already gone red twice in the test suite.",
    "",
    "``alembic upgrade head`` is the documented answer and it failed twice for",
    "reasons that have nothing to do with the schema:",
    "",
    "  * run from the repo it reads .env, where DATABASE_URL is the local",
    "    sqlite file, so it migrated the wrong database and printed nothing;",
    "  * ``railway run alembic upgrade head`` injects Railway's DATABASE_URL,",
    "    which names a *.railway.internal host that does not resolve outside",
    "    their network -- socket.gaierror getaddrinfo failed.",
    "",
    "So the columns are now added in the startup DDL block that already",
    "exists for exactly this, alongside the six plans columns that were",
    "handled the same way. IF NOT EXISTS, inside the same try/except: it is",
    "idempotent, it costs one statement per boot, and it cannot regress a",
    "database that is already correct. The Alembic revision stays as the",
    "source of truth for a fresh install.",
    "",
    "Verified against a database with the pre-0011 table: /strategies,",
    "/strategies/backtests and /recommendations all answer 200.",
    "",
    "No new tests -- test_backtest_store_unavailable already reproduces the",
    "missing-column shape and asserts nothing 500s."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  .\scripts\smoke\smoke-beat-market.ps1 -Refresh' -ForegroundColor Gray
Write-Host ''
Write-Host 'Expect: refresh computes 7, store_error blank, 11/12 green.' -ForegroundColor Gray
Write-Host 'If the provider circuit breaker is still open (CircuitOpenError in the' -ForegroundColor DarkGray
Write-Host 'logs), the refresh abstains instead - wait a few minutes and re-run.' -ForegroundColor DarkGray
