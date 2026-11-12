# SMOKE - PR 166 (V4): each plan row on Plan > My mix lists the holdings that make it up.
#
#   .\scripts\smoke\smoke-pr166-plan-rows-list-their-holdings.ps1 -Sha <short sha>
#
#   1  every sleeve carries holdings, and its lines add up to the sleeve row
#   2  core_holdings lists the core basket and any held name in no plan, and its
#      lines add up to the core row (cash aside)
#   3  sleeve lines + core lines account for every holding exactly once (/portfolio)
#   4  a sleeve line on its off-signal leg names what it holds instead
#   5  the page ships the expand-on-tap panel
#   6  NOT CHANGED: PR 165's split (sleeve held_pct, shared_with core); Today builds
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 166"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(no migration in this change)"

$sl = Api GET "/api/v1/plan/sleeves"
$pf = Api GET "/api/v1/portfolio"
$r = Api GET "/api/v1/recommendations"
$nav = [double]$sl.nav_ils

Sec "1  each sleeve lists its holdings"
foreach ($s in @($sl.sleeves)) {
    $hs = @($s.holdings)
    if ($hs.Count -eq 0 -or $null -eq $s.holdings) { Bad ("{0}: no holdings list" -f $s.strategy_id); continue }
    $sum = 0.0; foreach ($h in $hs) { $sum += [double]$h.held_ils }
    Note ("{0}: {1}" -f $s.strategy_id, (($hs | ForEach-Object { "$($_.ticker) $([math]::Round([double]$_.held_ils)) ($([math]::Round(100*[double]$_.held_pct,1))% / $([math]::Round(100*[double]$_.target_pct,1))%)" }) -join ', '))
    if ([math]::Abs($sum - [double]$s.held_ils) -lt 1.0) { Ok ("{0}: lines add up to the row ({1:N0})" -f $s.strategy_id, $sum) }
    else { Bad ("{0}: lines {1:N0} != row {2:N0}" -f $s.strategy_id, $sum, [double]$s.held_ils) }
}

Sec "2  the core lists its basket and what no plan holds"
$core = @($sl.core_holdings | Where-Object { $null -ne $_ })
if ($null -eq $sl.core_holdings -or $core.Count -eq 0) { Bad "no core_holdings" }
else {
    Note (($core | ForEach-Object { "$($_.ticker) $([math]::Round([double]$_.held_ils))" + $(if (-not $_.in_plan) { ' [not in plan]' } elseif ($_.shared) { ' [shared]' } else { '' }) }) -join ', ')
    $csum = 0.0; foreach ($h in $core) { $csum += [double]$h.held_pct }
    $cash = 0.0; foreach ($p in @($pf.positions)) { if ("$($p.ticker)" -eq 'CASH') { $cash += [double]$p.value_ils } }
    $coreRow = [double]$sl.core_held_pct - ($cash / $nav)
    if ([math]::Abs($csum - $coreRow) -lt 0.003) { Ok ("core lines {0:P1} = core row less cash {1:P1}" -f $csum, $coreRow) }
    else { Bad ("core lines {0:P1} != core row less cash {1:P1}" -f $csum, $coreRow) }
    $off = @($core | Where-Object { -not $_.in_plan })
    if ($off.Count -gt 0) { Ok ("held outside any plan, marked: {0}" -f (($off | ForEach-Object { $_.ticker }) -join ', ')) } else { Ok "nothing held outside the plan" }
}

Sec "3  every holding accounted for exactly once"
$lines = @{}
foreach ($s in @($sl.sleeves)) { foreach ($h in @($s.holdings)) { $t = "$($h.ticker)".ToUpper(); $lines[$t] = [double]($lines[$t]) + [double]$h.held_ils } }
foreach ($h in $core) { $t = "$($h.ticker)".ToUpper(); $lines[$t] = [double]($lines[$t]) + [double]$h.held_ils }
$miss = @()
foreach ($p in @($pf.positions)) {
    $t = "$($p.ticker)".ToUpper(); if ($t -eq 'CASH') { continue }
    if ([math]::Abs([double]($lines[$t]) - [double]$p.value_ils) -gt 2.0) { $miss += ("{0} held {1:N0} listed {2:N0}" -f $t, [double]$p.value_ils, [double]($lines[$t])) }
}
if ($miss.Count -eq 0) { Ok "each position's value = its sleeve lines + its core line" } else { Bad ($miss -join '; ') }

Sec "4  an off-signal leg says what it stands in for"
$parked = @(@($sl.sleeves) | ForEach-Object { @($_.holdings) } | Where-Object { $_.parked -and [double]$_.held_ils -gt 0 })
if ($parked.Count -eq 0) { Skip "no sleeve on its off-signal leg today" }
foreach ($h in $parked) { if (@($h.instead_of).Count -gt 0) { Ok ("{0}: instead of {1}" -f $h.ticker, (@($h.instead_of) -join ',')) } else { Bad ("{0} is parked but names nothing it stands in for" -f $h.ticker) } }

Sec "5  the page ships the panel"
try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app" -UseBasicParsing -TimeoutSec 60).Content } catch { $html = "" }
if ($html -match 'function mixToggle\(' -and $html -match 'not in your plan' -and $html -match 'instead of') { Ok "/app has mixToggle and the three tags" } else { Bad "/app does not carry the PR 166 panel" }

Sec "6  NOT CHANGED"
$semis = @($sl.sleeves | Where-Object { $_.strategy_id -eq 'btm_trend_soxl' })
if ($semis.Count -gt 0) {
    $sw = @(); if ($semis[0].shared_with.PSObject.Properties['SMH']) { $sw = @($semis[0].shared_with.SMH) }
    if ($sw -contains 'core' -and [double]$semis[0].held_pct -lt 0.13) { Ok ("PR 165 split holds: Semis {0:P1}, SMH shared with core" -f [double]$semis[0].held_pct) } else { Bad "PR 165 split regressed" }
} else { Skip "no Semis sleeve" }
if ($r -and $null -ne $r.recommendations -and @($r.degraded).Count -eq 0) { Ok "Today builds, no degraded agent" } else { Bad "Today did not build cleanly" }
Stop-Smoke
