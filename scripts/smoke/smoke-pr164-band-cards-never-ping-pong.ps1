# SMOKE - PR 164 (D147): accepting one band card may never fire another.
#
#   .\scripts\smoke\smoke-pr164-band-cards-never-ping-pong.ps1 -Sha <short sha>
#
#   1  every band buy on Today, simulated on /portfolio: after its accept no other
#      banded name is below the bottom of its band
#   2  no band buy sells a banded name while a holding outside the plan sits unsold
#      and unclaimed by any card on the screen
#   3  no band buy sells a sleeve (PR 163, unchanged)
#   4  NOT CHANGED: Today builds; /mix answers; the band rules are still evaluated
#
# Live book on 2026-11-11: VXUS 4.1% against 10.8 +-3 is the one band below. Before
# this PR its card sold 1.1272 VTI (21.8% -> ~15%, under VTI's 18.7% floor). After,
# it should sell XLE and XLF, which no part of the plan holds.
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 164"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(no migration in this change)"

$r = Api GET "/api/v1/recommendations"
$pf = Api GET "/api/v1/portfolio"
$rules = Api GET "/api/v1/rules"
$nav = [double]$pf.nav_ils

$val = @{}
foreach ($p in @($pf.positions)) { $val["$($p.ticker)".ToUpper()] = [double]$p.value_ils }
$band = @{}
foreach ($x in @(@($rules.rules) | Where-Object { $_.rule_type -eq 'rebalance_band' -and $_.active -and $null -ne $_.target })) {
    $band["$($x.ticker)".ToUpper()] = @{ target = [double]$x.target; level = [double]$x.level }
}
$sleeves = @{}
foreach ($p in @($r.book_target.planned_sleeve_targets.PSObject.Properties) + @($r.book_target.managed_sleeve_targets.PSObject.Properties)) { if ($p) { $sleeves[$p.Name.ToUpper()] = 1 } }
Note ("NAV {0:N0}; bands: {1}; sleeves: {2}" -f $nav, (($band.Keys | Sort-Object) -join ','), (($sleeves.Keys | Sort-Object) -join ','))

$cards = @(@($r.recommendations) + @($r.more_recommendations))
$bands = @($cards | Where-Object { "$($_.id)" -like 'rule_*' -and $_.apply.kind -eq 'buy_funded' })

function Below($v) {
    $out = @()
    foreach ($t in $band.Keys) {
        $w = 100.0 * [double]($v[$t]) / $nav
        if (($band[$t].target - $w) -gt $band[$t].level) { $out += $t }
    }
    return $out
}

Sec "1  an accepted band card leaves every other band inside"
if ($bands.Count -eq 0) { Skip "no band is breached below today, so no band buy to simulate" }
else {
    $before = @(Below $val)
    foreach ($c in $bands) {
        $v = @{}; foreach ($k in $val.Keys) { $v[$k] = $val[$k] }
        $bt = "$($c.apply.ticker)".ToUpper()
        $v[$bt] = [double]($v[$bt]) + [double]$c.apply.amount_ils
        foreach ($s in @($c.apply.sells)) { $st = "$($s.ticker)".ToUpper(); $v[$st] = [double]($v[$st]) - [double]$s.value_ils }
        $newly = @(Below $v | Where-Object { $before -notcontains $_ })
        Note ("{0}: buy {1} {2:N0}; sells {3}" -f $c.id, $bt, [double]$c.apply.amount_ils, ((@($c.apply.sells) | ForEach-Object { "$($_.ticker) $([math]::Round([double]$_.value_ils))" }) -join ', '))
        if ($newly.Count -eq 0) { Ok ("{0}: no other band breached after accept" -f $c.id) }
        else { Bad ("{0}: accepting it puts {1} below its band -- the ping-pong" -f $c.id, ($newly -join ',')) }
    }
}

Sec "2  outside-the-plan holdings sell before a banded name"
if ($bands.Count -eq 0) { Skip "no band buy today" }
else {
    $claimed = @{}
    foreach ($c in $cards) { foreach ($s in @($c.apply.sells)) { if ($s) { $claimed["$($s.ticker)".ToUpper()] = 1 } } }
    $outside = @($val.Keys | Where-Object { $_ -ne 'CASH' -and -not $band.ContainsKey($_) -and -not $sleeves.ContainsKey($_) -and $val[$_] -ge 100 })
    Note ("held outside the plan: {0}" -f (($outside | ForEach-Object { "$_ $([math]::Round($val[$_]))" }) -join ', '))
    foreach ($c in $bands) {
        $sold = @(@($c.apply.sells) | ForEach-Object { "$($_.ticker)".ToUpper() })
        $soldBanded = @($sold | Where-Object { $band.ContainsKey($_) })
        $idle = @($outside | Where-Object { $sold -notcontains $_ -and -not $claimed.ContainsKey($_) })
        if ($soldBanded.Count -gt 0 -and $idle.Count -gt 0) { Bad ("{0} sells {1} while {2} sits unsold" -f $c.id, ($soldBanded -join ','), ($idle -join ',')) }
        else { Ok ("{0} sells {1}" -f $c.id, ($sold -join ',')) }
    }
}

Sec "3  band buys sell no sleeve (PR 163)"
if ($bands.Count -eq 0) { Skip "no band buy today" }
else {
    foreach ($c in $bands) {
        $bad = @(@($c.apply.sells) | ForEach-Object { "$($_.ticker)".ToUpper() } | Where-Object { $sleeves.ContainsKey($_) })
        if ($bad.Count -eq 0) { Ok ("{0} sells no sleeve" -f $c.id) } else { Bad ("{0} sells sleeve(s): {1}" -f $c.id, ($bad -join ',')) }
    }
}

Sec "4  NOT CHANGED"
if ($r -and $null -ne $r.recommendations) { Ok ("Today builds: {0} card(s); degraded: {1}" -f @($r.recommendations).Count, (@($r.degraded) -join ',')) } else { Bad "Today did not build" }
if (@($r.degraded).Count -eq 0) { Ok "no degraded agent" } else { Bad ("degraded: {0}" -f (@($r.degraded) -join ',')) }
$m = Api GET "/api/v1/mix"
if ($m -and $m.target_source) { Ok "/mix answers" } else { Bad "/mix did not answer" }
if ($band.Count -gt 0) { Ok ("{0} band rule(s) evaluated: {1}" -f $band.Count, ((@($rules.rules) | Where-Object { $_.rule_type -eq 'rebalance_band' } | ForEach-Object { "$($_.ticker) $($_.status)" }) -join '; ')) } else { Skip "no active band rule" }
Stop-Smoke
