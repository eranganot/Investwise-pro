# SMOKE - PR 124: a rule card says what it would do to your goal (D86).
#
#   .\scripts\smoke\smoke-pr124-rule-card-goal-impact.ps1 -Sha <short sha of the merge>
#
#   1  POST /plan/frontier/rule-impact (read-only: nothing is saved) answers for
#      a proposed sale rule with a with/without comparison, and names a proposed
#      entry rule as not modelled
#   2  Today: every rule-proposing card is served (the line is filled client-side)
#   3  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 124"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the comparison"
$body = @{ rules = @(
    @{ ticker = "UGA"; rule_type = "trailing_stop"; mode = "pct"; level = 17 },
    @{ ticker = "SOXL"; rule_type = "strategy_signal"; mode = "entry"; level = 0 }) } | ConvertTo-Json -Depth 4
try {
    $j = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/frontier/rule-impact" -Method Post -Body $body `
        -ContentType "application/json" -Headers $script:ApiHeaders -TimeoutSec 300
} catch { $j = $null; Bad ("rule-impact failed: {0}" -f $_.Exception.Message) }
if ($j -and $j.ok -and $j.compare) {
    Ok ("UGA 17% trailing: goal {0}% -> {1}% ({2} pts), halving {3}% -> {4}%" -f $j.compare.without.p_goal_pct, $j.compare.with.p_goal_pct, $j.compare.goal_pts, $j.compare.without.p_halve_pct, $j.compare.with.p_halve_pct)
    $sig = @($j.proposed | Where-Object { $_.rule_type -eq "strategy_signal" })
    if ($sig.Count -eq 1 -and -not $sig[0].modelled) { Ok ("entry signal named: {0}" -f $sig[0].reason) } else { Bad "the entry signal is not named as unmodelled" }
} elseif ($j) { Bad ("no comparison: ok={0} reason={1}" -f $j.ok, $j.reason) }

Sec "2  Today"
$r = Api GET "/api/v1/recommendations"
$cards = @($r.recommendations) + @($r.more_recommendations)
$rc = @($cards | Where-Object { $_.apply.kind -in @("create_rules", "create_rule") })
Note ("{0} rule-proposing card(s): {1}" -f $rc.Count, (($rc | ForEach-Object { $_.title }) -join " | "))
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }

Sec "3  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
Stop-Smoke
