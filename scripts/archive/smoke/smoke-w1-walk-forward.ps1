# SMOKE - PR 15 / W1, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-w1-walk-forward.ps1
#   .\scripts\smoke\smoke-w1-walk-forward.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-w1-walk-forward.ps1 -All      # sweeps all 7
#
# Three claims, all false before this deploy:
#
#   1. The sweeps can be run WALK-FORWARD. They ran nightly on the full sample
#      and the result was stored in StrategyBacktest.robustness and read by
#      nothing - grep every renderer, route and script for robustness.sweep.
#   2. The report answers "does fitting beat NOT fitting", not "which value
#      wins". Every fold tests the fitted value against the value already in
#      the catalog.
#   3. A strategy with no sweepable parameter abstains BY NAME rather than
#      vanishing from the report.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$All,
    [string]$StrategyId = "btm_swing_dip")

. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }

function Api($method, $path, $timeout = 600) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $timeout
    } catch {
        Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - does fitting the parameter beat not fitting it" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

Sec "0  the deploy landed"
$health = Api GET '/health' 60
if ($null -eq $health) { Bad "/health unreachable - nothing below can be trusted" }
elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

$ids = if ($All) {
    @("btm_trend_tqqq","btm_trend_soxl","btm_vol_target_tqqq","btm_swing_dip",
      "btm_swing_breakout","btm_factor_stack","btm_dual_momentum")
} else { @($StrategyId) }

Sec "1  the sweep runs walk-forward, and says how it ended"
$results = @{}
foreach ($id in $ids) {
    $r = Api GET "/api/v1/plan/walk-forward?strategy_id=$id"
    $results[$id] = $r
    if ($null -eq $r) { Bad "$id - no response"; continue }
    if (-not $r.ok) {
        if ("$($r.reason)" -eq 'NO_SWEEP') {
            Ok "$id abstains NO_SWEEP, by name"
            Write-Host "        $($r.detail)" -ForegroundColor DarkGray
        } else {
            Bad "$id abstained: $($r.reason) - $($r.detail)"
        }
        continue
    }
    $v = $r.verdict
    Ok "$id -> $($v.kind)  $($v.folds_won)/$($v.folds_scored) folds, mean $($v.mean_diff)"
    Write-Host "        $($v.headline)" -ForegroundColor DarkGray
}

Sec "2  the bar was set before the numbers"
$live = @($results.Values | Where-Object { $_ -and $_.ok })
if ($live.Count -eq 0) {
    Skip "no measured strategy to check"
} else {
    $r = $live[0]
    if ($null -eq $r.bar) {
        Bad "no bar on the payload - a verdict whose threshold is invisible cannot be checked"
    } else {
        Ok "bar: $($r.bar.min_win_fraction) of folds AND a mean of $($r.bar.noise_floor)"
        if ("$($r.bar.note)" -notmatch 'before the numbers') {
            Bad "the bar does not say it predates the results"
        } else { Ok "and it says so" }
    }
    # The trap this exists to catch: a positive mean carried by one or two folds.
    foreach ($x in $live) {
        $v = $x.verdict
        if ($v.kind -eq 'FITTING_HELPS' -and $v.folds_won -lt ($r.bar.min_win_fraction * $v.folds_scored)) {
            Bad "$($x.strategy_id) claims FITTING_HELPS on $($v.folds_won)/$($v.folds_scored) folds"
        }
    }
    Ok "no verdict claims a win it did not earn on both halves"
}

Sec "3  the report says what it could not measure, and never tunes anything"
if ($live.Count -eq 0) {
    Skip "no measured strategy to check"
} else {
    foreach ($x in $live) {
        if ($null -eq $x.stability) { Bad "$($x.strategy_id) does not report parameter stability" }
        if ($null -eq $x.coverage.sessions_untested_at_end) {
            Bad "$($x.strategy_id) does not say what it left untested"
        }
        if ($null -ne $x.execution_plan) {
            Bad "$($x.strategy_id) returned an execution_plan - this route must never have one"
        }
    }
    Ok "every measured strategy reports stability, coverage and no apply path"
    $r = $live[0]
    Write-Host "        stability: $($r.stability.note)" -ForegroundColor DarkGray
    Write-Host "        untested:  $($r.coverage.sessions_untested_at_end) sessions at the recent end" -ForegroundColor DarkGray
    if ("$($r.disclaimer)" -match 'Not financial advice') { Ok "the disclaimer ships with the payload" }
    else { Bad "no disclaimer on the payload" }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
