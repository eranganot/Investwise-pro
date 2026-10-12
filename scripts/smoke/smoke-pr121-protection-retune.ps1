# SMOKE - PR 121: the protection retune searches properly (D82).
#
#   .\scripts\smoke\smoke-pr121-protection-retune.ps1 -Sha <short sha of the merge>
#
#   1  the four new settings exist (27 in all) and the live guard's defaults are
#      unchanged (sells 25%, re-arms 2.5 points under); guard levels tried widen
#      to 15/17.5/20/25
#   2  a goal verdict solved by this build carries the wider search: tried =
#      candidates, at least the grid's pass-1 size, one current setup.  A verdict
#      solved before the deploy is SKIPped (the nightly solve, or Plan > Card
#      thresholds > Save, re-solves it)
#   3  nothing else changed: /portfolio, Today
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 121"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the settings"
$p = Api GET "/api/v1/plan"
$names = @($p.thresholds_spec.PSObject.Properties.Name)
$new = @("guard_sell_fraction_pct", "guard_rearm_gap_pts", "guard_fractions_tried", "guard_rearm_gaps_tried")
$missing = @($new | Where-Object { $names -notcontains $_ })
if ($names.Count -eq 27 -and $missing.Count -eq 0) { Ok "27 settings, the 4 new ones present" } else { Bad ("{0} settings; missing: {1}" -f $names.Count, ($missing -join ",")) }
$d = $p.thresholds_defaults
if ([double]$d.guard_sell_fraction_pct -eq 25 -and [double]$d.guard_rearm_gap_pts -eq 2.5) { Ok "live guard defaults unchanged: sells 25%, re-arms 2.5 pts under" } else { Bad "guard defaults moved" }
if ((@($d.guard_levels) -join ",") -eq "15,17.5,20,25" -and (@($d.guard_fractions_tried) -join ",") -eq "25,50" -and (@($d.guard_rearm_gaps_tried) -join ",") -eq "2.5,5") {
    Ok "retune grid defaults: levels 15/17.5/20/25, sizes 25/50, gaps 2.5/5"
} else { Bad ("grid defaults: levels {0}; sizes {1}; gaps {2}" -f (@($d.guard_levels) -join ","), (@($d.guard_fractions_tried) -join ","), (@($d.guard_rearm_gaps_tried) -join ",")) }

Sec "2  the search"
$f = Api GET "/api/v1/plan/frontier"
$tu = $f.rules.tuning
if (-not $f -or -not $f.rules) { Skip "no goal verdict with a rules replay" }
elseif (-not $tu -or $null -eq $tu.tried) { Skip ("verdict solved {0} predates this build; re-run after the nightly solve" -f $f.computed_at) }
else {
    $n = @($tu.candidates).Count
    $cur = @($tu.candidates | Where-Object { $_.current }).Count
    if ($n -eq [int]$tu.tried -and $n -ge 17 -and $cur -eq 1) { Ok ("{0} setups measured, one of them today's (material: {1})" -f $n, $tu.material) }
    else { Bad ("tried {0}, candidates {1}, current {2}" -f $tu.tried, $n, $cur) }
    Note ("take-profits measured: {0}; screen paths {1}" -f $tu.take_profits_measured, $tu.screen_paths)
}

Sec "3  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
