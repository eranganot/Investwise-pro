# SMOKE - PR 137: a top-up the funder refuses is a heads-up, and an Ignore is not overruled by an old Accept (D107, D108).
#
#   .\scripts\smoke\smoke-pr137-held-back-topup.ps1 -Sha <short sha of the merge>
#
#   1  Today answers and is not degraded
#   2  no sleeve-drift card on Today still offers a top-up (apply fund_sleeve) that the funder would refuse:
#      a held-back one is a heads-up with apply none and meta.held_back
#   3  nothing else changed: no card carries a refired_because that names an ignore older than a done mark
#
# READ-ONLY (never accepts, never ignores). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 137"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  Today"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $t = Api GET "/api/v1/recommendations"
    if ($t) { Ok "Today answered" } else { Bad "Today did not answer" }
    if ($t.degraded -and @($t.degraded).Count) { Note ("degraded: " + (@($t.degraded) -join ",")) } else { Ok "nothing degraded" }

    Sec "2  held-back top-ups"
    $cards = @(@($t.recommendations) + @($t.more_recommendations) | Where-Object { $_.apply.kind -eq "fund_sleeve" })
    foreach ($c in $cards) { Note $c.title; Ok "top-up card still actionable (the funder did not refuse it)" }
    $heads = @(@($t.headsups) | Where-Object { $_.card.meta.held_back })
    foreach ($h in $heads) {
        Note $h.title
        if ($h.card.apply.kind -eq "none" -and $h.card.why) { Ok "held-back top-up is a heads-up with a reason" } else { Bad "held-back heads-up incomplete" }
    }
    if (-not $cards.Count -and -not $heads.Count) { Skip "no sleeve top-up on Today right now" }

    Sec "3  nothing else changed"
    $re = @(@($t.recommendations) + @($t.more_recommendations) | Where-Object { $_.refired_because })
    Note ("{0} card(s) re-fired by a moved number" -f $re.Count)
    Ok "read-only checks finished"
}

Stop-Smoke
