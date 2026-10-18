# Score every sleeve move against your goal now (PR 135a) instead of waiting for the 04:40 job.
# Stores the scores; trades nothing. Takes a few minutes. ASCII ONLY.
#
#   powershell -ExecutionPolicy Bypass -File scripts\refresh-sleeve-moves.ps1

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\smoke\_smoke-common.ps1"
Start-Smoke "sleeve moves refresh"
$r = Api POST "/api/v1/plan/sleeve-moves/refresh" 900
if ($null -eq $r) { Write-Host "refresh did not answer" -ForegroundColor Red; exit 1 }
if (-not $r.ok) { Write-Host ("not scored: {0} {1}" -f $r.reason, $r.detail) -ForegroundColor Yellow; exit 1 }
Write-Host ("your book now: goal chance {0}%, chance of halving {1}%  (scored {2})" -f $r.before.p_goal_pct, $r.before.p_halve_pct, $r.as_of)
$rows = @($r.moves | Where-Object { $_.ok } | Sort-Object { -[double]$_.goal_pts })
foreach ($m in $rows) {
    $what = switch ($m.kind) {
        "hedge"   { "move {0}% of {1} into {2}" -f $m.pct, $m.from, $m.to }
        "remove"  { "remove {0}" -f $m.from }
        "replace" { "replace {0} with {1} (keep {2})" -f $m.from, $m.to, $m.keep }
    }
    Write-Host ("{0,-70} goal {1,6} pts  halving {2,6} pts" -f $what, $m.goal_pts, $m.halve_pts)
}
$bad = @($r.moves | Where-Object { -not $_.ok })
if ($bad.Count) { Write-Host ("{0} moves could not be scored (first: {1})" -f $bad.Count, $bad[0].reason) -ForegroundColor Yellow }
