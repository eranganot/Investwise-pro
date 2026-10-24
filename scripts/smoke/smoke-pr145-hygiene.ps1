# SMOKE - PR 145: hygiene. smoke-all is read-only; the gate is Postgres (docs); the pre-push hook is checked in.
#
#   .\scripts\smoke\smoke-pr145-hygiene.ps1 -Sha <short sha of the merge>
#
#   1  the rule check smoke-all now READS (price_refresh's recorded result) is there and recent
#   2  NOT CHANGED: the app answers as before (Today builds, nothing degraded beyond the AI credits)
#
# READ-ONLY. ASCII ONLY. (This PR changes no app code; the claims are in the repo and its tests.)

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 145"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  the recorded rule check"
$pr = (Api GET "/api/v1/push/status").scheduler.history.price_refresh
if (-not $pr) { Bad "no price_refresh in the job history" }
elseif ($pr.last_ok -and $pr.last_result.rules) {
    $age = ([datetime]::UtcNow - ([datetime]$pr.last_run).ToUniversalTime()).TotalMinutes
    if ($age -le 75) { Ok ("rule check at {0} ({1:N0} min ago), {2} newly triggered" -f $pr.last_run, $age, $pr.last_result.rules.triggered) }
    else { Bad ("the last rule check is {0:N0} min old" -f $age) }
} else { Bad ("the last rule check did not run cleanly: {0}" -f $pr.last_error) }

Sec "2  not changed"
$t = Api GET "/api/v1/recommendations"
if ($null -eq $t) { Bad "Today did not build" }
else {
    $deg = @($t.degraded | Where-Object { $_ -notmatch "ai|gemini|llm" })
    if ($deg.Count) { Bad ("Today degraded: {0}" -f ($deg -join ", ")) } else { Ok ("Today builds: {0} card(s), {1} heads-up(s)" -f @($t.recommendations).Count, @($t.headsups).Count) }
}
Stop-Smoke
