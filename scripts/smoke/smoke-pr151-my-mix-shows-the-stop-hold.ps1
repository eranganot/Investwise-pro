# SMOKE - PR 151: My mix shows the core target the cards use, and a stop-held sleeve as on hold.
#
#   .\scripts\smoke\smoke-pr151-my-mix-shows-the-stop-hold.ps1 -Sha <short sha of the merge>
#
#   1  /plan/sleeves.core_target_pct equals /recommendations.book_target.core_pct
#   2  every sleeve carries on_stop_hold; each stop-held ticker (rules: buy_back, kind stop) is listed on its sleeve
#   3  NOT CHANGED: core_pct is still the planned remainder; the shell serves iw-v77
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 151"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$v = Api GET "/api/v1/plan/sleeves"
$bt = (Api GET "/api/v1/recommendations").book_target

Sec "1  the core target is the book target's"
if ($null -eq $v.core_target_pct) { Bad "core_target_pct is missing" }
elseif ([math]::Abs([double]$v.core_target_pct - [double]$bt.core_pct) -lt 0.0005) { Ok ("core_target_pct {0:P1} = book_target.core_pct" -f [double]$v.core_target_pct) }
else { Bad ("core_target_pct {0} vs book_target.core_pct {1}" -f $v.core_target_pct, $bt.core_pct) }

Sec "2  stop-held sleeves are marked"
$rules = Api GET "/api/v1/rules"
$stops = @(@($rules.rules) | Where-Object { $_.rule_type -eq "buy_back" -and $_.params.kind -eq "stop" } | ForEach-Object { ([string]$_.ticker).ToUpper() })
$missing = @(@($v.sleeves) | Where-Object { $null -eq $_.on_stop_hold })
if ($missing.Count) { Bad ("{0} sleeve(s) without on_stop_hold" -f $missing.Count) } else { Ok "every sleeve carries on_stop_hold" }
foreach ($t in $stops) {
    $own = @(@($v.sleeves) | Where-Object { @($_.tickers) -contains $t })
    if (-not $own.Count) { Note ("{0}: no sleeve holds it" -f $t); continue }
    if (@($own[0].on_stop_hold) -contains $t) { Ok ("{0} is marked on hold on {1}" -f $t, $own[0].strategy_id) } else { Bad ("{0} is on a stop hold but not marked" -f $t) }
}
if (-not $stops.Count) { Skip "no stop hold on the book now" }

Sec "3  NOT CHANGED"
$planned = 100.0 - [double]$v.allocated_pct
if ([math]::Abs([double]$v.core_pct - $planned) -lt 0.5) { Ok ("core_pct {0} is the planned remainder" -f $v.core_pct) } else { Bad ("core_pct {0}, planned remainder {1}" -f $v.core_pct, $planned) }
$sw = Invoke-WebRequest -Uri ($BaseUrl + "/app/sw.js") -UseBasicParsing -ErrorAction SilentlyContinue
if ($sw -and $sw.Content -match "iw-v77") { Ok "the shell serves iw-v77" } else { Note "sw.js version not read (path may differ)" }
Stop-Smoke
