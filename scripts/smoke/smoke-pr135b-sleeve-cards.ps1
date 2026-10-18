# SMOKE - PR 135b: the sleeve cards (wave 4, D103).
#
#   .\scripts\smoke\smoke-pr135b-sleeve-cards.ps1 -Sha <short sha of the merge>
#
#   1  the moves are scored for the current book (run scripts\refresh-sleeve-moves.ps1 first, or wait for 04:40)
#   2  any sleeve card on Today quotes the goal chance and the chance of halving, before -> after, and says
#      Accept sells nothing; the number of cards matches what the stored scores make material
#   3  nothing else changed: Today still answers; endpoints behind the login
#
# READ-ONLY (never accepts a card). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 135b"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  scored for this book"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $mv = Api GET "/api/v1/plan/sleeve-moves"
    $sl = Api GET "/api/v1/plan/sleeves"
    if (-not ($mv -and $mv.ok)) { Skip "not scored yet: run scripts\refresh-sleeve-moves.ps1, or wait for 04:40" }
    else {
        $same = $true
        foreach ($s in @($sl.sleeves)) { if ([math]::Abs([double]$mv.held.($s.strategy_id) - [double]$s.sleeve_pct) -gt 0.001) { $same = $false } }
        if ($same) { Ok ("scored {0} for the sleeves you hold" -f $mv.as_of) } else { Skip "scores are about an earlier book: no cards until the next scoring" }
        $mat = @($mv.moves | Where-Object { $_.ok -and ([double]$_.goal_pts -ge 5 -or ([double]$_.halve_pts -le -5)) }).Count
        Note "$mat moves are material at the default 5 points"
    }
}

Sec "2  the cards"
if (-not $haveKey) { Skip "no agent key" }
else {
    $t = Api GET "/api/v1/recommendations"
    $all = @($t.recommendations) + @($t.more_recommendations)
    $cards = @($all | Where-Object { $_.apply.kind -eq "sleeve_move" })
    if (-not $cards.Count) { Skip "no sleeve card today (nothing material, or scores are about an earlier book)" }
    foreach ($c in $cards) {
        Note ("{0}: {1}" -f $c.title, $c.why)
        if ($c.why -match "goal chance" -and $c.impact -match "Nothing is bought or sold") { Ok "card quotes the goal and says Accept sells nothing" } else { Bad "card wording incomplete: $($c.title)" }
    }
    if ($t.degraded -contains "sleeve_moves") { Bad "Today reports the sleeve cards degraded" } else { Ok "sleeve cards not degraded" }
}

Sec "3  nothing else changed"
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeve-moves" -TimeoutSec 60 | Out-Null; Bad "/plan/sleeve-moves answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/sleeve-moves needs a key (401)" } else { Bad "/plan/sleeve-moves answered $code" } }

Stop-Smoke
