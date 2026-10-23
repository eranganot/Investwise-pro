# SMOKE - PR 143: a rule check that fails says so; job history survives a deploy (D118).
#
#   .\scripts\smoke\smoke-pr143-rule-check-is-loud.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  the price job ran on this build and its result carries the rule check (subjects, triggered)
#   2  if the rule check failed, the job says so with a reason, and Today carries the heads-up
#   3  job history from BEFORE the deploy is still there (needs a second deploy after PR 143: SKIP on the first)
#   4  NOT CHANGED: a healthy check puts no "rules not checked" heads-up on Today
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 143"
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
$pr = $h.price_refresh

Sec "1  the price job checked the rules"
if (-not $pr) { Bad "no price_refresh in the job history" }
elseif ($pr.last_ok -and $pr.last_result.rules) { Ok ("price_refresh ok at {0}: rules checked for {1} book(s), {2} newly triggered" -f $pr.last_run, $pr.last_result.rules.subjects, $pr.last_result.rules.triggered) }
elseif ($pr.last_ok) { Bad ("price_refresh ok at {0} but carries no rule result" -f $pr.last_run) }
else { Note ("price_refresh FAILED at {0}: {1}" -f $pr.last_run, $pr.last_error) }

$t = Api GET "/api/v1/recommendations"
$head = @(@($t.headsups) | Where-Object { $_.card.meta.headsup_kind -eq "rule_check" })

Sec "2  a failed check is named, and on Today"
if ($pr -and $pr.last_ok -eq $false) {
    Bad ("the rule check is failing (an incident): {0}" -f $pr.last_error)
    if ($head.Count) { Ok ("Today says so: {0}" -f $head[0].title) } else { Bad "and Today does not say so" }
} else { Skip "the check is not failing right now" }

Sec "3  history from before the deploy survived it"
$sinceUtc = Utc $Since
if (-not $sinceUtc) { Skip "no -Since given" }
else {
    $old = @()
    # A job that runs at boot overwrites last_run; previous_run is the run before it.
    foreach ($p in $h.PSObject.Properties) {
        $prev = Utc $p.Value.previous_run; $last = Utc $p.Value.last_run
        if (($last -and $last -lt $sinceUtc) -or ($prev -and $prev -lt $sinceUtc)) { $old += $p.Name }
    }
    if ($old.Count) { Ok ("{0} job(s) remember a run from before {1}: {2}" -f $old.Count, $Since, ($old -join ", ")) }
    else { Skip "no job remembers a run from before the deploy (expected on PR 143's own deploy: nothing was persisted yet)" }
}

Sec "4  not changed: a healthy check raises nothing"
if ($pr -and $pr.last_ok) {
    if ($head.Count -eq 0) { Ok "no 'rules not checked' heads-up" } else { Bad ("heads-up while the check is healthy: {0}" -f $head[0].title) }
} else { Skip "the check is not healthy right now" }
Stop-Smoke
