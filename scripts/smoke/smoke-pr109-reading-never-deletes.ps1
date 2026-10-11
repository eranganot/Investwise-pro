# SMOKE - PR 109: reading Today never deletes or retires a rule.
#
#   .\scripts\smoke\smoke-pr109-reading-never-deletes.ps1 -Sha <short sha of the merge>
#
#   1  the rule set (id, active) is identical before and after two reads of
#      Today and one of the Rules tab
#   2  the banner: every carded rule has a visible card; `uncarded` is reported
#      (new in PR 109: such a rule used to be "healed", i.e. switched off)
#   3  watch alerts (buy_dip / price_below / price_above) on unheld tickers are
#      listed, not purged
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 109"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 109 has no migration)"

function RuleSet { $x = Api GET "/api/v1/rules"; return @($x.rules | ForEach-Object { "{0}|{1}" -f $_.id, $_.active } | Sort-Object) }

Sec "1  reads write nothing"
$before = RuleSet
$r1 = Api GET "/api/v1/recommendations"
$null = Api GET "/api/v1/rules"
$r2 = Api GET "/api/v1/recommendations"
$after = RuleSet
$diff = @(Compare-Object $before $after)
if ($before.Count -gt 0 -and $diff.Count -eq 0) { Ok ("{0} rules, identical across two Today reads" -f $before.Count) }
elseif ($before.Count -eq 0) { Skip "no rules on this book" }
else { Bad ("changed by a read: {0}" -f (($diff | ForEach-Object { "$($_.SideIndicator)$($_.InputObject)" }) -join ', ')) }

Sec "2  the banner"
$b = $r2.rule_banner
if ($null -eq $b) { Bad "no rule_banner" }
else {
    if ($null -ne $b.PSObject.Properties['uncarded']) { Ok ("uncarded reported: [{0}]; healed: [{1}]" -f (@($b.uncarded) -join ','), (@($b.healed) -join ',')) }
    else { Bad "rule_banner has no 'uncarded' (old build?)" }
    $all = @($r2.recommendations) + @($r2.more_recommendations) + @(@($r2.headsups) | Where-Object { $_.card } | ForEach-Object { $_.card })
    $visible = @($all | Where-Object { "$($_.id)" -like 'rule_*' } | ForEach-Object { "$($_.id)".Substring(5) })
    $missing = @(@($b.carded) | Where-Object { $visible -notcontains $_ })
    if ($missing.Count -eq 0) { Ok ("{0} carded rule(s), each with a visible card" -f @($b.carded).Count) } else { Bad ("carded without a card: {0}" -f ($missing -join ', ')) }
    if (@($b.healed).Count -eq 0) { Ok "no rule healed by a read" } else { Bad ("a read healed: {0}" -f (@($b.healed) -join ', ')) }
}

Sec "3  watch alerts"
$x = Api GET "/api/v1/rules"
$pf = Api GET "/api/v1/portfolio"
$held = @($pf.positions | ForEach-Object { "$($_.ticker)".ToUpper() })
$watch = @($x.rules | Where-Object { @('buy_dip', 'price_below', 'price_above') -contains $_.rule_type -and $held -notcontains "$($_.ticker)".ToUpper() })
if ($watch.Count -gt 0) { Ok ("{0} watch alert(s) listed: {1}" -f $watch.Count, (($watch | ForEach-Object { "$($_.ticker) $($_.rule_type)" }) -join ', ')) }
else { Skip "no watch alerts on unheld tickers on this book" }
Stop-Smoke
