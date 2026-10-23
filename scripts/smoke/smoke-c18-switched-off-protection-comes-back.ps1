# SMOKE - C18 (D124): switched-off protection re-arms itself after rearm_after_days (3), never one already crossed.
#
#   .\scripts\smoke\smoke-c18-switched-off-protection-comes-back.ps1 -Sha <short sha of the merge>
#
#   1  the setting is on the plan (Card-thresholds sheet): rearm_after_days, default 3
#   2  no downside rule has been off longer than the setting (+1 h for the 30-min check), unless it is crossed
#   3  every automatic re-arm left a rule event by "auto" with its reason and (C18.1) its price
#   4  NOT CHANGED: a crossed rule that is off is still off (PR 138's card), and still listed
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app", [string]$C181Since = "2026-10-23T18:40:00Z")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C18"
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
$plan = Api GET "/api/v1/plan"
$days = $plan.thresholds.rearm_after_days

Sec "1  the setting"
if ($null -eq $days) { Bad "no rearm_after_days on the plan (a build before C18)"; Stop-Smoke }
Ok ("rearm_after_days = {0}" -f $days)

$downside = @("stop_loss", "trailing_stop", "profit_protect", "trend_break", "drawdown_guard")
$rules = @((Api GET "/api/v1/rules").rules)
$ev = @((Api GET "/api/v1/rules/events?limit=200").events)
$now = [datetime]::UtcNow

Sec "2  nothing is off longer than the setting"
if ([double]$days -le 0) { Skip "set to 0 (never)" }
else {
    $off = @($rules | Where-Object { -not $_.active -and $_.rule_type -in $downside -and -not $_.breached_now })
    $late = @()
    foreach ($r in $off) {
        $p = @($ev | Where-Object { "$($_.rule_id)" -eq "$($r.id)" -and $_.outcome -eq "paused" } | Sort-Object { Utc $_.outcome_at } -Descending)[0]
        if ($p -and ($now - (Utc $p.outcome_at)).TotalHours -gt ([double]$days * 24 + 1)) { $late += ("{0} {1} (off since {2})" -f $r.ticker, $r.rule_type, $p.outcome_at) }
        elseif (-not $p) { Note ("{0} {1} is off with no pause event in the last 200 (paused before PR 140): its last update decides" -f $r.ticker, $r.rule_type) }
    }
    if ($late.Count) { foreach ($x in $late) { Bad ("still off past {0} days: {1}" -f $days, $x) } }
    else { Ok ("{0} downside rule(s) off, none past {1} days" -f $off.Count, $days) }
}

Sec "3  automatic re-arms say so"
$auto = @($ev | Where-Object { $_.outcome -eq "resumed" -and $_.action.by -eq "auto" })
if ($auto.Count -eq 0) { Skip "no automatic re-arm yet" }
foreach ($e in $auto) {
    if ($e.action.off_since -and $e.action.after_days) { Ok ("{0} {1} re-armed at {2} (off since {3})" -f $e.ticker, $e.rule_type, $e.outcome_at, $e.action.off_since) }
    else { Bad ("{0} {1} re-armed with no reason recorded" -f $e.ticker, $e.rule_type) }
    # C18.1: the event records the price it re-armed at. Re-arms before C18.1 deployed have none.
    if ($e.trigger_price) { Ok ("{0} re-armed at price {1}" -f $e.ticker, $e.trigger_price) }
    elseif ((Utc $e.outcome_at) -lt (Utc $C181Since)) { Note ("{0}: re-armed before C18.1, no price recorded" -f $e.ticker) }
    else { Bad ("{0} re-armed with no price recorded" -f $e.ticker) }
}

Sec "4  not changed: a crossed rule stays off"
$crossed = @($rules | Where-Object { -not $_.active -and $_.rule_type -in $downside -and $_.breached_now })
if ($crossed.Count -eq 0) { Skip "no switched-off rule is crossed right now" }
else { Ok ("{0} crossed rule(s) left off for you to decide: {1}" -f $crossed.Count, (($crossed | ForEach-Object { "$($_.ticker) $($_.rule_type)" }) -join ", ")) }
Stop-Smoke
