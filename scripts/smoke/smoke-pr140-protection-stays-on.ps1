# SMOKE - PR 140: the protection card never switches off a stop; paused protection is on Today (D115).
#
#   .\scripts\smoke\smoke-pr140-protection-stays-on.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  no card on Today would pause a downside rule (stop-loss, trailing stop, profit-protect, trend break, guard)
#   2  the protection card, if shown, gives goal odds and halving side by side in its title
#   3  every downside rule that is off (and not already crossed) is on the "switched off" heads-up, with Re-arm all
#   4  every pause / re-arm since -Since left a rule event that says who did it
#
# READ-ONLY (never accepts, never re-arms, never toggles). ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 140"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

# Compared in UTC (PowerShell reads an ISO "Z" time as local; see the PR 139 smoke).
function Utc($v) {
    if ($null -eq $v -or "$v" -eq "") { return $null }
    $d = [datetime]$v
    if ($d.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($d, [DateTimeKind]::Utc) }
    return $d.ToUniversalTime()
}
$downside = @("stop_loss", "trailing_stop", "profit_protect", "trend_break", "drawdown_guard")
$rules = @((Api GET "/api/v1/rules").rules)
$byId = @{}; foreach ($r in $rules) { $byId["$($r.id)"] = $r }
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$cards = @(@($t.recommendations) + @($t.more_recommendations))

Sec "1  no card pauses a downside rule"
$protect = @($cards | Where-Object { $_.apply.kind -eq "frontier_rules" })
$bad = 0
foreach ($c in $protect) {
    foreach ($id in @($c.apply.pause_rule_ids)) {
        $r = $byId["$id"]
        if ($r -and $r.rule_type -in $downside) { Bad ("{0} would pause {1} {2}" -f $c.id, $r.ticker, $r.rule_type); $bad++ }
    }
}
if ($protect.Count -eq 0) { Skip "no protection card on Today right now" }
elseif ($bad -eq 0) { Ok ("{0} protection card(s): none pauses a downside rule" -f $protect.Count) }

Sec "2  the card's title carries both numbers"
if ($protect.Count -eq 0) { Skip "no protection card on Today right now" }
foreach ($c in $protect) {
    if ($c.title -match "goal odds" -and $c.title -match "halving") { Ok $c.title } else { Bad ("title lacks one of the two: {0}" -f $c.title) }
}

Sec "3  switched-off protection is on Today"
$off = @($rules | Where-Object { -not $_.active -and $_.rule_type -in $downside -and -not $_.breached_now })
$head = @(@($t.headsups) | Where-Object { $_.card.meta.headsup_kind -eq "protection_off" })
if ($off.Count -eq 0) {
    if ($head.Count -eq 0) { Ok "no downside rule is off, and no heads-up claims one" } else { Bad "a 'switched off' heads-up with nothing off" }
} elseif ($head.Count -eq 0) {
    Bad ("{0} downside rule(s) off and no heads-up (or it was Ignored): {1}" -f $off.Count, (($off | ForEach-Object { "$($_.ticker) $($_.rule_type)" }) -join ", "))
} else {
    $listed = @($head[0].card.meta.rule_ids | ForEach-Object { "$_" })
    $missing = @($off | Where-Object { $listed -notcontains "$($_.id)" })
    if ($missing.Count -eq 0) { Ok ("heads-up lists all {0} off: {1}" -f $off.Count, $head[0].title) }
    else { Bad ("heads-up misses {0}" -f (($missing | ForEach-Object { "$($_.ticker) $($_.rule_type)" }) -join ", ")) }
    if ($head[0].apply.kind -eq "rearm_rules") { Ok ("act: {0}" -f $head[0].act.label) } else { Bad "heads-up has no Re-arm act" }
}

Sec "4  every pause and re-arm says who did it"
$ev = @((Api GET "/api/v1/rules/events?limit=200").events)
$sinceUtc = Utc $Since
$moves = @($ev | Where-Object { $_.outcome -in @("paused", "resumed") -and ((-not $sinceUtc) -or ((Utc $_.outcome_at) -ge $sinceUtc)) })
if ($moves.Count -eq 0) { Skip "no pause or re-arm in the window yet" }
foreach ($e in $moves) {
    if ($e.action.by -in @("card", "you")) { Ok ("{0} {1} {2} by {3}" -f $e.outcome, $e.ticker, $e.rule_type, $e.action.by) }
    else { Bad ("{0} {1} {2} has no 'by'" -f $e.outcome, $e.ticker, $e.rule_type) }
}
Stop-Smoke
