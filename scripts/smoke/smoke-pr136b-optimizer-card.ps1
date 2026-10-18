# SMOKE - PR 136b: the optimiser's card (wave 5, D106).
#
#   .\scripts\smoke\smoke-pr136b-optimizer-card.ps1 -Sha <short sha of the merge>
#
#   1  the optimiser has a result for this book (run scripts\run-optimizer.ps1 first, or wait for 05:10)
#   2  Today shows the matching optimiser card or heads-up: actionable when the gain is material, else a
#      heads-up quoting the best measured and the price of the halving limit
#   3  nothing else changed: Today not degraded on the optimiser; endpoints behind the login
#
# READ-ONLY (never accepts). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 136b"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the optimiser result"
$res = $null
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $res = Api GET "/api/v1/plan/optimizer"
    if ($res -and $res.ok) { Ok ("result {0}: {1}% today, {2} steps" -f $res.as_of, $res.before.p_goal_pct, @($res.steps).Count) }
    else { Skip "no result yet: run scripts\run-optimizer.ps1, or wait for 05:10" }
}

Sec "2  Today"
if ($haveKey) {
    $t = Api GET "/api/v1/recommendations"
    $cards = @(@($t.recommendations) + @($t.more_recommendations) | Where-Object { $_.meta.optimizer })
    $heads = @(@($t.headsups) | Where-Object { $_.card.meta.optimizer })
    foreach ($c in $cards) { Note $c.title; if ($c.apply.kind -eq "sleeve_move" -and $c.impact -match "Nothing is bought or sold") { Ok "actionable optimiser card" } else { Bad "optimiser card incomplete" } }
    foreach ($h in $heads) { Note $h.title; if ($h.card.why -match "tried \d+ changes") { Ok "ceiling heads-up quotes what was tried" } else { Bad "ceiling heads-up incomplete" } }
    if (-not $cards.Count -and -not $heads.Count) {
        if ($res -and $res.ok) { Skip "no optimiser card: on track, or the result is about an earlier book" } else { Skip "no optimiser result yet" }
    }
    if ($t.degraded -contains "optimizer") { Bad "Today reports the optimiser degraded" } else { Ok "optimiser card not degraded" }
}

Sec "3  nothing else changed"
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/optimizer" -TimeoutSec 60 | Out-Null; Bad "/plan/optimizer answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/optimizer needs a key (401)" } else { Bad "/plan/optimizer answered $code" } }

Stop-Smoke
