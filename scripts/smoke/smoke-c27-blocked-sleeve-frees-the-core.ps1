# SMOKE - C27 (D127): money a stop-held sleeve cannot use is not reserved; the core card can act on it.
#
#   .\scripts\smoke\smoke-c27-blocked-sleeve-frees-the-core.ps1 -Sha <short sha of the merge>
#
#   1  the core card reports the sleeve shortfall and the part a stop hold blocks
#   2  it reserves ("NOT sold here") only when part of the shortfall is buyable
#   3  a triggered rebalance band is not left with "nothing to execute" while the core card can sell off-basket holdings
#   4  NOT CHANGED: PR 141 -- no 2x/3x buy on Today while a 2x/3x stop hold is active
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C27"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$t = Api GET "/api/v1/recommendations"
$all = @(@($t.recommendations) + @($t.more_recommendations))
$core = @($all | Where-Object { $_.apply.kind -eq "core_to_basket" })

Sec "1  the shortfall and its blocked part"
if ($core.Count -eq 0) { Skip "no core card today (the core holds its basket)" }
else {
    $pl = $core[0].meta.plan
    if ($null -eq $pl.sleeve_short_blocked_ils) { Bad "no sleeve_short_blocked_ils on the core card (a build before C27)" }
    else { Ok ("sleeves short {0:N0} ILS, of which a stop hold blocks {1:N0}" -f [double]$pl.sleeve_short_ils, [double]$pl.sleeve_short_blocked_ils) }

    Sec "2  reserves only for a buyable shortfall"
    $how = (@($core[0].how) -join " ")
    $buyable = [double]$pl.sleeve_short_ils - [double]$pl.sleeve_short_blocked_ils
    if ($how -match "NOT sold here" -and $buyable -lt 40) { Bad ("reserves holdings for a shortfall the stop hold blocks ({0:N0} ILS buyable)" -f $buyable) }
    elseif ($how -match "NOT sold here") { Ok ("reserves for {0:N0} ILS the app can buy" -f $buyable) }
    else { Ok "nothing reserved for a blocked sleeve" }
    Note ("core card: {0} -- {1}" -f $core[0].title, $core[0].action)
}

Sec "3  a triggered band has an answer"
$bands = @(@($t.headsups) | Where-Object { $_.card.meta.rule_type -eq "rebalance_band" -and $_.card.apply.kind -eq "none" })
if ($bands.Count -eq 0) { Ok "no rebalance band left with nothing to execute" }
elseif ($core.Count -and (@($core[0].how) -join " ") -notmatch "NOT sold here") { Ok ("{0} band(s) without their own trade; the core card trades for them: {1}" -f $bands.Count, (($bands | ForEach-Object { $_.ticker }) -join ", ")) }
else { Bad ("{0} band(s) with nothing to execute and no core card to act: {1}" -f $bands.Count, (($bands | ForEach-Object { $_.ticker }) -join ", ")) }

Sec "4  not changed: PR 141"
$funds = @((Api GET "/api/v1/plan").leveraged_funds)
$rules = @((Api GET "/api/v1/rules").rules)
$holds = @($rules | Where-Object { $_.rule_type -eq "buy_back" -and $_.active -and $_.params.kind -eq "stop" -and ($funds -contains $_.ticker) })
if ($holds.Count -eq 0) { Skip "no 2x/3x stop hold right now" }
else {
    $leak = @($all | Where-Object { ($_.apply.kind -eq "buy_funded" -or $_.apply.kind -eq "buy") -and ($funds -contains $_.apply.ticker) })
    if ($leak.Count) { Bad ("a 2x/3x buy while {0} is on hold" -f $holds[0].ticker) } else { Ok ("{0} on hold; no 2x/3x buy" -f $holds[0].ticker) }
}
Stop-Smoke
