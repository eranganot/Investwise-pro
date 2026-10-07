# SMOKE - PR 104d: the card thresholds are settings (Card-thresholds sheet).
#
#   .\scripts\smoke\smoke-pr104d-thresholds.ps1 -Sha <sha serving now>
#
#   1  /plan carries every threshold: the value that applies, which ones are the
#      user's, the defaults, and what each one decides
#   2  the defaults are today's numbers (50 / 100 / 0.5% / 250 / 150 / 3 / 25 /
#      25 / 5 / 15,20) unless the user has set one
#   3  the page has the Card-thresholds sheet
#
# READ-ONLY: changes no setting. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104d"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged since 104d.0)"

Sec "1  the thresholds are on /plan"
$p = Api GET "/api/v1/plan"
$keys = @('min_card_tax_ils', 'min_card_trade_ils', 'min_card_trade_pct', 'min_funded_trade_ils', 'cash_floor_ils',
          'rebalance_band_pts', 'take_profit_fraction_pct', 'gain_line_pct', 'material_odds_pts', 'guard_levels')
$missing = @($keys | Where-Object { $null -eq $p.thresholds.$_ -or $null -eq $p.thresholds_spec.$_ })
if ($missing.Count -eq 0) { Ok "all 10 thresholds, each with a label and what it decides" } else { Bad ("missing: {0}" -f ($missing -join ', ')) }
foreach ($k in $keys) { Note ("{0,-26} {1,-10} default {2}{3}" -f $k, (@($p.thresholds.$k) -join ','), (@($p.thresholds_defaults.$k) -join ','), $(if ($null -ne $p.thresholds_overrides.$k) { '  (set by you)' } else { '' })) }

Sec "2  the defaults"
$d = $p.thresholds_defaults
$want = @{ min_card_tax_ils = 50; min_card_trade_ils = 100; min_card_trade_pct = 0.5; min_funded_trade_ils = 250; cash_floor_ils = 150;
           rebalance_band_pts = 3; take_profit_fraction_pct = 25; gain_line_pct = 25; material_odds_pts = 5 }
$wrong = @($want.Keys | Where-Object { [double]$d.$_ -ne [double]$want[$_] })
if ($wrong.Count -eq 0 -and (@($d.guard_levels) -join ',') -eq '15,20') { Ok "defaults are today's numbers" } else { Bad ("defaults changed: {0}" -f ($wrong -join ', ')) }

Sec "3  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($html -and $html -match "function openThresholdsSheet\(\)") { Ok "Card-thresholds sheet present" } else { Bad "no Card-thresholds sheet" }
Stop-Smoke
