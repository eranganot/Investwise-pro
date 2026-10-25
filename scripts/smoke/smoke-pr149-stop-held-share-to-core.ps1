# SMOKE - PR 149: a stop-held sleeve's share goes to the core, not to cash (D127).
#
#   .\scripts\smoke\smoke-pr149-stop-held-share-to-core.ps1 -Sha <short sha of the merge>
#
#   1  every stop hold (rules: buy_back, params.kind = stop) is absent from book_target.held_back
#      (31a2d00: held_back = SOXL 0.23, target Cash 0.2376 -> FAIL there)
#   2  target Cash = the deliberate reserve + take-profit holds only
#   3  NOT CHANGED: the plan still shows the stop-held sleeve; no Today card buys a stop-held ticker
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 149"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$rules = Api GET "/api/v1/rules"
$stops = @(@($rules.rules) | Where-Object { $_.rule_type -eq "buy_back" -and $_.params.kind -eq "stop" } |
           ForEach-Object { ([string]$_.ticker).ToUpper() })
$p = Api GET "/api/v1/recommendations"
$bt = $p.book_target
$held = @{}
if ($bt.held_back) { foreach ($k in $bt.held_back.PSObject.Properties) { $held[$k.Name.ToUpper()] = [double]$k.Value } }

Sec "1  no stop hold parks cash"
if ($stops.Count -eq 0) { Skip "no stop hold on the book now" }
foreach ($t in $stops) {
    if ($held.ContainsKey($t)) { Bad ("{0} is on a stop hold and still parks {1:P1} in Cash" -f $t, $held[$t]) }
    else { Ok ("{0} is on a stop hold and parks nothing in Cash" -f $t) }
}

Sec "2  target Cash is the reserve plus take-profit holds"
$tp = 0.0; foreach ($k in $held.Keys) { if ($stops -notcontains $k) { $tp += $held[$k] } }
$cashT = [double]$bt.target.Cash; $want = [double]$bt.cash_pct + $tp
if ([math]::Abs($cashT - $want) -lt 0.002) { Ok ("target Cash {0:P2} = reserve {1:P2} + take-profit holds {2:P2}" -f $cashT, [double]$bt.cash_pct, $tp) }
else { Bad ("target Cash {0:P2}, expected {1:P2}" -f $cashT, $want) }
Note ("core_pct {0:P1}" -f [double]$bt.core_pct)

Sec "3  NOT CHANGED"
$planned = @{}
if ($bt.planned_sleeve_targets) { foreach ($k in $bt.planned_sleeve_targets.PSObject.Properties) { $planned[$k.Name.ToUpper()] = [double]$k.Value } }
foreach ($t in $stops) {
    if ($planned.ContainsKey($t) -and $planned[$t] -gt 0) { Ok ("the plan still shows the {0} sleeve at {1:P0}" -f $t, $planned[$t]) }
    else { Note ("{0} is not a planned sleeve ticker" -f $t) }
}
$cards = @(@($p.recommendations) + @($p.more_recommendations))
$buyers = @($cards | Where-Object {
    $a = $_.apply
    $a -and (($stops -contains ([string]$a.ticker).ToUpper() -and $a.kind -like "buy*") -or
             (@($a.legs) | Where-Object { $_ -and $stops -contains ([string]$_.ticker).ToUpper() -and $_.action -eq "buy" }))
})
if ($buyers.Count -eq 0) { Ok "no Today card buys a stop-held ticker" } else { Bad ("{0} card(s) buy a stop-held ticker: {1}" -f $buyers.Count, (($buyers | ForEach-Object { $_.title }) -join "; ")) }
Stop-Smoke
