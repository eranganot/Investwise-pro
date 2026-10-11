# SMOKE - PR 115: every decision is a setting; one minimum trade (D-c, D-d).
#
#   .\scripts\smoke\smoke-pr115-settings.ps1 -Sha <short sha of the merge>
#
#   1  /plan ships 23 thresholds, each with a label (13 new)
#   2  the new defaults are today's numbers (unless you set one)
#   3  the ONE card floor is reported and is never below the funded minimum
#   4  the page: the Card-thresholds sheet states the floor; sw.js iw-v54
#   5  non-change: Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 115"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(unchanged: no migration)"

$p = Api GET "/api/v1/plan"
$new = @('concentration_cap_pct', 'sleeve_drift_band_pts', 'min_resize_pts', 'material_gap_close_pct', 'material_vol_drop_pts',
         'ignore_mute_days', 'done_mute_days', 'refire_change_pct', 'near_rule_pct', 'near_trail_giveback_pct',
         'near_cap_pts', 'ideas_shown', 'max_idea_cards')

Sec "1  23 settings"
$keys = @($p.thresholds_spec.PSObject.Properties | ForEach-Object { $_.Name })
$missing = @($new | Where-Object { $keys -notcontains $_ })
if ($keys.Count -eq 23 -and $missing.Count -eq 0) { Ok "23 thresholds, all 13 new present" } else { Bad ("{0} keys; missing {1}" -f $keys.Count, ($missing -join ', ')) }

Sec "2  defaults are today's numbers"
$d = $p.thresholds_defaults
$want = @{ sleeve_drift_band_pts = 5; min_resize_pts = 2; material_gap_close_pct = 33.33; material_vol_drop_pts = 3;
           ignore_mute_days = 7; done_mute_days = 14; refire_change_pct = 25; near_rule_pct = 10;
           near_trail_giveback_pct = 60; near_cap_pts = 2; ideas_shown = 5; max_idea_cards = 2 }
$wrong = @($want.Keys | Where-Object { [double]$d.$_ -ne [double]$want[$_] })
$capDef = @{ Low = 15; Medium = 25; High = 40 }[$p.risk_tolerance]
if ($capDef -and [double]$d.concentration_cap_pct -ne [double]$capDef) { $wrong += 'concentration_cap_pct' }
if ($wrong.Count -eq 0) { Ok ("defaults unchanged (cap default {0}% for {1})" -f $d.concentration_cap_pct, $p.risk_tolerance) } else { Bad ("changed: {0}" -f ($wrong -join ', ')) }
foreach ($k in $new) { Note ("{0,-24} {1,-8} default {2}{3}" -f $k, $p.thresholds.$k, $d.$k, $(if ($null -ne $p.thresholds_overrides.$k) { '  (set by you)' } else { '' })) }

Sec "3  one card floor"
$floor = $p.thresholds_effective_card_floor_ils
if ($null -ne $floor -and [double]$floor -ge [double]$p.thresholds.min_funded_trade_ils) { Ok ("smallest trade card today: ILS {0}" -f $floor) }
else { Bad ("floor {0} vs funded minimum {1}" -f $floor, $p.thresholds.min_funded_trade_ils) }

Sec "4  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($html -and $html -match "Smallest trade card today") { Ok "sheet states the floor" } else { Bad "sheet line missing" }
$sw = $null; try { $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($sw -and $sw -match "iw-v54") { Ok "sw.js iw-v54" } else { Bad "sw.js not bumped" }

Sec "5  non-change"
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
