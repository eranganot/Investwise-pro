# SMOKE - PR 147: every Buy / Sell the ledger records since the deploy carries a price and a value (D120).
#
#   .\scripts\smoke\smoke-pr147-the-ledger-says-what-it-sold.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  no Buy or Sell row since -Since has price 0 or value 0 (before: 140 of 440 rows, every stop, trim and funded buy)
#   2  the count of older holes, for the record (they are not rewritten by this PR)
#   3  NOT CHANGED: cash moved by trades is still reported (rule events carry cash_added_ils on trims and stops)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 147"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

function Utc($v) {
    if ($null -eq $v -or "$v" -eq "") { return $null }
    $d = [datetime]$v
    if ($d.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($d, [DateTimeKind]::Utc) }
    return $d.ToUniversalTime()
}
$rows = @((Api GET "/api/v1/portfolio/ledger?limit=2000").entries)
$sinceUtc = Utc $Since
$trades = @($rows | Where-Object { $_.side -in @("Buy", "Sell") -and [double]$_.quantity -gt 0 })
$holes = @($trades | Where-Object { [double]$_.price -le 0 -or [double]$_.value_ils -le 0 })

Sec "1  every trade since the deploy has a price and a value"
if (-not $sinceUtc) { Skip "no -Since given" }
else {
    $new = @($trades | Where-Object { (Utc $_.at) -ge $sinceUtc })
    $newHoles = @($new | Where-Object { [double]$_.price -le 0 -or [double]$_.value_ils -le 0 })
    if ($new.Count -eq 0) { Skip "no trade since the deploy yet" }
    elseif ($newHoles.Count -eq 0) { Ok ("{0} trade(s) since {1}, all with a price and a value" -f $new.Count, $Since) }
    foreach ($h in $newHoles) { Bad ("{0} {1} {2} x{3} ({4}): price {5}, value {6}" -f $h.at, $h.side, $h.ticker, $h.quantity, $h.source, $h.price, $h.value_ils) }
}

Sec "2  older rows without a price or value (not rewritten here)"
$old = @($holes | Where-Object { (-not $sinceUtc) -or ((Utc $_.at) -lt $sinceUtc) })
Note ("{0} of {1} trade rows from before the deploy lack a price or value" -f $old.Count, $trades.Count)
Ok "counted"

Sec "3  not changed: trims and stops still report the cash they moved"
$ev = @((Api GET "/api/v1/rules/events?limit=200").events)
$sales = @($ev | Where-Object { $_.outcome -eq "executed" -and $_.action.executed.kind -in @("trim", "sell_position") -and
    ((-not $sinceUtc) -or ((Utc $_.outcome_at) -ge $sinceUtc)) })
if ($sales.Count -eq 0) { Skip "no rule sale in the window" }
foreach ($e in $sales) {
    $x = $e.action.executed
    if ([double]$x.cash_added_ils -gt 0) { Ok ("{0} {1}: cash +{2}{3}" -f $e.ticker, $x.kind, $x.cash_added_ils, $(if ($x.price) { ", price " + $x.price } else { "" })) }
    else { Bad ("{0} {1}: no cash_added_ils" -f $e.ticker, $x.kind) }
}
Stop-Smoke
