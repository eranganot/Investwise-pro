# SMOKE - PR 139: a stop-out is held against every buyer until its sleeve re-enters (D114).
#
#   .\scripts\smoke\smoke-pr139-stop-out-hold.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  every protective exit executed since -Since carries a stop hold (the 10-12 / 10-16 SOXL exits carried none)
#   2  every stop hold is LISTED, even though its ticker is no longer held, and says what ends it
#   3  no card on Today buys a ticker under a stop hold (funded buy, cash redeploy leg, sleeve top-up)
#
# READ-ONLY (never accepts, never ignores, never ends a hold). ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 139"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$protective = @("stop_loss", "trailing_stop", "profit_protect", "trend_break")
$rulesBody = Api GET "/api/v1/rules"
$rules = @($rulesBody.rules)
$holds = @($rules | Where-Object { $_.rule_type -eq "buy_back" -and $_.active -and $_.params.kind -eq "stop" })
$heldTk = @($holds | ForEach-Object { $_.ticker.ToUpper() })

Sec "1  protective exits record a stop hold"
$events = @((Api GET "/api/v1/rules/events?limit=200"))
if ($events.Count -eq 1 -and $events[0].events) { $events = @($events[0].events) }
# Compared in UTC. PowerShell turns an ISO string ending in Z into LOCAL time and
# reads a stamp with no zone as local too, so on a machine in Israel a mixed
# comparison is three hours off and silently drops the newest exits.
function Utc($v) {
    if ($null -eq $v -or "$v" -eq "") { return $null }
    $d = [datetime]$v
    if ($d.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($d, [DateTimeKind]::Utc) }
    return $d.ToUniversalTime()
}
$sinceUtc = Utc $Since
$exits = @($events | Where-Object {
    $_.outcome -eq "executed" -and $_.rule_type -in $protective -and $_.action.executed.kind -eq "sell_position" -and
    ((-not $sinceUtc) -or ((Utc $_.outcome_at) -ge $sinceUtc)) })
if (-not $Since) { Note "no -Since: checking every protective exit in the log, older ones will show as pre-PR 139" }
if ($exits.Count -eq 0) { Skip "no protective exit executed in the window yet" }
foreach ($e in $exits) {
    $bh = $e.action.executed.buyback_hold
    if ($bh -and $bh.kind -eq "stop") { Ok ("{0} {1} at {2}: stop hold recorded" -f $e.ticker, $e.rule_type, $e.outcome_at) }
    elseif ($Since) { Bad ("{0} {1} at {2}: executed with no stop hold" -f $e.ticker, $e.rule_type, $e.outcome_at) }
    else { Note ("{0} {1} at {2}: no hold (expected before PR 139)" -f $e.ticker, $e.rule_type, $e.outcome_at) }
}

Sec "2  every stop hold is listed and says what ends it"
if ($holds.Count -eq 0) { Skip "no stop hold on the book" }
foreach ($h in $holds) {
    if ($h.note -match "no re-buy until") { Ok ("{0}: {1}" -f $h.ticker, $h.note) } else { Bad ("{0}: note does not say what ends the hold: {1}" -f $h.ticker, $h.note) }
    if ($h.triggered) { Bad ("{0}: a stop hold fired on a price (it never may)" -f $h.ticker) } else { Ok ("{0}: not fired by a price" -f $h.ticker) }
}

Sec "3  Today buys nothing that is on hold"
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer" }
else {
    $cards = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | ForEach-Object { $_.card } | Where-Object { $_ }))
    $owners = @($holds | ForEach-Object { $_.params.strategy_id } | Where-Object { $_ })
    $hits = 0
    foreach ($c in $cards) {
        $a = $c.apply; if (-not $a) { continue }
        $buys = @()
        if ($a.kind -eq "buy_funded" -and $a.ticker) { $buys += $a.ticker.ToUpper() }
        if ($a.kind -eq "redeploy_cash") { $buys += @($a.legs | ForEach-Object { "$($_.ticker)".ToUpper() }) }
        if ($a.kind -eq "fee_swap" -and $a.buy) { $buys += $a.buy.ToUpper() }
        foreach ($b in $buys) { if ($heldTk -contains $b) { Bad ("card {0} buys {1}, which is on hold" -f $c.id, $b); $hits++ } }
        if ($a.kind -eq "fund_sleeve" -and $owners -contains $a.strategy_id -and $c.apply.kind -ne "none") {
            Note ("card {0} tops up {1}; its held ticker must be left out at Accept (the executor refuses it)" -f $c.id, $a.strategy_id)
        }
    }
    if ($heldTk.Count -eq 0) { Skip "no ticker on hold, nothing to check against Today" }
    elseif ($hits -eq 0) { Ok ("{0} card(s) checked, none buys {1}" -f $cards.Count, ($heldTk -join ", ")) }
}
Stop-Smoke
