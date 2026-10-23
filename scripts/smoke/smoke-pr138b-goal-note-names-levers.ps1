# SMOKE - PR 138b: the goal "nothing to accept" heads-up names what would close the gap.
#
#   .\scripts\smoke\smoke-pr138b-goal-note-names-levers.ps1 -Sha <short sha of the merge>
#
#   1  the optimiser ceiling heads-up (when Today shows one) names a lever: a lower target
#      return, a monthly deposit, a later deadline, or says none works with the rules armed
#   2  its action names the target, the deadline and the deposits as the owner's to set
#   3  NOT CHANGED: it still quotes what the optimiser tried; it is still a heads-up (no Accept)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 138b"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$t = Api GET "/api/v1/recommendations"
$lv = (Api GET "/api/v1/plan/frontier").levers
$heads = @(@($t.headsups) | Where-Object { $_.card.meta.optimizer -and $_.card.meta.ceiling })

Sec "1  the heads-up names a lever"
if ($heads.Count -eq 0) { Skip "no optimiser ceiling heads-up today (on track, or no solve yet)"; Stop-Smoke }
foreach ($h in $heads) {
    Note $h.title
    $why = "$($h.card.why)"
    if ($why -match "target return from|a month|deadline out by|no deposit and no later deadline") { Ok "names a lever" }
    elseif (-not $lv) { Skip "the stored solve has no levers to name" }
    else { Bad "the solve measured levers and the note names none" }
}

Sec "2  the action says whose levers they are"
foreach ($h in $heads) {
    if ("$($h.card.action)" -match "target, the deadline, the deposits") { Ok "action names target, deadline, deposits" } else { Bad ("action is the old text: {0}" -f $h.card.action) }
}

Sec "3  not changed"
foreach ($h in $heads) {
    if ("$($h.card.why)" -match "tried \d+ changes") { Ok "still quotes what was tried" } else { Bad "no longer quotes what was tried" }
    if ($h.card.apply -and $h.card.apply.kind -ne "none") { Bad ("the heads-up now applies {0}" -f $h.card.apply.kind) } else { Ok "still words only (apply: none)" }
}
Stop-Smoke
