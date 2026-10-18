# SMOKE - PR 136a: the goal optimiser (wave 5, D105).
#
#   .\scripts\smoke\smoke-pr136a-optimizer.ps1 -Sha <short sha of the merge>
#
#   1  the stored result is served (after 05:10 or scripts\run-optimizer.ps1); SKIP if not run yet
#   2  it carries today's and the best book's goal chance and chance of halving, the steps, and step B
#   3  the minimum step is a setting (default 0.5)
#   4  nothing else changed: endpoints behind the login
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 136a"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1-2  the stored result"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $r = Api GET "/api/v1/plan/optimizer"
    if ($r -and $r.reason -eq "NOT_RUN") { Skip "not run yet: 05:10, or scripts\run-optimizer.ps1" }
    elseif ($r -and $r.ok) {
        Ok ("run {0}: today {1}% / halving {2}%; {3} steps; {4} candidates" -f $r.as_of, $r.before.p_goal_pct, $r.before.p_halve_pct, @($r.steps).Count, $r.candidates_tried)
        if ($null -ne $r.halve_limit_pct -and $null -ne $r.min_step_pts) { Ok "limits recorded ($($r.halve_limit_pct)% / $($r.min_step_pts) pts)" } else { Bad "limits missing" }
        if (@($r.steps).Count -and -not $r.after) { Bad "steps but no best-book score" } else { Ok "best book scored (or no step)" }
    } else { Bad "optimizer did not answer" }
}

Sec "3  the minimum step is a setting"
if ($haveKey) {
    $pl = Api GET "/api/v1/plan"
    if ([double]$pl.thresholds_defaults.optimizer_min_step_pts -eq 0.5) { Ok "optimizer_min_step_pts default 0.5" } else { Bad "optimizer_min_step_pts missing" }
}

Sec "4  nothing else changed"
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/optimizer" -TimeoutSec 60 | Out-Null; Bad "/plan/optimizer answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/optimizer needs a key (401)" } else { Bad "/plan/optimizer answered $code" } }

Stop-Smoke
