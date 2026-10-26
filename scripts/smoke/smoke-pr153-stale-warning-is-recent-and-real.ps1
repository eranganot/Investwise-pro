# SMOKE - PR 153: Today's "Some checks didn't run: history_stale" is recent and real (D136).
#
#   .\scripts\smoke\smoke-pr153-stale-warning-is-recent-and-real.ps1 -Sha <short sha of the merge>
#
#   1  providers/health reports stale_warning (and keeps stale_served) for history and fundamentals
#   2  Today names history_stale exactly when a provider's stale_warning is true
#   3  a stale_warning is backed by a stale read a failure forced within the window (30 min)
#   4  NOT CHANGED: an OPEN history / fundamentals circuit still names the agents it silences
#
# Before the merge, on f610693, 1 FAILs (no stale_warning) and 2 FAILs when Today shows
# history_stale with no failure in the last 30 minutes: the bug as it was seen on 2026-10-26.
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 153"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$windowSec = 1800
$ph = Api GET "/api/v1/providers/health"
if (-not $ph) { Bad "providers/health did not answer"; Stop-Smoke }

Sec "1  health reports the warning, and keeps the lifetime count"
$warn = $false
foreach ($n in @("history", "fundamentals")) {
    $p = $ph.$n
    if (-not $p) { Bad "$n missing from providers/health"; continue }
    Note ("{0}: circuit {1}, stale_served {2}, on_failure {3}, {4}s since, last_error {5}s ago" -f $n,
          $p.circuit_state, $p.stale_served, $p.stale_served_on_failure, $p.seconds_since_stale_on_failure,
          $p.seconds_since_last_error)
    if ($null -eq $p.stale_served) { Bad "$n lost stale_served (the health page and smokes read it)" }
    if ($null -eq $p.stale_warning) { Bad "$n has no stale_warning: Today still reads the lifetime counter"; continue }
    Ok ("{0}: stale_warning = {1}" -f $n, $p.stale_warning)
    if ($p.stale_warning) { $warn = $true }
}

Sec "2  Today names history_stale exactly when a warning is true"
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$deg = @($t.degraded)
$shown = $deg -contains "history_stale"
Note ("degraded: {0}" -f (($deg -join ", ") -replace "^$", "none"))
$recentFail = @("history", "fundamentals" | Where-Object {
    $s = $ph.$_.seconds_since_last_error; $null -ne $s -and [double]$s -le $windowSec }).Count -gt 0
if ($shown -and -not $warn -and -not $recentFail) {
    Bad "history_stale is on Today with no failure in the last 30 minutes (the 2026-10-26 report)"
} elseif ($shown -eq $warn) { Ok ("history_stale shown = {0}, matches the warning" -f $shown) }
elseif ($shown) { Bad "history_stale shown while no provider warns" }
else { Bad "a provider warns but Today does not name history_stale" }

Sec "3  a warning is backed by a recent forced stale read"
if (-not $warn) { Skip "no warning right now (nothing failed in the window)" }
foreach ($n in @("history", "fundamentals")) {
    $p = $ph.$n
    if (-not $p.stale_warning) { continue }
    $s = $p.seconds_since_stale_on_failure
    if ($null -ne $s -and [double]$s -le $windowSec -and [int]$p.stale_served_on_failure -gt 0) {
        Ok ("{0}: forced stale read {1}s ago" -f $n, $s)
    } else { Bad ("{0}: warning without a forced stale read in the window ({1}s)" -f $n, $s) }
}

Sec "4  NOT CHANGED: an open circuit names the agents it silences"
$open = @("history", "fundamentals" | Where-Object { $ph.$_.circuit_state -eq "OPEN" })
if ($open.Count -eq 0) { Skip "no circuit is open" }
if ($open -contains "history") {
    $miss = @("holding_verdicts", "momentum", "war_room" | Where-Object { $deg -notcontains $_ })
    if ($miss.Count) { Bad ("history OPEN but degraded lacks {0}" -f ($miss -join ", ")) } else { Ok "history OPEN is named" }
}
if ($open -contains "fundamentals") {
    $miss = @("holding_verdicts", "hedge" | Where-Object { $deg -notcontains $_ })
    if ($miss.Count) { Bad ("fundamentals OPEN but degraded lacks {0}" -f ($miss -join ", ")) } else { Ok "fundamentals OPEN is named" }
}

Stop-Smoke
