# PHASE 12 - one card per pot of cash (for real this time) + latency attribution.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase12-one-cash-card.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/recommendations.py",
    "scripts",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_redeploy_cash.py", "tests/test_reconcile.py",
                      "tests/test_recommendations.py", "tests/test_recs_extras.py")

$msg = @(
    "fix(recs): reconcile cash cards after ALL agents; attribute endpoint latency",
    "",
    "1. TWO CONTRADICTORY CARDS REACHED THE USER.",
    "   Today showed 'Redeploy 3,884 of idle cash' (971 into SCHD, keeps 2,590",
    "   buffer) next to 'Add to SCHD - 7,899' (spend 5,827 cash AND sell 16 COW,",
    "   'still leaves 122 short'). Same pot of money, same ticker, two sizes, and",
    "   no way for the user to tell which to follow.",
    "",
    "   Phase 9 added a filter for exactly this, but placed it immediately after",
    "   _income_cost_recs -- while _war_room_recs, which emits the SCHD card,",
    "   runs later in build_recommendations. The filter ran before the card it",
    "   was meant to remove existed. Moved to just before _reconcile, after every",
    "   agent has contributed, and widened to any sized buy of a ticker the",
    "   redeploy card already funds regardless of which agent emitted it.",
    "",
    "2. LATENCY IS NOW ATTRIBUTABLE.",
    "   /recommendations went 24.2s -> ~5s with the provider cache, but there was",
    "   no way to see which of the ~10 agents owned the remaining 5s, so the next",
    "   step would have been another guess. _Stopwatch marks each agent and the",
    "   response carries timings_ms plus the three slowest. Measure, then fix."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy - see where the 5s actually goes:" -ForegroundColor Cyan
Write-Host '  $A = @{ "x-agent-key" = $env:IW_AGENT_KEY }'
Write-Host '  $B = "https://investwise-pro-production.up.railway.app"'
Write-Host '  Invoke-RestMethod "$B/api/v1/recommendations" -Headers $A | Select-Object -Expand timings_ms'
Write-Host "`nAnd confirm only ONE cash card remains:" -ForegroundColor Cyan
Write-Host '  (Invoke-RestMethod "$B/api/v1/recommendations" -Headers $A).recommendations | Select-Object title, @{n="kind";e={$_.apply.kind}}'
