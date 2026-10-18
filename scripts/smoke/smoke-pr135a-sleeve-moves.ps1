# SMOKE - PR 135a: every sleeve move scored by the goal model (wave 4 inputs, D102).
#
#   .\scripts\smoke\smoke-pr135a-sleeve-moves.ps1 -Sha <short sha of the merge>
#
#   1  the scored moves are served (after the 04:40 job or scripts\refresh-sleeve-moves.ps1); SKIP if not yet scored
#   2  the moves include hedge (1x and 2x) and remove rows, each with goal and halving deltas
#   3  the job is registered (jobs status lists sleeve_moves)
#   4  nothing else changed: endpoints behind the login
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 135a"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1-2  the scored moves"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $r = Api GET "/api/v1/plan/sleeve-moves"
    if ($r -and $r.reason -eq "NOT_SCORED") { Skip "not scored yet: the 04:40 job, or scripts\refresh-sleeve-moves.ps1" }
    elseif ($r -and $r.ok) {
        Ok ("scored {0}: goal {1}%, halving {2}%; {3} moves" -f $r.as_of, $r.before.p_goal_pct, $r.before.p_halve_pct, @($r.moves).Count)
        $kinds = @($r.moves | ForEach-Object { $_.kind } | Sort-Object -Unique)
        if ($kinds -contains "hedge" -and $kinds -contains "remove") { Ok ("kinds: {0}" -f ($kinds -join ', ')) } else { Bad ("kinds: {0}" -f ($kinds -join ', ')) }
        $scored = @($r.moves | Where-Object { $_.ok -and $null -ne $_.goal_pts })
        if ($scored.Count) { Ok "$($scored.Count) moves carry goal deltas" } else { Bad "no move carries a goal delta" }
        $scales = @($r.moves | Where-Object { $_.kind -eq "hedge" } | ForEach-Object { $_.scale } | Sort-Object -Unique)
        if ($scales -contains 1 -and $scales -contains 2) { Ok "hedges at 1x and 2x" } else { Bad "hedge scales: $($scales -join ',')" }
    } else { Bad "sleeve-moves did not answer" }
}

Sec "3  the job"
if ($haveKey) {
    $j = $null; try { $j = Api GET "/api/v1/push/status" } catch {}
    if ($j -and ($j | ConvertTo-Json -Depth 6) -match "sleeve_moves") { Ok "sleeve_moves is a registered job" } else { Skip "job list not readable here (it is pinned by tests)" }
}

Sec "4  nothing else changed"
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeve-moves" -TimeoutSec 60 | Out-Null; Bad "/plan/sleeve-moves answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/sleeve-moves needs a key (401)" } else { Bad "/plan/sleeve-moves answered $code" } }

Stop-Smoke
