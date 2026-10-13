# SMOKE - PR 126: the selection engine (rank_select), gate test 3 vs own instruments (D90), selection edge (D91).
#
#   .\scripts\smoke\smoke-pr126-selection-engine.ps1 -Sha <short sha of the merge>
#
#   1  the engine is a7
#   2  once re-measured (03:30, or a manual refresh): every row says what its overfitting verdict was judged against,
#      and keeps the old SPY reading beside it (SKIP while rows are still a6)
#   3  nothing else changed: the same 8 strategies, none of them a selection rule yet
#
# Public endpoints only. READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 126"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$bt = Api GET "/api/v1/strategies/backtests"
if (-not $bt) { Bad "/strategies/backtests did not answer"; Stop-Smoke }

Sec "1  engine"
if ($bt.engine_version -eq "a7") { Ok "engine a7" } else { Bad "engine $($bt.engine_version), expected a7" }

Sec "2  the verdict says what it was judged against"
$stale = @($bt.stale)
$fresh = @($bt.strategies | Where-Object { $_.backtest -and $_.backtest.ok -and ($stale -notcontains $_.id) })
if ($fresh.Count -eq 0) {
    Skip ("no row re-measured on a7 yet ({0} stale): the 03:30 refresh recomputes them; run again after it" -f $stale.Count)
} else {
    foreach ($s in $fresh) {
        $o = $null
        if ($s.backtest.robustness) { $o = $s.backtest.robustness.out_of_sample }
        if (-not $o -or -not $o.verdict) { Note "$($s.id): no out-of-sample fold"; continue }
        if ($o.verdict_basis -like "*own instruments*" -and $null -ne $o.own_hold_decay_pct -and $o.verdict_vs_benchmark) {
            Ok ("{0}: {1} vs own instruments (SPY reading: {2}; decay {3} vs holding {4})" -f $s.id, $o.verdict, $o.verdict_vs_benchmark, $o.cagr_decay_pct, $o.own_hold_decay_pct)
        } else { Bad ("{0}: verdict basis is '{1}'" -f $s.id, $o.verdict_basis) }
    }
}

Sec "3  nothing else changed"
$ids = @($bt.strategies | ForEach-Object { $_.id }) -join ","
$want = "btm_trend_tqqq,btm_trend_soxl,btm_vol_target_tqqq,btm_swing_dip,btm_swing_breakout,btm_factor_stack,btm_dual_momentum,btm_qqq_hold"
if ($ids -eq $want) { Ok "the same 8 strategies, in order" } else { Bad "catalogue changed: $ids" }
$sel = @($bt.strategies | Where-Object { $_.backtest -and $_.backtest.metrics -and $null -ne $_.backtest.metrics.selection_edge_pct })
if ($sel.Count -eq 0) { Ok "no selection rule in the catalogue yet (PR 127 adds them)" } else { Bad "$($sel.Count) rows carry a selection edge already" }

Stop-Smoke
