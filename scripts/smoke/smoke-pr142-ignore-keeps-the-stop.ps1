# SMOKE - PR 142: Ignoring a fired stop keeps the stop on, re-armed below today (D117).
#
#   .\scripts\smoke\smoke-pr142-ignore-keeps-the-stop.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  every fired stop card on Today says Ignore keeps the stop (stop-loss, trailing stop, profit-protect)
#   2  no stop-loss / trailing stop / profit-protect was IGNORED into "off" since -Since (outcome dismissed)
#   3  every stop Ignored since -Since is still on, not latched, and not already crossed
#   4  NOT CHANGED: every held ticker with a stop before is still covered (stops that are off are on the heads-up)
#
# READ-ONLY (never accepts, never ignores, never re-arms). ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 142"
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
$reArm = @("stop_loss", "trailing_stop", "profit_protect")
$rules = @((Api GET "/api/v1/rules").rules)
$byId = @{}; foreach ($r in $rules) { $byId["$($r.id)"] = $r }
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$cards = @(@($t.recommendations) + @($t.more_recommendations))

Sec "1  a fired stop card says what Ignore does"
$stopCards = @($cards | Where-Object { $_.id -like "rule_*" } | Where-Object {
    $rid = "$($_.rule_id)"; $r = $byId[$rid]; $r -and $r.rule_type -in $reArm })
if ($stopCards.Count -eq 0) { Skip "no fired stop on Today right now" }
foreach ($c in $stopCards) {
    if (@($c.how) -match "Ignore keeps the position and the stop") { Ok ("{0}: {1}" -f $c.id, (@($c.how) -match "^Ignore")[0]) }
    else { Bad ("{0} does not say Ignore keeps the stop: {1}" -f $c.id, (@($c.how) -join " | ")) }
}

Sec "2  no stop was ignored into off"
$ev = @((Api GET "/api/v1/rules/events?limit=200").events)
$sinceUtc = Utc $Since
if (-not $Since) { Note "no -Since: checking the whole visible log (older dismissals are pre-PR 142)" }
$inWin = @($ev | Where-Object { (-not $sinceUtc) -or ((Utc $_.outcome_at) -ge $sinceUtc) })
$off = @($inWin | Where-Object { $_.rule_type -in $reArm -and $_.outcome -eq "dismissed" })
if ($off.Count -eq 0) { Ok ("no stop-loss, trailing stop or profit-protect was dismissed{0}" -f $(if ($Since) { " since $Since" } else { " in the visible log" })) }
foreach ($e in $off) {
    if ($Since) { Bad ("{0} {1} ignored at {2} and switched off" -f $e.ticker, $e.rule_type, $e.outcome_at) }
    else { Note ("{0} {1} dismissed at {2} (before PR 142)" -f $e.ticker, $e.rule_type, $e.outcome_at) }
}

Sec "3  every ignored stop is still on"
$kept = @($inWin | Where-Object { $_.rule_type -in $reArm -and $_.outcome -eq "kept" -and $_.action.executed.by -eq "ignore" })
if ($kept.Count -eq 0) { Skip "no stop ignored in the window yet" }
foreach ($e in $kept) {
    $r = $byId["$($e.rule_id)"]
    if (-not $r) { Note ("{0} {1}: rule gone (position sold since)" -f $e.ticker, $e.rule_type); continue }
    if ($r.active -and -not $r.breached_now) { Ok ("{0} {1} on at {2}: {3}" -f $r.ticker, $r.rule_type, $r.level, $r.note) }
    elseif ($r.active -and $r.triggered) { Ok ("{0} {1} fired again after the ignore (a new card)" -f $r.ticker, $r.rule_type) }
    else { Bad ("{0} {1} after the ignore: active={2} breached={3}" -f $r.ticker, $r.rule_type, $r.active, $r.breached_now) }
}

Sec "4  not changed: switched-off stops are still reported"
$offNow = @($rules | Where-Object { -not $_.active -and $_.rule_type -in $reArm -and -not $_.breached_now })
$head = @(@($t.headsups) | Where-Object { $_.card.meta.headsup_kind -eq "protection_off" })
if ($offNow.Count -eq 0) { Ok "no stop is off" }
elseif ($head.Count -and -not @($offNow | Where-Object { @($head[0].card.meta.rule_ids) -notcontains "$($_.id)" })) { Ok ("{0} off, all on the heads-up" -f $offNow.Count) }
else { Bad ("{0} stop(s) off and not on the heads-up" -f $offNow.Count) }
Stop-Smoke
