# SMOKE - PR 130: round 2 - R6 and R7 join the catalogue, MH3 retired (D94).
#
#   .\scripts\smoke\smoke-pr130-round2.ps1 -Sha <short sha of the merge>
#
#   1  the two new entries are offered with cash as their off-state; MH3 is retired (D94) and moves to Hold the Nasdaq
#   2  once measured (03:30 or a manual refresh): both beat QQQ and carry a 2000-2016 run (SKIP until then)
#   3  nothing else changed: the earlier entries and retirements stand
#
# Public endpoints only. READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 130"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$bt = Api GET "/api/v1/strategies/backtests"
if (-not $bt) { Bad "/strategies/backtests did not answer"; Stop-Smoke }
function Row($id) { return @($bt.strategies | Where-Object { $_.id -eq $id })[0] }

Sec "1  the catalogue"
$r6 = Row "btm_tqqq_trend_cash"; $r7 = Row "btm_tqqq_trend_vol_brake"; $mh3 = Row "btm_qld_trend"
if ($r6 -and -not $r6.retired -and (@($r6.base_when_flat) -join ",") -eq "BIL") { Ok "3x Nasdaq Trend to Cash offered, off-state BIL" } else { Bad "btm_tqqq_trend_cash missing, retired or not cash when off" }
if ($r7 -and -not $r7.retired) { Ok "3x Nasdaq Trend with a Volatility Brake offered" } else { Bad "btm_tqqq_trend_vol_brake missing or retired" }
if ($mh3 -and $mh3.retired -and $mh3.retired.decision -eq "D94" -and $mh3.retired.move_to -eq "btm_qqq_hold") { Ok "MH3 retired (D94), moves to btm_qqq_hold" } else { Bad "MH3 not retired as D94" }

Sec "2  measured"
foreach ($r in @($r6, $r7)) {
    if (-not $r) { continue }
    $m = $null; if ($r.backtest) { $m = $r.backtest.metrics }
    if ($null -eq $m -or $null -eq $m.cagr_pct) { Skip "$($r.id) not measured yet (03:30 refresh)"; continue }
    $q = Row "btm_qqq_hold"; $qc = $null; if ($q -and $q.backtest -and $q.backtest.metrics) { $qc = $q.backtest.metrics.cagr_pct }
    if ($null -ne $qc -and [double]$m.cagr_pct -gt [double]$qc) { Ok ("{0}: {1}%/yr vs QQQ {2}%, worst drop -{3}%, tier {4}" -f $r.id, $m.cagr_pct, $qc, $m.max_drawdown_pct, $r.measured_risk) } else { Bad ("{0}: {1}%/yr does not beat QQQ {2}" -f $r.id, $m.cagr_pct, $qc) }
    $lh = $r.backtest.robustness.long_history
    if ($lh -and $lh.ok) { Note ("  2000-2016: {0}%/yr, worst drop -{1}%" -f $lh.cagr_pct, $lh.max_drawdown_pct) } else { Bad "$($r.id) has no 2000-2016 run" }
}

Sec "3  nothing else changed"
$ret = @($bt.strategies | Where-Object { $_.retired } | ForEach-Object { $_.id }) | Sort-Object
if (($ret -join ",") -eq "btm_qld_trend,btm_swing_breakout,btm_swing_dip") { Ok "retired: MH3 and the two swing rules" } else { Bad "retired: $($ret -join ',')" }
if (@($bt.strategies).Count -eq 11) { Ok "11 entries" } else { Bad "$(@($bt.strategies).Count) entries, expected 11" }

Stop-Smoke
