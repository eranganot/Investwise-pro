# SMOKE - PR 116: say the true thing (L3, L4, L5, L6, L9, L14; D80, D81).
#
#   .\scripts\smoke\smoke-pr116-say-the-true-thing.ps1 -Sha <short sha of the merge>
#
#   1  28 settings; commodity_buys_core_only exists and defaults to 1 (D80)
#   2  the plan-health cash line calls the target a target, not a floor (L4)
#   3  no Today card names a tab that does not exist or calls the target a floor
#      (L6, L4)
#   4  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 116"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the setting"
$p = Api GET "/api/v1/plan"
$names = @($p.thresholds_spec.PSObject.Properties.Name)
if ($names.Count -eq 28 -and $names -contains "commodity_buys_core_only" -and [int]$p.thresholds_defaults.commodity_buys_core_only -eq 1) {
    Ok "28 settings; commodity buys from the core by default"
} else { Bad ("{0} settings; commodity_buys_core_only default {1}" -f $names.Count, $p.thresholds_defaults.commodity_buys_core_only) }

Sec "2  plan health"
$h = Api GET "/api/v1/health-check"
$d = [string]$h.plan_parts.cash.detail
if ($d -match "cash target" -and $d -notmatch "floor") { Ok ("cash line: {0}" -f ($d -replace '[^\x20-\x7E]', '')) }
else { Bad ("cash line still says floor: {0}" -f ($d -replace '[^\x20-\x7E]', '')) }

Sec "3  Today's copy"
$r = Api GET "/api/v1/recommendations"
$cards = @($r.recommendations) + @($r.more_recommendations)
$text = ($cards | ForEach-Object { @($_.title, $_.why, $_.action, $_.impact) + @($_.how) } | Where-Object { $_ }) -join " | "
$hits = @("Cash tab", "Goal settings", "Rules tab", "floor intact", "liquidity floor", "rebuild your cash floor") | Where-Object { $text -like "*$_*" }
if ($hits.Count -eq 0) { Ok ("{0} cards, no stale place names or 'floor' for the target" -f $cards.Count) } else { Bad ("found: {0}" -f ($hits -join ", ")) }

Sec "4  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
