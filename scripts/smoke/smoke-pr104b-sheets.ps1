# SMOKE - PR 104b: the Edit-goal and Edit-rules sheets' live odds preview.
#
#   .\scripts\smoke\smoke-pr104b-sheets.ps1 -Sha <sha serving now>
#
#   1  POST /plan/frontier/preview answers for the goal as it is, and the headline
#      matches the stored verdict's (same model, same rules)
#   2  a later deadline raises the goal ("30%/yr on every shekel"); a lower ROI
#      lowers it; each preview says how long it took
#   3  rules as they would be: pausing one armed stop moves the odds; nothing saved
#   4  no verdict was stored and no rule changed (read-only)
#   The sheets themselves are checked on the phone.
#
# READ-ONLY: previews only, saves nothing. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104b"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

function Preview($body) {
    $t0 = Get-Date
    $r = $null
    try { $r = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/plan/frontier/preview" -Headers $script:ApiHeaders -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 300 } catch { Note "preview -> $($_.Exception.Message)" }
    if ($r) { $r | Add-Member -NotePropertyName secs -NotePropertyValue ([int]((Get-Date) - $t0).TotalSeconds) -Force }
    return $r
}

$before = Api GET "/api/v1/plan/frontier"
$rulesBefore = @((Api GET "/api/v1/rules").rules | ForEach-Object { "$($_.id)=$($_.active)/$($_.level)" }) -join ','

Sec "1  the goal as it is"
$now = Preview @{}
if (-not $now -or -not $now.ok) { Bad ("preview failed: {0}" -f $now.reason); Stop-Smoke }
Ok ("goal {0} by {1}: {2}% with your rules, {3}% halving ({4}s)" -f $now.goal.target, $now.goal.deadline, $now.headline.p_goal_pct, $now.p_halve_pct, $now.secs)
if ($before.headline -and [math]::Abs([double]$now.headline.p_goal_pct - [double]$before.headline.p_goal_pct) -le 3) { Ok ("matches the stored verdict ({0}%)" -f $before.headline.p_goal_pct) }
elseif ($before.headline) { Bad ("preview {0}% vs stored verdict {1}%" -f $now.headline.p_goal_pct, $before.headline.p_goal_pct) }

Sec "2  the goal as it would be"
$yr = [int]("$($now.goal.deadline)".Substring(0, 4)) + 3
$later = Preview @{ target_date = "$yr" }
$lower = Preview @{ target_roi_pct = 10 }
if ($later.ok -and [double]$later.goal.target -gt [double]$now.goal.target) { Ok ("deadline {0}: goal {1} -> {2}, chance {3}% ({4}s)" -f $yr, $now.goal.target, $later.goal.target, $later.headline.p_goal_pct, $later.secs) } else { Bad "a later deadline did not raise the goal" }
if ($lower.ok -and [double]$lower.goal.target -lt [double]$now.goal.target) { Ok ("10%/yr: goal {0}, chance {1}% ({2}s)" -f $lower.goal.target, $lower.headline.p_goal_pct, $lower.secs) } else { Bad "a lower ROI did not lower the goal" }

Sec "3  rules as they would be"
$rules = @((Api GET "/api/v1/rules").rules)
$guard = $rules | Where-Object { $_.rule_type -eq 'drawdown_guard' -and $_.active } | Select-Object -First 1
if ($guard) {
    $off = Preview @{ armed = @{ "$($guard.id)" = $false } }
    if ($off.ok) { Ok ("guard paused: {0}% chance, {1}% halving (armed: {2}% / {3}%) ({4}s)" -f $off.headline.p_goal_pct, $off.p_halve_pct, $now.headline.p_goal_pct, $now.p_halve_pct, $off.secs) } else { Bad "rules preview failed" }
} else { Skip "no armed drawdown guard to preview without" }

Sec "4  nothing was saved"
$after = Api GET "/api/v1/plan/frontier"
$rulesAfter = @((Api GET "/api/v1/rules").rules | ForEach-Object { "$($_.id)=$($_.active)/$($_.level)" }) -join ','
if ("$($after.computed_at)" -eq "$($before.computed_at)") { Ok "no verdict stored" } else { Bad "a preview stored a verdict" }
if ($rulesAfter -eq $rulesBefore) { Ok "no rule changed" } else { Bad "rules changed during the smoke" }

Stop-Smoke
