# SMOKE - PR 120: the goal has one chance, and Today quotes it.
#
#   .\scripts\smoke\smoke-pr120-one-goal-chance.ps1 -Sha <short sha of the merge>
#
#   1  Today's On track (/health-check) equals the Plan headline
#      (/plan/projection goal_headline.p_goal_pct), basis goal_model. Before PR
#      120 the live book read 35 (no-rules projection) against Plan's 31.
#   2  the page: the Today "Chance of hitting your goal" row reads goal_headline;
#      sw.js is iw-v52
#   3  non-change: Today builds with nothing degraded
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 120"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 120 has no migration)"

Sec "1  one chance"
$h = Api GET "/api/v1/health-check"
$p = Api GET "/api/v1/plan/projection"
$gh = $p.goal_headline
if ($null -eq $gh -or $null -eq $gh.p_goal_pct) {
    if ("$($h.on_track_detail)" -match 'rough estimate') { Skip "no fresh goal verdict; Today's figure is labelled a rough estimate" }
    else { Bad "no goal verdict and Today's figure is not labelled: $($h.on_track_detail)" }
} else {
    $want = [math]::Round([double]$gh.p_goal_pct)
    if ([math]::Abs([double]$h.on_track - $want) -le 1 -and $h.on_track_basis -eq 'goal_model') {
        Ok ("Today On track {0} = Plan {1}% ({2})" -f $h.on_track, $gh.p_goal_pct, $h.on_track_detail)
    } else { Bad ("Today On track {0} ({1}) vs Plan {2}%" -f $h.on_track, $h.on_track_basis, $gh.p_goal_pct) }
    Note ("the chart's own no-rules run: {0:P1}" -f [double]$p.probability_meets_target)
}

Sec "2  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($html -and $html -match "const gpc=\(r\.goal_headline") { Ok "Today chance row reads goal_headline" } else { Bad "Today chance row still reads the chart's figure" }
$sw = $null; try { $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($sw -and $sw -match "iw-v52") { Ok "sw.js iw-v52" } else { Bad "sw.js not bumped" }

Sec "3  non-change"
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
