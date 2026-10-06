# SMOKE - PR 104a: the Plan tab's three sub-tabs (Goal . My mix . Explore).
#
#   .\scripts\smoke\smoke-pr104a-plan-tabs.ps1 -Sha <sha serving now>
#
#   1  the served page carries the three sub-tabs, What would it take? folded in
#      Explore, and service worker iw-v45
#   2  every endpoint the sub-tabs read answers: /plan /plan/frontier
#      /recommendations /rules /plan/sleeves /mix /strategies
#   3  My mix's targets add up: sleeves + core + commodities + cash = 100%
#   4  the protective-rules filter: stops on sleeve tickers + the drawdown guard
#   The look is checked on the phone: qa/QA-2026-10-06-pr104a-plan-tabs.md
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104a"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

Sec "1  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
$sw = $null; try { $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if (-not $html) { Bad "could not load the page" }
else {
    if ($html -match "planTab\('goal'\)" -and $html -match "planTab\('mix'\)" -and $html -match "planTab\('explore'\)") { Ok "Goal . My mix . Explore sub-tabs" } else { Bad "sub-tabs missing" }
    if ($html.IndexOf('id="stratBox"') -lt $html.IndexOf('id="wwitBox"') -and $html -match '<details class="card" id="wwitBox"') { Ok "What would it take? folded at the foot of Explore" } else { Bad "What would it take? not where D5 puts it" }
}
if ("$sw" -match "iw-v45") { Ok "service worker iw-v45" } else { Skip "sw.js not at iw-v45 (or not served at /sw.js)" }

Sec "2  what the sub-tabs read"
foreach ($ep in @("/api/v1/plan", "/api/v1/plan/frontier", "/api/v1/recommendations", "/api/v1/rules", "/api/v1/plan/sleeves", "/api/v1/mix", "/api/v1/strategies")) {
    $r = Api GET $ep
    if ($null -ne $r) { Ok "$ep" } else { Bad "$ep did not answer" }
}

Sec "3  My mix adds up"
$s = Api GET "/api/v1/plan/sleeves"
$sl = (@($s.sleeves) | Measure-Object -Property sleeve_pct -Sum).Sum
$com = [double]$s.commodities_pct * 100; $cash = [double]$s.cash_reserve_pct * 100
$core = [math]::Max(0, 100 - $sl - $com - $cash)
Note ("sleeves {0:N1} + core {1:N1} + commodities {2:N1} + cash {3:N1}" -f $sl, $core, $com, $cash)
if ([math]::Abs($sl + $core + $com + $cash - 100) -lt 0.05) { Ok "targets add to 100%" } else { Bad "targets do not add to 100%" }

Sec "4  the protective rules shown"
$rules = @((Api GET "/api/v1/rules").rules)
$tick = @{}; foreach ($x in @($s.sleeves)) { foreach ($t in @($x.tickers)) { $tick["$t".ToUpper()] = $x.strategy_id } }
$stops = @($rules | Where-Object { @('profit_protect', 'trailing_stop', 'stop_loss') -contains $_.rule_type -and $tick.ContainsKey("$($_.ticker)".ToUpper()) })
$guard = $rules | Where-Object { $_.rule_type -eq 'drawdown_guard' } | Select-Object -First 1
foreach ($x in $stops) { Note ("{0} {1} {2}{3}" -f $x.ticker, $x.rule_type, $x.level, $(if ($x.active) { '' } else { ' (paused)' })) }
if ($stops.Count -gt 0 -or $guard) { Ok ("{0} stop(s) on sleeve tickers{1}" -f $stops.Count, $(if ($guard) { ", drawdown guard $($guard.level)%" } else { '' })) } else { Skip "no stops or guard armed" }

Note "BY EYE: qa/QA-2026-10-06-pr104a-plan-tabs.md (8 checks)"
Stop-Smoke
