# SMOKE - PR 122: an ignored goal card stays ignored (hotfix).
#
#   .\scripts\smoke\smoke-pr122-goal-cards-stay-ignored.ps1 -Sha <short sha of the merge>
#
#   1  every goal/frontier card on Today (and in the hidden list) has a stable
#      id ending in _book -- no solve-row uuid in it
#   2  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY. (Whether Ignore holds across a re-solve is the
# owner's Pixel check: Ignore the card, wait for the nightly solve.)

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 122"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  stable ids"
$r = Api GET "/api/v1/recommendations"
$cards = @($r.recommendations) + @($r.more_recommendations)
$goal = @($cards | Where-Object { $_.id -like "rec_goal_*" -or $_.id -like "rec_frontier_*" })
$bad = @($goal | Where-Object { $_.id -notmatch "_book$" })
if ($goal.Count -eq 0) { Skip "no goal card on Today right now (ignored or none offered)" }
elseif ($bad.Count -eq 0) { Ok ("{0} goal card(s), stable ids: {1}" -f $goal.Count, (($goal | ForEach-Object { $_.id }) -join ", ")) }
else { Bad ("solve-row ids still present: {0}" -f (($bad | ForEach-Object { $_.id }) -join ", ")) }

Sec "2  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
