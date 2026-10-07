# PHASE 14 - engine version tracks the metric surface, not just the numbers.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase14-engine-version.ps1
#
# Fixes the 8 skips from the phase 13 smoke run: stored rows were computed by an
# older engine but reported themselves as fresh.
#
# AFTER DEPLOY THE STORED ROWS WILL READ STALE ON PURPOSE (engine a2 -> a3).
# That is the fix working. Recompute to clear it:
#     POST /api/v1/strategies/backtests/refresh
# or just run the smoke script with -Refresh, which does it for you.
#
# No migration.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/backtest_service.py",
    "tests/test_backtest_service.py",
    "scripts/smoke/smoke-phase10-13.ps1",
    "scripts/deploy/phase14-engine-version.ps1"
)

Invoke-Suite -Focus @("tests/test_backtest_service.py", "tests/test_strategy_backtest.py")

$msg = @(
    "fix(backtest): bump the engine version when the metric surface changes",
    "",
    "Production smoke came back 21 passed / 0 failed / 8 skipped, and all",
    "eight skips were one root cause: stored rows had been written by the",
    "previous engine, but reported stale=false and were served as current.",
    "`"no stale results`" passed when it should not have.",
    "",
    "ENGINE_VERSION exists precisely to prevent this and I did not bump it",
    "when a2 gained sessions_per_year, limiting_ticker,",
    "history_start_by_ticker, history_capped_by_provider and a",
    "benchmark-relative overfitting verdict. Consumers then saw those fields",
    "simply absent -- which reads as `"not measured`" rather than `"measured by",
    "an engine that did not have this field`". The whole point of the version",
    "is that those two are distinguishable.",
    "",
    "* ENGINE_VERSION a2 -> a3, with the rule restated at the constant: bump",
    "  when a change alters a stored number OR adds a field to one.",
    "* Rows now carry stale_reason (`"engine_version`" | `"age`" | null) and",
    "  live_engine_version. The two staleness causes need different",
    "  responses: age waits for the 03:30 job, a version mismatch needs a",
    "  recompute now. Conflating them is what produced eight vague skips",
    "  instead of one actionable failure.",
    "* Smoke reports the version mismatch once, as a FAIL naming the command",
    "  that fixes it, and stops emitting a per-strategy skip for fields an",
    "  older row could not have carried. It also no longer prints a bare",
    "  `"'likely overfitted' - decayed  vs benchmark`" with empty numbers when",
    "  the stored verdict predates the benchmark-relative judgement.",
    "",
    "+1 test. ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  .\scripts\smoke\smoke-phase10-13.ps1 -Refresh' -ForegroundColor Gray
Write-Host '  Expect: 0 failed, 0 skipped. Any remaining skip is a real gap.' -ForegroundColor DarkGray
