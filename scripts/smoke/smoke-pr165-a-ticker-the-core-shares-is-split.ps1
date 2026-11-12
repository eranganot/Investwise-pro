# SMOKE - PR 165 (D148): a ticker a sleeve and the core basket both want is split
# between them by target -- on the Plan tab and in the sleeve-drift card.
#
#   .\scripts\smoke\smoke-pr165-a-ticker-the-core-shares-is-split.ps1 -Sha <short sha>
#
#   1  /plan/sleeves: a sleeve ticker the core basket also wants is marked
#      shared with "core", and the sleeve holds only its share of it
#   2  the sleeve rows add up to allocated_held_pct, and core + sleeves + cash = 100
#   3  no "X has drifted to ..." card measures a shared ticker against the sleeve alone
#   4  NOT CHANGED: Today builds with no degraded agent; the band rules still answer
#
# Live book on 2026-11-11: SMH 19.1% = Semiconductors sleeve 10% + core 8.14%. Before
# this PR the sleeve row read 19.0% / 10.0% and the core 46.2% / 54.2%.
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 165"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(no migration in this change)"

$sl = Api GET "/api/v1/plan/sleeves"
$r = Api GET "/api/v1/recommendations"
$rules = Api GET "/api/v1/rules"
$nav = [double]$sl.nav_ils
$coreTarget = [double]$sl.core_target_pct
Note ("NAV {0:N0}; core target {1:P1}; core held {2:P1}; sleeves held {3:P1}" -f $nav, $coreTarget, [double]$sl.core_held_pct, [double]$sl.allocated_held_pct)

# The core basket's tickers and their plan weight: band rule target minus the sleeves' share.
$band = @{}
foreach ($x in @(@($rules.rules) | Where-Object { $_.rule_type -eq 'rebalance_band' -and $null -ne $_.target })) { $band["$($x.ticker)".ToUpper()] = [double]$x.target }

Sec "1  a ticker the core also wants is split"
$sharedSeen = 0
$sharedTk = @{}
foreach ($s in @($sl.sleeves)) {
    $tks = @(@($s.tickers) | ForEach-Object { "$_".ToUpper() })
    $sleeveT = [double]$s.sleeve_pct
    foreach ($t in $tks) {
        $planT = $band[$t]
        if ($null -eq $planT) { continue }
        $coreShare = $planT - $sleeveT
        $sw = @()
        if ($s.shared_with -and $s.shared_with.PSObject.Properties[$t]) { $sw = @($s.shared_with.$t) }
        Note ("{0}: {1} plan {2:N2}% = sleeve {3:N2} + core {4:N2}; held {5:P1}; shared_with {6}" -f $s.strategy_id, $t, $planT, $sleeveT, $coreShare, [double]$s.held_pct, ($sw -join ','))
        if ($coreShare -lt 0.5) { continue }
        $sharedSeen++
        $sharedTk[$t] = 1
        if ($sw -contains 'core') { Ok ("{0} is marked shared with the core" -f $t) } else { Bad ("{0}: the core wants {1:N1}% of the book in it, but shared_with does not say core" -f $t, $coreShare) }
        $sleeveHeldPct = 100.0 * [double]$s.held_pct
        if ($sleeveHeldPct -le $sleeveT + 3.0) { Ok ("{0} holds {1:N1}% against its {2:N1}% (its share, not the whole {3})" -f $s.strategy_id, $sleeveHeldPct, $sleeveT, $t) }
        else { Bad ("{0} holds {1:N1}% against {2:N1}% -- still credited with the whole {3}" -f $s.strategy_id, $sleeveHeldPct, $sleeveT, $t) }
    }
}
if ($sharedSeen -eq 0) { Skip "no sleeve ticker is also in the core basket today" }

Sec "2  the rows add up"
$sum = 0.0; foreach ($s in @($sl.sleeves)) { $sum += [double]$s.held_pct }
if ([math]::Abs($sum - [double]$sl.allocated_held_pct) -lt 0.0005) { Ok ("sleeve rows {0:P2} = allocated_held_pct {1:P2}" -f $sum, [double]$sl.allocated_held_pct) }
else { Bad ("sleeve rows {0:P2} != allocated_held_pct {1:P2}" -f $sum, [double]$sl.allocated_held_pct) }
if ([math]::Abs($sum + [double]$sl.core_held_pct - 1.0) -lt 0.0005) { Ok "sleeves + core held = 100% of the book (cash inside the core figure, as before)" }
else { Bad ("sleeves {0:P2} + core {1:P2} != 100%" -f $sum, [double]$sl.core_held_pct) }

Sec "3  drift cards on a shared ticker quote the combined plan"
$cards = @(@($r.recommendations) + @($r.more_recommendations) + @($r.headsups) + @($r.hidden_cards))
$drift = @($cards | Where-Object { "$($_.title)" -like '* has drifted to *' })
if ($drift.Count -eq 0) { Ok "no sleeve-drift card today (SMH sits within its combined plan)" }
foreach ($c in $drift) {
    $t = "$($c.title)".Split(' ')[0].ToUpper()
    if ($sharedTk.ContainsKey($t) -and "$($c.title)" -notlike '*core)*') { Bad ("'{0}' measures a shared ticker against the sleeve alone" -f $c.title) }
    else { Ok ("'{0}'" -f $c.title) }
}

Sec "4  NOT CHANGED"
if ($r -and $null -ne $r.recommendations) { Ok ("Today builds: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "Today did not build" }
if (@($r.degraded).Count -eq 0) { Ok "no degraded agent" } else { Bad ("degraded: {0}" -f (@($r.degraded) -join ',')) }
if ($band.Count -gt 0) { Ok ("{0} band rule(s) answer" -f $band.Count) } else { Skip "no band rule" }
Stop-Smoke
