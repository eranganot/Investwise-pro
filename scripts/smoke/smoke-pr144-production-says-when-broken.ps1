# SMOKE - PR 144: a build or a background job that is broken says so, on Today and on the phone (D121).
#
#   .\scripts\smoke\smoke-pr144-production-says-when-broken.ps1 -Sha <short sha of the merge>
#
#   1  boot_check ran on this build and every scheduled job's in-function imports resolve
#   2  every registered job has a history entry (a run, or registered_at for one that has not run yet)
#   3  Today's job-health heads-up matches the job history: present exactly when a job is failing or stale
#   4  NOT CHANGED: PR 143's rule-check heads-up still matches the price job
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 144"
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
$st = Api GET "/api/v1/push/status"
if (-not $st) { Bad "push/status did not answer"; Stop-Smoke }
$h = $st.scheduler.history
$jobs = @($st.scheduler.jobs | ForEach-Object { $_.id })

Sec "1  the boot check"
$b = $h.boot_check
if (-not $b) { Bad "no boot_check in the job history (a build before PR 144)" }
elseif ($b.last_ok) { Ok ("boot_check ok at {0}: {1} job(s) resolve" -f $b.last_run, @($b.last_result.jobs).Count) }
else { Bad ("boot_check FAILED (an incident): {0}" -f $b.last_error) }

Sec "2  every registered job is in the history"
$missing = @($jobs | Where-Object { -not $h.$_ })
if ($missing.Count) { Bad ("no history at all for: {0}" -f ($missing -join ", ")) }
else { Ok ("{0} job(s), each with a run or a registered_at" -f $jobs.Count) }

Sec "3  the job-health heads-up matches the history"
$now = [datetime]::UtcNow
$sick = @()
foreach ($p in $h.PSObject.Properties) {
    if ($p.Name -eq "price_refresh") { continue }
    $v = $p.Value
    if ($v.last_ok -eq $false) { $sick += $p.Name; continue }
    if ($p.Name -eq "boot_check") { continue }
    $okAt = Utc $v.last_success; if (-not $okAt) { $okAt = Utc $v.registered_at }
    if ($okAt -and ($now - $okAt).TotalHours -gt 26) { $sick += $p.Name }
}
$t = Api GET "/api/v1/recommendations"
$head = @(@($t.headsups) | Where-Object { $_.card.meta.headsup_kind -eq "job_health" })
if ($sick.Count -eq 0 -and $head.Count -eq 0) { Ok "every job healthy, and no job-health heads-up" }
elseif ($sick.Count -and $head.Count) {
    $named = @($head[0].card.meta.jobs)
    $miss = @($sick | Where-Object { $named -notcontains $_ })
    if ($miss.Count) { Bad ("heads-up misses {0}" -f ($miss -join ", ")) } else { Ok ("heads-up names {0}: {1}" -f ($sick -join ", "), $head[0].title) }
    Note "a sick job on production is an incident: read its last_error"
}
elseif ($sick.Count) { Bad ("{0} failing or stale and no heads-up" -f ($sick -join ", ")) }
else { Bad ("a job-health heads-up with every job healthy: {0}" -f $head[0].title) }

Sec "4  not changed: the rule-check heads-up"
$pr = $h.price_refresh
$rc = @(@($t.headsups) | Where-Object { $_.card.meta.headsup_kind -eq "rule_check" })
if ($pr.last_ok -and $rc.Count -eq 0) { Ok "rule check healthy, no rule-check heads-up" }
elseif (-not $pr.last_ok -and $rc.Count) { Ok ("rule check failing and Today says so: {0}" -f $rc[0].title) }
else { Bad ("rule check last_ok={0} but {1} rule-check heads-up(s)" -f $pr.last_ok, $rc.Count) }
Stop-Smoke
