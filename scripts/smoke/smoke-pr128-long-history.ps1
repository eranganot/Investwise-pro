# SMOKE - PR 128: leverage over 2000-2016 (D92), risk parity, the two Very-high candidates (not added).
#
#   .\scripts\smoke\smoke-pr128-long-history.ps1 -Sha <short sha of the merge>
#
#   1  the four flagged strategies carry a long-history run once re-measured (SKIP while stale)
#   2  the shell that draws the new chip is served (sw.js iw-v62)
#   3  nothing else changed: the same 8 strategies, neither Very-high candidate added
#
# Public endpoints only. READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 128"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$bt = Api GET "/api/v1/strategies/backtests"
if (-not $bt) { Bad "/strategies/backtests did not answer"; Stop-Smoke }
$stale = @($bt.stale)

Sec "1  the 2000-2016 run"
foreach ($sid in @("btm_trend_tqqq", "btm_trend_soxl", "btm_vol_target_tqqq", "btm_qqq_hold")) {
    $s = @($bt.strategies | Where-Object { $_.id -eq $sid })[0]
    if (-not $s -or -not $s.backtest) { Bad "$sid has no row"; continue }
    if ($stale -contains $sid) { Skip "$sid not re-measured yet (03:30 refresh)"; continue }
    $lh = $null
    if ($s.backtest.robustness) { $lh = $s.backtest.robustness.long_history }
    if ($lh -and $lh.ok -and $lh.end -lt "2016-10-01" -and $null -ne $lh.max_drawdown_pct) {
        Ok ("{0}: {1}%/yr over {2}..{3}, worst drop -{4}% ({5}: {6})" -f $sid, $lh.cagr_pct, $lh.start, $lh.end, $lh.max_drawdown_pct, $lh.compared_with, $lh.passes)
    } elseif ($lh) { Bad ("{0}: long history did not measure ({1} {2})" -f $sid, $lh.reason, $lh.detail) }
    else { Bad "$sid carries no long_history" }
}

Sec "2  the shell"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30 } catch { $sw = $null }
if ($sw -and $sw.Content -match "iw-v62") { Ok "sw.js iw-v62" } else { Bad "sw.js is not iw-v62" }

Sec "3  nothing else changed"
$ids = @($bt.strategies | ForEach-Object { $_.id }) -join ","
$want = "btm_trend_tqqq,btm_trend_soxl,btm_vol_target_tqqq,btm_swing_dip,btm_swing_breakout,btm_factor_stack,btm_dual_momentum,btm_qqq_hold"
if ($ids -eq $want) { Ok "the same 8 strategies (VH1 and VH2 failed the gate and were not added)" } else { Bad "catalogue changed: $ids" }

Stop-Smoke
