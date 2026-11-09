# SMOKE - PR 159 (C30/C33 re-measured): no card buys what the core realign sells;
# leftover cash never buys a class that is over target.
#
#   .\scripts\smoke\smoke-pr159-no-card-buys-what-the-realign-sells.ps1 -Sha <short sha>
#
#   1  Today: no buy idea names a ticker outside the core basket and the sleeves
#   2  a cash preview (?simulate_cash=, writes nothing): no new position outside the
#      basket and the sleeves, and no leg buys a class that is over target
#   3  NOT CHANGED: /mix answers with the same target source; Today still builds
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [double]$SimCash = 2000)
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 159"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this change)"

$r = Api GET "/api/v1/recommendations"
$bt = $r.book_target
$keep = @{}
foreach ($p in @($bt.managed_sleeve_targets.PSObject.Properties)) { $keep[$p.Name.ToUpper()] = 'sleeve' }
$core = "$($bt.core_strategy_id)"
$basket = @()
if ($core) {
    $s = Api GET "/api/v1/strategies"
    foreach ($g in @($s.by_goal.PSObject.Properties)) {
        foreach ($e in @($g.Value)) { if ($e.id -eq $core) { $basket = @($e.basket | ForEach-Object { "$($_[0])".ToUpper() }) } }
    }
}
foreach ($t in $basket) { $keep[$t] = 'basket' }
Note ("core {0}: basket {1}; sleeves {2}" -f $core, ($basket -join ','), ((@($bt.managed_sleeve_targets.PSObject.Properties) | ForEach-Object { $_.Name }) -join ','))

Sec "1  Today's buy ideas"
if (-not $core -or $basket.Count -eq 0) { Skip "no basketed core on this book: nothing for the realign to sell" }
else {
    $ideas = @(@($r.recommendations) + @($r.more_recommendations) | Where-Object { $_.dimension -eq 'opportunity' -and $_.meta.ticker })
    $bad = @($ideas | Where-Object { -not $keep.ContainsKey("$($_.meta.ticker)".ToUpper()) })
    Note ("{0} buy idea card(s): {1}" -f $ideas.Count, (($ideas | ForEach-Object { $_.meta.ticker }) -join ','))
    if ($bad.Count -eq 0) { Ok "no buy idea outside the basket and the sleeves" } else { Bad ("off-basket buy idea(s): {0}" -f (($bad | ForEach-Object { $_.meta.ticker }) -join ',')) }
}

Sec "2  a cash preview of $SimCash ILS (writes nothing)"
$m = Api GET "/api/v1/mix"
$sim = Api GET ("/api/v1/recommendations?simulate_cash={0}" -f $SimCash)
$legs = @(@($sim.recommendations) | ForEach-Object { @($_.apply.legs) } | Where-Object { $_ -and $_.ticker })
Note ("legs: {0}" -f (($legs | ForEach-Object { "$($_.ticker) $([math]::Round([double]$_.amount_ils))" }) -join ', '))
if ($legs.Count -eq 0) { Skip "the preview produced no legs" }
else {
    $newOff = @($legs | Where-Object { $_.new_position -and $basket.Count -gt 0 -and -not $keep.ContainsKey("$($_.ticker)".ToUpper()) })
    if ($newOff.Count -eq 0) { Ok "no new position outside the basket and the sleeves" } else { Bad ("new off-basket position(s): {0}" -f (($newOff | ForEach-Object { $_.ticker }) -join ',')) }
    $over = @()
    foreach ($p in @($m.target_allocation.PSObject.Properties)) {
        if ($p.Name -ne 'Cash' -and [double]$m.current_allocation.($p.Name) -gt [double]$p.Value + 0.0005) { $over += $p.Name }
    }
    Note ("classes over target now: {0}" -f $(if ($over.Count) { $over -join ',' } else { 'none' }))
    $intoOver = @($legs | Where-Object { $over -contains "$($_.asset_class)" })
    if ($intoOver.Count -eq 0) { Ok "no leg buys a class that is over target" } else { Bad ("leg(s) into an over-target class: {0}" -f (($intoOver | ForEach-Object { "$($_.ticker) ($($_.asset_class))" }) -join ', ')) }
}

Sec "3  NOT CHANGED"
if ($m -and $m.target_source) { Ok ("/mix answers (target source {0})" -f $m.target_source) } else { Bad "/mix did not answer" }
if ($r -and $null -ne $r.recommendations) { Ok ("Today builds: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "Today did not build" }
Stop-Smoke
