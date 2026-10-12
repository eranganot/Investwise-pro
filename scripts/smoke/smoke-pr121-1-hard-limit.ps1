# SMOKE - PR 121.1: the halving limit is a hard limit (D83).
#
#   .\scripts\smoke\smoke-pr121-1-hard-limit.ps1 -Sha <short sha of the merge>
#
#   1  a goal verdict solved by this build says whether today's rules break the
#      halving limit (rules.tuning.breach). A verdict solved before the deploy
#      is SKIPped.
#   2  over the limit with a setup inside it: the retune is material, and Today
#      shows "Bring your crash risk inside your limit"; inside the limit the
#      card, if any, is the odds-gain retune
#   3  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 121.1"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1+2  the hard limit"
$f = Api GET "/api/v1/plan/frontier"
$tu = $f.rules.tuning
$r = Api GET "/api/v1/recommendations"
$cards = @($r.recommendations) + @($r.more_recommendations)
$protect = @($cards | Where-Object { $_.id -like "rec_goal_protect_*" })
if (-not $tu -or $null -eq $tu.breach) { Skip ("verdict solved {0} predates this build; re-run after a re-solve" -f $f.computed_at) }
else {
    $on = $f.rules.on
    Note ("today: halving {0}% vs limit {1}%; goal {2}%" -f $on.p_halve_pct, $tu.halve_floor_pct, $on.p_goal_pct)
    if ([bool]$tu.breach -ne ([double]$on.p_halve_pct -gt [double]$tu.halve_floor_pct)) { Bad "breach flag disagrees with the numbers" }
    elseif ($tu.breach -and $tu.best) {
        if ($tu.material) { Ok ("over the limit -> material: guard {0}%, halving {1}%, goal {2}%" -f $tu.best.guard_pct, $tu.best.stats.p_halve_pct, $tu.best.stats.p_goal_pct) } else { Bad "over the limit with a setup inside it, but not material" }
        if ($protect.Count -eq 1 -and $protect[0].title -like "Bring your crash risk inside your limit*") { Ok ("Today: '{0}'" -f $protect[0].title) }
        else { Bad ("Today has no breach card ({0} protect cards; it may be dismissed)" -f $protect.Count) }
    } elseif ($tu.breach) { Ok "over the limit and no setup inside it: nothing to offer" }
    else { Ok ("inside the limit; protect cards on Today: {0}" -f $protect.Count) }
}

Sec "3  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
