# SHIP - Phase T3: what the target costs.
#
#   .\scripts\ship-t3.ps1              # verify only
#   .\scripts\ship-t3.ps1 -Ship        # verify, then commit + push if green
#
# T3 attaches a cost block to a solve: drawdown in shekels with its recovery
# figure, the distribution with the median beside the mean, the tax to stay, and
# the funding cost to arrive. It writes nothing.
#
# After CI is green, run the deployment half:
#   .\scripts\smoke\smoke-t3.ps1
# which validates T0..T3 against the running app and proves the solve does not
# move the book.

param([switch]$Ship)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$Py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$results = [ordered]@{}
function Step([string]$name, [scriptblock]$body) {
    Write-Host "`n--- $name" -ForegroundColor Cyan
    try {
        & $body
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
        $script:results[$name] = "PASS"
        Write-Host "    PASS" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL"
        Write-Host "    FAIL: $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

# The conversion is the one that would be wrong silently: a measured CAGR fed in
# as the engine's drift shifts every percentile down by the volatility drag, and
# nothing on the card would look broken.
Step "the measured CAGR anchors the MEDIAN, not the mean" {
    & $Py -m pytest tests/test_target_cost.py -q --no-header -p no:randomly `
        -k "anchors_the_median or volatility or zero_volatility"
}

Step "T3 tests" { & $Py -m pytest tests/test_target_cost.py -q --no-header -p no:randomly }

Step "no skips in the T3 file" {
    $out = & $Py -m pytest tests/test_target_cost.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") { throw "$($Matches[1]) skipped -- a skip is not a pass" }
    $global:LASTEXITCODE = 0
}

Step "T0..T2 still green" {
    & $Py -m pytest tests/test_blend.py tests/test_target_solver.py `
        tests/test_t0_benchmarks_and_windows.py tests/test_performance_fx.py `
        -q --no-header -p no:randomly
}

Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

Write-Host "`n================ PHASE T3 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-48} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

Write-Host "`n--- committing T3" -ForegroundColor Cyan
$files = @("app/services/target_solver.py", "tests/test_target_cost.py",
           "scripts/smoke/smoke-t3.ps1")
git add $files
git diff --cached --name-only

$msg = @'
feat(t3): what the target costs -- drawdown in shekels, median beside mean, tax

A solve now carries a cost block. A required sleeve size on its own is an
incomplete answer.

Drawdown: the measured blended figure, the same fall in shekels at current
NAV, and the recovery it implies. Down 45% needs +82%, not +45%. A
drawdown reported alone invites the reader to net it against the return
above it, which is arithmetic that does not work.

The distribution, not the point. SimulationEngine already returns real and
nominal bands with a mean; this seeds it from the MEASURED blended CAGR and
volatility and prints the median beside the mean. On a 45% volatility blend
over ten years the median lands about 64% below the mean -- which is the
honest version of "a target expressed as an average is not the outcome you
are most likely to get". Real terms lead, CPI-deflated, assumption stated.
Seeded, so a projection does not move between refreshes.

The subtle part, and the one that would have been wrong silently:
geometric_to_arithmetic_pct. The engine draws exp((mu - sigma^2/2)T + ...),
so mu is the arithmetic drift and the MEDIAN lands at exp((mu -
sigma^2/2)T). A measured CAGR is a geometric return -- it is what the path
compounded at -- so it belongs on the median. Passing it in as mu directly
would shift every percentile down by the volatility drag, by more the more
volatile the blend, and nothing on the card would look broken. Converted
explicitly, so the median projects at the rate that was measured and the
mean sits above it by exactly the drag.

Tax, both halves: the drag to STAY in it comes from the blend
(gross_cagr_pct minus net); the cost to ARRIVE comes from the same
plan_funding the sleeve path calls, with the same sleeve exclusion, rather
than a second implementation of the trim ranking.

Plus scripts/smoke/smoke-t3.ps1 -- the deployment half, covering T0 through
T3 against the running app, since T0..T2 shipped without smokes of their
own. Entirely read-only, and it PROVES the solve is read-only rather than
trusting it: sleeves, positions and cash are fingerprinted either side of a
solve and compared. Also checks that a target without a ceiling is refused,
that a tighter ceiling never returns a larger sleeve, that the reported
point was measured in full rather than from the light sweep, and that the
median sits below the mean.
'@

$mf = Join-Path $env:TEMP ("iw_t3_msg_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. Now the deployment half:" -ForegroundColor Green
Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor DarkGray
Write-Host "  .\scripts\smoke\smoke-t3.ps1" -ForegroundColor DarkGray
