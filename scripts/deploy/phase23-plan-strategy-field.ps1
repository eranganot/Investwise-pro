# PHASE 23 - GET /plan reports which strategy is applied.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase23-plan-strategy-field.ps1
#
# One field. No migration, no UI change, no behaviour change to anything that
# was already working -- but it is what makes section 13 of the Beat the Market
# smoke able to see the strategy it just applied.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/api/routes/plan.py",
    "tests/test_strategy_signals.py",
    "scripts/deploy/phase23-plan-strategy-field.ps1"
)

Invoke-Suite -Focus @("tests/test_strategy_signals.py", "tests/test_plan_explore.py",
                      "tests/test_backtest_service.py", "tests/test_tx_isolation.py")

$msg = @(
    "fix(plan): report which strategy is applied",
    "",
    "Live smoke, two lines apart:",
    "",
    "    PASS  applied 'btm_trend_tqqq'",
    "    FAIL  no strategy applied - signals and discipline cannot be exercised",
    "",
    "The apply worked. The read could not see it: _plan_dict has never",
    "included ``strategy``, although the column has existed since migration",
    "0007_plan_strategy and apply_strategy writes it. Only",
    "/strategies/{id}/preview read it back, so from every other caller --",
    "the smoke, the Plan UI, anything asking `"what am I running?`" -- applying",
    "a strategy looked like a no-op.",
    "",
    "That also made the whole rule-based family unreachable in practice: the",
    "signal evaluation and the discipline card both start from",
    "active_strategy_id, which resolves the plan's strategy. Reading it back",
    "is how anything knows the pipeline is live.",
    "",
    "The field is now always present -- null when no plan is configured,",
    "rather than absent -- so a caller can distinguish `"no strategy`" from `"an",
    "older build that does not report one`".",
    "",
    "+1 test covering both catalogs: a rule-based id and a static basket",
    "resolve through the same path and both come back on /plan."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  .\scripts\smoke\smoke-beat-market.ps1 -ApplyStrategy btm_trend_tqqq' -ForegroundColor Gray
Write-Host ''
Write-Host 'Section 13 should now evaluate the signal instead of failing.' -ForegroundColor Gray
