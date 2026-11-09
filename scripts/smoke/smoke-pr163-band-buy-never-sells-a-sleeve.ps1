# SMOKE - PR 163 (C36): a band top-up never funds itself by selling a sleeve.
#
#   .\scripts\smoke\smoke-pr163-band-buy-never-sells-a-sleeve.ps1 -Sha <short sha>
#
#   1  every band card on Today (id rule_*, apply buy_funded) sells no sleeve ticker
#      (SKIP when no band is breached below today -- the usual state)
#   2  the band rules are still there and still evaluated (/rules answers with them)
#   3  NOT CHANGED: Today builds; /mix answers
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 163"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(no migration in this change)"

$r = Api GET "/api/v1/recommendations"
$sleeves = @{}
foreach ($p in @($r.book_target.planned_sleeve_targets.PSObject.Properties) + @($r.book_target.managed_sleeve_targets.PSObject.Properties)) { if ($p) { $sleeves[$p.Name.ToUpper()] = 1 } }
Note ("sleeves: {0}" -f (($sleeves.Keys | Sort-Object) -join ','))

Sec "1  band buys sell no sleeve"
$bands = @(@($r.recommendations) + @($r.more_recommendations) | Where-Object { "$($_.id)" -like 'rule_*' -and $_.apply.kind -eq 'buy_funded' })
if ($bands.Count -eq 0) { Skip "no band is breached below today, so no band buy to check" }
else {
    foreach ($c in $bands) {
        $sold = @(@($c.apply.sells) | ForEach-Object { "$($_.ticker)".ToUpper() })
        $bad = @($sold | Where-Object { $sleeves.ContainsKey($_) })
        Note ("{0}: buy {1} {2}; sells {3}" -f $c.id, $c.apply.ticker, $c.apply.amount_ils, ($sold -join ','))
        if ($bad.Count -eq 0) { Ok ("{0} sells no sleeve" -f $c.id) } else { Bad ("{0} sells sleeve(s): {1}" -f $c.id, ($bad -join ',')) }
    }
}

Sec "2  the band rules"
$rules = Api GET "/api/v1/rules"
$b = @(@($rules.rules) | Where-Object { $_.rule_type -eq 'rebalance_band' -and $_.active })
if ($b.Count -gt 0) { Ok ("{0} active band rule(s): {1}" -f $b.Count, (($b | ForEach-Object { "$($_.ticker) $($_.status)" }) -join '; ')) } else { Skip "no active band rule" }

Sec "3  NOT CHANGED"
if ($r -and $null -ne $r.recommendations) { Ok ("Today builds: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "Today did not build" }
$m = Api GET "/api/v1/mix"
if ($m -and $m.target_source) { Ok "/mix answers" } else { Bad "/mix did not answer" }
Stop-Smoke
