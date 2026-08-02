# PHASE 6 - triggered rules clear once dealt with; cash isn't a tradeable holding.
#
# Follow-up to the reported "4 trading rules triggered: CASH, MSFT, META, META -
# I already took actions". Two bugs, one banner.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase6-rule-clearing.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/rules_service.py",
    "app/services/recommendations.py",
    "tests/test_rule_resolution.py",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_rule_resolution.py", "tests/test_rule_actions.py")

$msg = @(
    "fix(rules): clear triggered rules once acted on; keep cash out of rules",
    "",
    "Reported: '4 trading rules triggered: CASH, MSFT, META, META - I already",
    "took actions'. Two independent bugs behind one banner.",
    "",
    "1. Acting never cleared the rule. `triggered` latches True and only price",
    "   alerts ever reset it, so Accept / Mark-as-done / Ignore all left the flag",
    "   set. The banner reads the rules table directly, so it kept counting work",
    "   the user had already done - nagging about a closed position.",
    "   Now: resolve_rule() stamps the audit event AND clears the flag. A fired",
    "   stop-loss / take-profit / trailing stop / buy-dip is a ONE-SHOT order, so",
    "   it is consumed (active=False) - leaving it armed would re-fire on the",
    "   identical condition a second later. A max-weight cap or price alert is a",
    "   standing condition, so it re-arms instead. Wired into apply, dismiss and",
    "   complete, so every exit from a rule card closes its loop.",
    "",
    "2. CASH was in the rule position index, so the rule suggester offered stops",
    "   on a cash balance. Harmless while the row was mispriced at ~72.9 - but",
    "   the phase 1 pricing fix reset it to its true 1.0, which those stops read",
    "   as a 98% crash and fired. Cash is now excluded from the index entirely.",
    "",
    "Also: a rule whose ticker is no longer a tradeable holding (sold, or CASH)",
    "is retired rather than skipped. Skipping left a latched `triggered` flag",
    "counting toward the banner with no card able to clear it - which is why the",
    "stale CASH entries would otherwise have persisted after fix 2.",
    "",
    "+3 tests (test_rule_resolution.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy, force one evaluation to retire the stale rules:" -ForegroundColor Cyan
Write-Host '  $H = @{ "x-agent-key" = $env:IW_AGENT_KEY }'
Write-Host '  $B = "https://investwise-pro-production.up.railway.app"'
Write-Host '  Invoke-RestMethod -Method POST "$B/api/v1/rules/evaluate" -Headers $H'
Write-Host '  (Invoke-RestMethod "$B/api/v1/rules" -Headers $H).rules | Where-Object { $_.triggered } | Select-Object ticker, rule_type, triggered, active'
Write-Host "`nExpect: no CASH rows, and only rules you have NOT yet acted on." -ForegroundColor Cyan
