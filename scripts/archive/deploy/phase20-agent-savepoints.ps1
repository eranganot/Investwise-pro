# PHASE 20 - one failing agent must not 500 /recommendations.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase20-agent-savepoints.ps1
#
# This is the fix for the 500 that broke smoke-all sections 1, 6, 8 and 10.
# No migration, no schema change, no UI change.
#
# NOTE: the underlying agent failure is still there -- this stops it taking the
# endpoint down and makes it show up in `degraded` instead. After deploying,
# smoke-beat-market section 14 prints which agent degraded, which is the next
# thing to fix and now finally visible.
#
# Also worth running in production if you have not: alembic upgrade head
# (migrations 0010 and 0011 exist; a missing column is one thing that makes an
# agent raise in the first place).

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/recommendations.py",
    "tests/test_tx_isolation.py",
    "scripts/get-error-log.ps1",
    "scripts/deploy/phase20-agent-savepoints.ps1"
)

Invoke-Suite -Focus @("tests/test_tx_isolation.py", "tests/test_recommendations.py",
                      "tests/test_accept_honesty.py", "tests/test_reconcile.py",
                      "tests/test_done_vs_ignored.py", "tests/test_strategy_signals.py",
                      "tests/test_backtest_service.py")

$msg = @(
    "fix(recs): one failing agent no longer 500s the endpoint on Postgres",
    "",
    "Production traceback, finally read rather than guessed at:",
    "",
    "    asyncpg.exceptions.InFailedSQLTransactionError: current transaction",
    "    is aborted, commands ignored until end of transaction block",
    "      File `"/code/app/services/recommendations.py`", line 813, in build_recommendations",
    "      File `"/code/app/services/recommendations.py`", line 207, in load_dismissed",
    "",
    "load_dismissed was the first casualty, not the cause. Every defensive",
    "handler in build_recommendations catches an agent's failure, logs it,",
    "marks the pipeline degraded and carries on with the SAME session. SQLite",
    "tolerates that, which is why all 448 local tests were green while",
    "/recommendations returned 500 in production. Postgres does not: the",
    "first failed statement aborts the transaction and everything after it",
    "raises, so a non-critical agent failing takes the whole endpoint down --",
    "and the traceback names a function far from the actual fault.",
    "",
    "Each database-touching agent (performance, war room, trading rules,",
    "strategy signals) now runs inside a SAVEPOINT. Its failure rolls back",
    "its own statements and the outer transaction survives.",
    "",
    "NOT session.rollback(), which was the first fix I wrote and was worse:",
    "rollback expires every loaded ORM object, so the positions this function",
    "keeps using afterwards each trigger a lazy reload and raise",
    "MissingGreenlet. Caught by the new end-to-end test, not by reasoning.",
    "",
    "The remaining handlers do no database work, so they are left alone --",
    "wrapping them would expire the identity map for no benefit.",
    "",
    "+3 tests. Two check the invariant STRUCTURALLY (every DB-touching agent",
    "sits inside a savepoint; the helper uses begin_nested and not rollback),",
    "because the symptom only appears on Postgres and a behavioural test on",
    "SQLite stays green while production burns. The third breaks an agent for",
    "real and asserts the endpoint still answers 200 with ``degraded`` set.",
    "",
    "ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head' -ForegroundColor Gray
Write-Host '  2. .\scripts\smoke\smoke-all.ps1              # section 1 should return 200' -ForegroundColor Gray
Write-Host '  3. .\scripts\smoke\smoke-beat-market.ps1      # section 14 names any degraded agent' -ForegroundColor Gray
Write-Host ''
Write-Host 'If an agent is still failing it will now appear in `degraded` rather' -ForegroundColor Gray
Write-Host 'than as a 500. Send me that name and the traceback for it.' -ForegroundColor Gray
