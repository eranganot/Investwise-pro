# PHASE 7 - surplus cash gets a sized, executable home.
#
# After accepting a stop-loss the proceeds land in cash and the mix drifts off
# plan. The app noticed ("Put idle cash to work") but the card was apply:none -
# it named nothing, sized nothing, and could execute nothing.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase7-redeploy-cash.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/recommendations.py",
    "tests/test_redeploy_cash.py",
    # Test-isolation fix: these two patched module globals with bare assignments,
    # which leaked into every later test and starved
    # test_trading_rule_suggestions of its holdings. Now via monkeypatch.
    "tests/test_rule_resolution.py",
    "tests/test_cash_pricing_guard.py",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_redeploy_cash.py", "tests/test_funding_service.py", "tests/test_reconcile.py")

$msg = @(
    "feat(cash): turn idle cash into a sized, executable redeployment plan",
    "",
    "Reported after accepting a triggered stop-loss: 'now I have more cash - so",
    "where should I put it? hold it or reinvest?'. The portfolio sat at 30% cash",
    "against a 3% floor, and the only card about it was advisory.",
    "",
    "New _redeploy_cash_recs emits ONE card that answers the question concretely.",
    "Allocation is derived, never invented:",
    "  * spendable  = cash above the objective's floor (Preserve 10% .. Grow 3%)",
    "  * candidates = each asset class under its target weight, filled first by",
    "    holdings already owned that sit below their share of that class, then by",
    "    a screener pick for any class with no representation at all",
    "  * every leg is clipped by the single-name concentration cap, and legs below",
    "    MIN_TRADE_ILS are dropped rather than rounded up into dust trades",
    "",
    "New redeploy_cash apply-kind executes it: each leg priced live, cash debited",
    "leg by leg so a price move between building and accepting the card can only",
    "shorten the list - never spend money that isn't there. Topping up an existing",
    "position blends the cost basis so gain/loss stays honest.",
    "",
    "The old advisory 'Put idle cash to work' card is dropped when this one fires;",
    "two cards about the same shekels is exactly what _reconcile exists to stop.",
    "",
    "+5 tests (test_redeploy_cash.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy - see the plan WITHOUT executing it:" -ForegroundColor Cyan
Write-Host '  $H = @{ "x-agent-key" = $env:IW_AGENT_KEY }'
Write-Host '  $B = "https://investwise-pro-production.up.railway.app"'
Write-Host '  $c = (Invoke-RestMethod "$B/api/v1/recommendations" -Headers $H).recommendations | Where-Object { $_.apply.kind -eq "redeploy_cash" }'
Write-Host '  $c.title; $c.action; $c.apply.legs | Format-Table ticker, amount_ils, reason'
Write-Host "`nThen tap Accept in the app to execute it." -ForegroundColor Cyan
