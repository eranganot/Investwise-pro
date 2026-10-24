# SMOKE - PR 146b: Today no longer computes the behind-your-goal projection while the goal story owns the goal.
#
#   .\scripts\smoke\smoke-pr146b-today-skips-the-unused-projection.ps1 -Sha <short sha of the merge>
#
#   1  the goal verdict is in goal mode (the story that owns the goal) -- else SKIP
#   2  five warm reads: goal_projection median under 50 ms (it was 250 ms on 6b56882)
#   3  the total server time is reported, for the record
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 146b"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  the goal story owns the goal"
# 146c: goal-mode is not enough -- a verdict solved for other sleeves is not current, and then the
# older behind-your-goal card speaks and needs the projection (seen live on b951765).
$t0 = Api GET "/api/v1/recommendations"
$behind = @(@($t0.recommendations) + @($t0.more_recommendations) | Where-Object { $_.title -like "You're behind*" })
if ($behind.Count) { Skip "the behind-your-goal card is on Today (the goal verdict is not current): it needs the projection"; Stop-Smoke }
Ok "the goal story owns the goal"

Sec "2  goal_projection, five warm reads"
$null = Api GET "/api/v1/recommendations"
$gp = @(); $tot = @()
for ($i = 0; $i -lt 5; $i++) {
    $t = (Api GET "/api/v1/recommendations").timings_ms
    $gp += [double]$t.goal_projection
    $tot += [double](@($t.PSObject.Properties | Where-Object { $_.Name -ne "slowest" -and ($_.Value -is [int] -or $_.Value -is [long] -or $_.Value -is [double]) } | Measure-Object -Property Value -Sum).Sum)
}
$m = ($gp | Sort-Object)[2]; $mt = ($tot | Sort-Object)[2]
if ($m -lt 50) { Ok ("goal_projection median {0} ms (was 250)" -f $m) } else { Bad ("goal_projection median {0} ms" -f $m) }
Ok ("Today server time median {0} ms (was ~500)" -f $mt)
Stop-Smoke
