# SMOKE - PR 146c: the goal projection is kept for the same inputs, so a warm Today does not search again.
#
#   .\scripts\smoke\smoke-pr146c-projection-kept.ps1 -Sha <short sha of the merge>
#
#   1  one read primes it; five warm reads: goal_projection median under 50 ms (250 ms on b951765),
#      whether the behind-your-goal card speaks (cached) or the goal story owns the goal (skipped, 146b)
#   2  the Today server time, for the record
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 146c"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  goal_projection on warm reads"
$p = Api GET "/api/v1/recommendations"
$behind = @(@($p.recommendations) + @($p.more_recommendations) | Where-Object { $_.title -like "You're behind*" }).Count
$gp = @(); $tot = @()
for ($i = 0; $i -lt 5; $i++) {
    $t = (Api GET "/api/v1/recommendations").timings_ms
    $gp += [double]$t.goal_projection
    $tot += [double](@($t.PSObject.Properties | Where-Object { $_.Name -ne "slowest" -and ($_.Value -is [int] -or $_.Value -is [long] -or $_.Value -is [double]) } | Measure-Object -Property Value -Sum).Sum)
}
$m = ($gp | Sort-Object)[2]; $mt = ($tot | Sort-Object)[2]
$why = if ($behind) { "behind card on Today: kept answer" } else { "goal story owns the goal: skipped" }
if ($m -lt 50) { Ok ("goal_projection median {0} ms ({1}; was 250)" -f $m, $why) } else { Bad ("goal_projection median {0} ms ({1})" -f $m, $why) }

Sec "2  for the record"
Ok ("Today server time median {0} ms (was ~505)" -f $mt)
Stop-Smoke
