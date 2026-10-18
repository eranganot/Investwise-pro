# SMOKE - the sleeve plan's PRs not yet checked on production, in one run (PR 134d - 136b).
#
#   powershell -ExecutionPolicy Bypass -File scripts\smoke\smoke-sleeve-plan-all.ps1
#
# Runs each PR's own smoke against the build production serves NOW (read from /health), so every
# "serving <sha>" check is about one build. READ-ONLY: no script here accepts a card or changes data.
# Prints each script's output, then one summary line per PR. Exit code 1 if any PR has a FAIL. ASCII ONLY.

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
$here = $PSScriptRoot
$h = $null
try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Write-Host "/health unreachable" -ForegroundColor Red; exit 1 }
$sha = $h.commit
Write-Host ("production serves {0} (schema {1})" -f $sha, $h.schema_revision) -ForegroundColor White

$runs = @(
    @{ pr = "134d"; file = "smoke-pr134d-more-hedges.ps1";      what = "ten hedges in the catalogue; Quality Momentum 5 Explore only" },
    @{ pr = "135a"; file = "smoke-pr135a-sleeve-moves.ps1";     what = "sleeve moves scored by the goal model" },
    @{ pr = "135b"; file = "smoke-pr135b-sleeve-cards.ps1";     what = "sleeve cards on Today" },
    @{ pr = "135c"; file = "smoke-pr135c-overweight-choice.ps1"; what = "overweight: trim or raise" },
    @{ pr = "136a"; file = "smoke-pr136a-optimizer.ps1";        what = "the goal optimiser" },
    @{ pr = "136b"; file = "smoke-pr136b-optimizer-card.ps1";   what = "the optimiser's card" }
)
$summary = @()
foreach ($r in $runs) {
    Write-Host ""
    Write-Host ("==== PR {0}: {1} ====" -f $r.pr, $r.what) -ForegroundColor Cyan
    $out = & powershell -ExecutionPolicy Bypass -File (Join-Path $here $r.file) -Sha $sha -BaseUrl $BaseUrl 2>&1
    $out | ForEach-Object { Write-Host $_ }
    $line = ($out | Where-Object { "$_" -match "^PASS \d+\s+FAIL \d+\s+SKIP \d+" } | Select-Object -Last 1)
    $summary += [pscustomobject]@{ PR = $r.pr; Result = $(if ($line) { "$line" } else { "no summary (exit $LASTEXITCODE)" }); Code = $LASTEXITCODE }
}
Write-Host ""
Write-Host "==== summary ====" -ForegroundColor White
foreach ($s in $summary) {
    $c = if ($s.Code -ne 0) { "Red" } else { "Green" }
    Write-Host ("PR {0,-5} {1}" -f $s.PR, $s.Result) -ForegroundColor $c
}
if (@($summary | Where-Object { $_.Code -ne 0 }).Count) { exit 1 }
exit 0
