# FULL END-TO-END SMOKE - everything, in order, with one verdict at the end.
#
#   .\scripts\smoke\smoke-e2e.ps1
#   .\scripts\smoke\smoke-e2e.ps1 -Full        # + recompute backtests, + apply a strategy (WRITES)
#
# Runs:
#   1. a deploy freshness check   - is production actually running your HEAD?
#   2. smoke-all.ps1              - phases 1-10
#   3. smoke-beat-market.ps1      - phases 10-17 (contributions, backtests, signals)
#
# The freshness check exists because two separate debugging rounds were spent on
# results from a container that predated the fix being tested. A smoke run
# against a stale deploy is worse than no smoke run: it produces confident,
# wrong conclusions.

param(
    # Recompute backtests and apply a strategy so the signal and discipline
    # checks can actually run. -Full WRITES: it changes your plan's objective,
    # risk tolerance and strategy. Without it those checks fail by design.
    [switch]$Full,
    [string]$ApplyStrategy = "btm_trend_tqqq",
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app"
)

$ErrorActionPreference = 'Continue'
if (-not $env:IW_AGENT_KEY) {
    $env:IW_AGENT_KEY = "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070"
}
$here = $PSScriptRoot
$repo = (Resolve-Path "$here\..\..").Path

Write-Host ""
Write-Host "===== INVESTWISE END-TO-END SMOKE =====" -ForegroundColor Magenta

# ---------- 0. is production running what we think it is? ----------
Write-Host "`n0. Deploy freshness" -ForegroundColor Cyan
Push-Location $repo
$localHead = (git rev-parse --short HEAD 2>$null)
$localMsg = (git log -1 --format=%s 2>$null)
git fetch origin --quiet 2>$null
$ahead = (git rev-list --count origin/main..HEAD 2>$null)
Pop-Location

Write-Host "   local HEAD : $localHead  $localMsg" -ForegroundColor DarkGray
if ($ahead -and [int]$ahead -gt 0) {
    Write-Host "  WARN  $ahead commit(s) not pushed - production cannot contain them" -ForegroundColor Yellow
} else {
    Write-Host "  OK    local HEAD is pushed" -ForegroundColor Green
}

# The app reports app_version, not a commit, so this cannot prove which build is
# live. What it CAN do is prove the app is up and answering before we interpret
# anything below it.
try {
    $h = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
    $health = Invoke-RestMethod -Uri "$BaseUrl/api/v1/health-check" -Headers $h -TimeoutSec 30
    Write-Host "  OK    production answering (health score $($health.wealth_health_score))" -ForegroundColor Green
} catch {
    Write-Host "  FAIL  production not answering: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "        Everything below would be meaningless. Check Railway." -ForegroundColor Yellow
    exit 1
}
Write-Host "   If a check below contradicts a fix you just deployed, confirm Railway" -ForegroundColor DarkGray
Write-Host "   shows '$localHead' as Active before believing the result." -ForegroundColor DarkGray

# ---------- 1. the original suite ----------
Write-Host "`n`n===== PART 1: smoke-all (phases 1-10) =====" -ForegroundColor Magenta
& "$here\smoke-all.ps1"
$part1 = $LASTEXITCODE

# ---------- 2. the Beat the Market work ----------
Write-Host "`n`n===== PART 2: smoke-beat-market (phases 10-17) =====" -ForegroundColor Magenta
if ($Full) {
    & "$here\smoke-beat-market.ps1" -Refresh -ApplyStrategy $ApplyStrategy -BaseUrl $BaseUrl
} else {
    & "$here\smoke-beat-market.ps1" -BaseUrl $BaseUrl
}
$part2 = $LASTEXITCODE

Write-Host "`n`n===== END-TO-END COMPLETE =====" -ForegroundColor Magenta
if (-not $Full) {
    Write-Host "Ran WITHOUT -Full, so these are expected and not real failures:" -ForegroundColor DarkGray
    Write-Host "  * section 13 (signals) fails unless a Beat the Market strategy is applied" -ForegroundColor DarkGray
    Write-Host "  * backtests are read from storage, not recomputed" -ForegroundColor DarkGray
    Write-Host "Re-run with -Full to exercise both (it writes to your plan)." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Read the two PASS/FAIL/SKIP totals above. A SKIP is not a PASS." -ForegroundColor Cyan
Write-Host "If anything failed, the fastest next step is the traceback:" -ForegroundColor Cyan
Write-Host "  .\scripts\get-error-log.ps1 -Trigger -TriggerPath '<the failing path>'" -ForegroundColor Gray
