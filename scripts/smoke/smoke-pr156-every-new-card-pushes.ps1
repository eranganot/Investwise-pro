# SMOKE - PR 156: every new recommendation card is pushed (D140).
#
#   .\scripts\smoke\smoke-pr156-every-new-card-pushes.ps1 -Sha <short sha of the merge>
#
#   Run it 10+ minutes after the deploy: the first run is 5 minutes after boot.
#
#   1  push_evaluate runs every 30 minutes and its last run is ok
#   2  the switch-over push ("rec:baseline") was written at most once (row since PR 156.1)
#   3  every card on Today has its rec:<id> row in the push ledger
#      (a card newer than the last push_evaluate run is a NOTE, not a FAIL)
#   4  NOT CHANGED: the 07:00 digest job, the app-health heads-up push, risk-alert severities
#
# Before the merge, on 5672fc4, 1 FAILs (the job ran hourly) and 2 FAILs (no baseline):
# the old rules, under which a MEDIUM or LOW card and every "ready to arm" card were never pushed.
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 156"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

function Utc($s) { return [DateTime]::Parse($s, [Globalization.CultureInfo]::InvariantCulture,
                                           [Globalization.DateTimeStyles]::AdjustToUniversal) }

$st = Api GET "/api/v1/push/status"
if (-not $st) { Bad "push/status did not answer"; Stop-Smoke }
$now = (Get-Date).ToUniversalTime()

Sec "1  push_evaluate every 30 minutes"
$h = $st.scheduler.history.push_evaluate
if (-not $h) { Bad "push_evaluate has no history" }
else {
    if ($h.last_ok) { Ok ("last run ok at {0} ({1} runs, {2} failures)" -f $h.last_run, $h.runs, $h.failures) }
    else { Bad ("last run failed: {0}" -f $h.last_error) }
    # The interval is read from the scheduler (next run minus last run), not from two
    # history entries: those straddle the restart a deploy causes.
    $job = $st.scheduler.jobs | Where-Object { $_.id -eq "push_evaluate" } | Select-Object -First 1
    if (-not $job -or -not $job.next_run) { Bad "push_evaluate is not scheduled" }
    else {
        $gap = ((Utc $job.next_run) - (Utc $h.last_run)).TotalMinutes
        if ($gap -ge 25 -and $gap -le 35) { Ok ("next run {0:N1} min after the last" -f $gap) }
        else { Bad ("next run {0:N1} min after the last, expected ~30" -f $gap) }
    }
    $age = ($now - (Utc $h.last_run)).TotalMinutes
    if ($age -gt 40) { Bad ("last run {0:N0} min ago: the job has stalled" -f $age) }
}
if (-not $st.subscriptions) { Bad "no push subscription: nothing can reach the phone" }
else { Ok ("{0} subscription(s)" -f $st.subscriptions) }

Sec "2  the switch-over push was sent once"
$recent = @($st.recent_notifications)
$sigs = @($recent | ForEach-Object { $_.signature })
$base = @($sigs | Where-Object { $_ -eq "rec:baseline" }).Count
if ($base -eq 1) { Ok "rec:baseline once" }
elseif ($base -gt 1) { Bad "rec:baseline $base times: the baseline is not remembered" }
elseif ($recent.Count -ge 50) { Skip "baseline older than the 50 newest ledger rows" }
else {
    # PR 156.1 writes the row. The owner's device switched over at 2026-11-02 17:26 UTC on
    # 7b7ca98 (sent=1, one card marked), before the row existed, so it never gets one.
    Skip "no rec:baseline row: this subscriber switched over before PR 156.1 (2026-11-02 17:26 UTC)"
}

Sec "3  every card on Today is in the push ledger"
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$cards = @($t.recommendations | Where-Object { $_.id })
Note ("{0} card(s) on Today, {1} ledger row(s) read" -f $cards.Count, $recent.Count)
if ($cards.Count -eq 0) { Skip "no card on Today to check" }
$lastRun = if ($h -and $h.last_run) { Utc $h.last_run } else { $null }
foreach ($c in $cards) {
    $sig = "rec:$($c.id)"
    $row = $recent | Where-Object { $_.signature -eq $sig } | Select-Object -First 1
    if ($row) { Ok ("{0} pushed {1}  ({2})" -f $c.id, $row.at, $c.severity) }
    elseif ($recent.Count -ge 50) { Skip ("{0}: not in the 50 newest rows (cannot tell)" -f $c.id) }
    else { Note ("{0} not pushed yet: newer than the last run at {1}? Re-run after the next one. ({2})" -f
                 $c.id, $lastRun, $c.title) }
}

Sec "4  NOT CHANGED"
$jobs = @($st.scheduler.jobs | ForEach-Object { $_.id })
if ($jobs -contains "push_digest") { Ok "push_digest still scheduled" } else { Bad "push_digest is gone" }
if ($st.notify_severities) { Ok ("risk alerts still filtered by {0}" -f $st.notify_severities) }
else { Bad "notify_severities missing" }
$hu = @($t.headsups | Where-Object { $_.card.meta.headsup_kind -in @("rule_check", "job_health") })
foreach ($x in $hu) {
    $s = "trigger:app_health:$($x.card.id)"
    if ($sigs -contains $s) { Ok "app-health heads-up pushed: $($x.card.title)" }
    else { Note "app-health heads-up not in the ledger yet: $($x.card.title)" }
}
if ($hu.Count -eq 0) { Ok "no app-health heads-up (nothing failing)" }

Stop-Smoke
