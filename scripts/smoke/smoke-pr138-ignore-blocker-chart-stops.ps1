# SMOKE - PR 138: Ignore works on every card, an unfundable sleeve is a blocker, the chart ends at today, an OFF stop is a card (D110-D113).
#
#   .\scripts\smoke\smoke-pr138-ignore-blocker-chart-stops.ps1 -Sha <short sha of the merge>
#
#   1  Today answers; payload has a blockers list
#   2  the account chart answers and says whether it has a provisional point (a point or a reason)
#   3  any stop that is OFF with its condition true has a card on Today or is not breached
#
# READ-ONLY (never accepts, never ignores). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 138"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    Sec "1  Today and blockers"
    $t = Api GET "/api/v1/recommendations"
    if ($t) { Ok "Today answered" } else { Bad "Today did not answer" }
    if ($t.PSObject.Properties.Name -contains "blockers") { Ok ("blockers present: {0}" -f @($t.blockers).Count) } else { Bad "payload has no blockers key" }
    foreach ($b in @($t.blockers)) { Note $b.title }

    Sec "2  chart"
    $h = Api GET "/api/v1/portfolio/nav-history?range=1M"
    if ($h.provisional) { Ok ("provisional point: {0} {1}%" -f $h.provisional.as_of, $h.provisional.pct) }
    elseif ($h.provisional_reason) { Ok ("no provisional point, reason: {0}" -f $h.provisional_reason) }
    else { Bad "chart says neither a point nor a reason" }

    Sec "3  stops that are off"
    $rules = @((Api GET "/api/v1/rules"))
    $cards = @(@($t.recommendations) + @($t.more_recommendations))
    foreach ($r in ($rules | Where-Object { -not $_.active -and $_.breached_now -and $_.rule_type -in @("stop_loss","trailing_stop") })) {
        $has = @($cards | Where-Object { $_.meta.rule_id -eq $r.id -and $_.apply.kind -eq "edit_rule" }).Count
        if ($has) { Ok ("{0}: off stop has its card" -f $r.ticker) } else { Bad ("{0}: stop is off and crossed with no card (or it was Ignored)" -f $r.ticker) }
    }
    Ok "read-only checks finished"
}
Stop-Smoke
