# SMOKE - PR 107: a job that fails says so.
#
#   .\scripts\smoke\smoke-pr107-jobs-fail-loudly.ps1 -Sha <short sha of the merge>
#
#   1  the scheduler runs and has all 11 jobs registered
#   2  market_refresh is in the history within minutes of boot (primed through the
#      guarded path now; before PR 107 it was called on the startup thread and
#      first appeared after 60 min)
#   3  no job in the history has last_ok = false (a FAIL here is real: the job
#      that failed is named, and before PR 107 it would have read green)
#   4  non-change: Today builds; the futures snapshot is served
#
# NEXT MORNING (record in STATUS): push_digest ran at 07:00 with last_ok true and
# recent_notifications shows the digest.
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 107"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 107 has no migration)"

$want = @('backtest_refresh', 'futures_warm', 'holding_review', 'market_refresh', 'nav_snapshot', 'price_refresh',
          'push_digest', 'push_evaluate', 'solver_refresh', 'standing_orders', 'strategy_signals')

Sec "1  scheduler"
$ps = Api GET "/api/v1/push/status"
$ids = @($ps.scheduler.jobs | ForEach-Object { $_.id })
$missing = @($want | Where-Object { $ids -notcontains $_ })
if ($ps.scheduler.scheduler_running -and $missing.Count -eq 0) { Ok "running, all 11 jobs registered" }
else { Bad ("running={0}; missing: {1}" -f $ps.scheduler.scheduler_running, ($missing -join ', ')) }

Sec "2  primed at boot"
$deadline = (Get-Date).AddSeconds(180)
while ($null -eq $ps.scheduler.history.market_refresh -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15; $ps = Api GET "/api/v1/push/status"
}
foreach ($j in @('market_refresh', 'futures_warm', 'price_refresh', 'standing_orders')) {
    if ($null -ne $ps.scheduler.history.$j) { Ok "$j ran (runs $($ps.scheduler.history.$j.runs))" } else { Bad "$j has not run since boot" }
}

Sec "3  no job reports a failure"
$hist = $ps.scheduler.history
$bad = @($hist.PSObject.Properties | Where-Object { $_.Value.last_ok -eq $false })
if ($bad.Count -eq 0) { Ok ("{0} job(s) in history, none failed" -f @($hist.PSObject.Properties).Count) }
else { foreach ($b in $bad) { Bad ("{0}: {1}" -f $b.Name, $b.Value.last_error) } }

Sec "4  non-change"
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
$f = Api GET "/api/v1/markets/futures"
if ($f -and @($f.futures).Count -gt 0) { Ok ("futures served: {0} items{1}" -f @($f.futures).Count, $(if ($f.stale) { ' (STALE)' } else { '' })) }
else { Bad "no futures served" }
Stop-Smoke
