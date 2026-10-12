# SMOKE - PR 123: every rule shows its effect on the goal (D85).
#
#   .\scripts\smoke\smoke-pr123-rule-goal-impact.ps1 -Sha <short sha of the merge>
#
#   1  a goal verdict solved by this build models the held tickers that carry
#      sale rules (rules.holdings_modelled) and has a per-rule pass
#      (rules.per_rule, 500 paths). Older verdict: SKIP.
#   2  GET /rules: every ACTIVE rule carries goal_impact -- an effect, a
#      "covered", or "not in the goal model" with a reason
#   3  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 123"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the solve"
$f = Api GET "/api/v1/plan/frontier"
$rb = $f.rules
if (-not $rb -or $null -eq $rb.per_rule) { Skip ("verdict solved {0} predates this build; re-run after a re-solve" -f $f.computed_at) }
else {
    Ok ("held tickers modelled: {0}" -f (@($rb.holdings_modelled) -join ", "))
    $n = @($rb.per_rule.rules.PSObject.Properties).Count
    if ($n -gt 0 -and [int]$rb.per_rule.paths -eq 500) { Ok ("{0} rules measured one at a time on 500 paths" -f $n) } else { Bad "per-rule pass missing" }
}

Sec "2  the Rules tab"
$ru = Api GET "/api/v1/rules"
$act = @($ru.rules | Where-Object { $_.active })
$miss = @($act | Where-Object { $null -eq $_.goal_impact })
if (-not $rb -or $null -eq $rb.per_rule) { Skip "no per-rule verdict yet" }
elseif ($miss.Count -eq 0) {
    $m = @($act | Where-Object { $_.goal_impact.modelled -ne $false }).Count
    Ok ("{0} active rules all carry goal_impact ({1} modelled, {2} with a reason)" -f $act.Count, $m, ($act.Count - $m))
} else { Bad ("{0} active rules without goal_impact: {1}" -f $miss.Count, (($miss | ForEach-Object { "$($_.rule_type) $($_.ticker)" }) -join ", ")) }

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
