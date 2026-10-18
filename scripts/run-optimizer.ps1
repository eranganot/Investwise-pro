# Search for the best book the app can measure for your goal now (PR 136a) instead of waiting for 05:10.
# Stores the result; trades nothing. Takes several minutes. ASCII ONLY.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run-optimizer.ps1

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\smoke\_smoke-common.ps1"
Start-Smoke "goal optimiser"
$r = Api POST "/api/v1/plan/optimizer/refresh" 1800
if ($null -eq $r) { Write-Host "the optimiser did not answer" -ForegroundColor Red; exit 1 }
if (-not $r.ok) { Write-Host ("not run: {0} {1}" -f $r.reason, $r.detail) -ForegroundColor Yellow; exit 1 }
Write-Host ("{0}  ({1} candidates measured; halving limit {2}%; smallest step kept {3} pts)" -f `
    $(if ($r.starter) { "STARTER BOOK (empty portfolio)" } else { "your book" }), $r.candidates_tried, $r.halve_limit_pct, $r.min_step_pts)
Write-Host ("today:  goal chance {0}%, chance of halving {1}%" -f $r.before.p_goal_pct, $r.before.p_halve_pct)
if ($r.after) { Write-Host ("best:   goal chance {0}%, chance of halving {1}%" -f $r.after.p_goal_pct, $r.after.p_halve_pct) }
$i = 0
foreach ($s in @($r.steps)) { $i++; Write-Host ("  step {0}: {1} {2}  (+{3} pts -> {4}%)" -f $i, $s.kind, $s.sleeve, $s.goal_pts, $s.after.p_goal_pct) }
if (-not @($r.steps).Count) { Write-Host "  no change raises the goal chance by the minimum step within the halving limit" }
Write-Host ("best sleeves: {0}" -f (($r.best.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', '))
if ($r.protection) { Write-Host ("protection retune on that book: best {0}% goal / {1}% halving; worth changing: {2}" -f $r.protection.best_p_goal_pct, $r.protection.best_p_halve_pct, $r.protection.material) }
if ($r.core) {
    if ($r.core.outcome -eq "SWITCH") { Write-Host ("core on that book: switching to {0} measures better (current {1})" -f $r.core.best_name, $r.core.current) }
    else { Write-Host ("core on that book: keep {0} (no other core measures better by enough to switch)" -f $r.core.current) }
}
