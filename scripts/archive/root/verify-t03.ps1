# VERIFY - T0.3: the performance backfill stops adding shekels to dollars.
#
#   .\scripts\verify-t03.ps1              # verify only (no git, no push)
#   .\scripts\verify-t03.ps1 -Ship        # verify, then commit + push if green
#   .\scripts\verify-t03.ps1 -Fast        # targeted tests + ruff only, skip full suite
#
# Rules carried over, each learned the hard way:
#   * A SKIP is never a PASS -- a skipped regression test is indistinguishable
#     from a broken one, so any skip in the T0.3 file fails this script.
#   * Print the exception TYPE and message on the FIRST failure, not the tail of
#     a 900-line log.
#   * Never ship on a partial pass. -Ship is gated on the FULL suite, not on the
#     targeted file, because the fix changes a signature the endpoint depends on.

param(
    [switch]$Ship,
    [switch]$Fast
)

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

# --- 1. The regression file, on its own, first ------------------------------
# Fast failure with a readable reason beats finding it 700 tests later.
Step "T0.3 regression tests" {
    & $Py -m pytest tests/test_performance_fx.py -q --no-header -p no:randomly
}

# --- 2. A skip here is a failure -------------------------------------------
Step "no skips in the T0.3 file" {
    $out = & $Py -m pytest tests/test_performance_fx.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") {
        throw "$($Matches[1]) skipped -- a skipped regression test proves nothing"
    }
    $global:LASTEXITCODE = 0
}

# --- 3. The endpoint test that used to pass while the bug was live ----------
# It exercises a single-currency book, where the defect is invisible by
# construction. It must still pass: the fix is a no-op on returns for a
# one-currency book, and if it is not, the conversion is wrong.
Step "existing performance tests still green" {
    & $Py -m pytest tests/test_performance.py -q --no-header
}

# --- 4. Full suite ----------------------------------------------------------
if (-not $Fast) {
    Step "full suite" { & $Py -m pytest -q --no-header }
} else {
    $results["full suite"] = "SKIPPED (-Fast)"
    Write-Host "`n--- full suite" -ForegroundColor Cyan
    Write-Host "    SKIPPED -- -Fast was passed. This is NOT a pass." -ForegroundColor Yellow
}

# --- 5. Lint ----------------------------------------------------------------
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ T0.3 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-34} {1}" -f $k, $v) -ForegroundColor $c
}
$failed  = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
$skipped = ($results.Values | Where-Object { $_ -like "SKIPPED*" }).Count
Write-Host ("  ---- {0} pass / {1} fail / {2} skipped" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed, $skipped)

if ($failed -gt 0) {
    Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red
    exit 1
}

if (-not $Ship) {
    Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green
    exit 0
}
if ($skipped -gt 0) {
    Write-Host "`nRefusing to ship with a skipped step. Drop -Fast." -ForegroundColor Yellow
    exit 1
}

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing T0.3" -ForegroundColor Cyan
$files = @(
    "app/services/performance_service.py",
    "app/engines/performance.py",
    "app/static_app/index.html",
    "app/static_app/sw.js",
    "tests/test_performance_fx.py"
)
git add $files
git status --short $files

$msg = @'
fix(performance): FX-normalize the backfill before summing holdings

performance() projected positions to (ticker, quantity), dropping market
and meta -- the only inputs price_currency takes. The worker was not
ignoring FX; it had been handed no way to apply it. On a mixed book the
series added shekels to dollars: reproduced at 1 TASE holding + 10 MSFT,
the start value came out 64.3% low and the excess vs SPY read 8.33%
against a true 12.62%.

The projection exists to keep ORM objects off the worker thread
(MissingGreenlet). The safety fix caused the currency bug.

- market + meta now travel with each holding; per-holding fx_rate(
  price_currency(...)) applied before the sum.
- Rate is today's spot held constant across the window (guarded_fx has no
  historical series). Reported as fx_basis spot -- it removes the
  currency-MIX error, it does not attribute return to currency movement.
- The benchmark is deliberately not converted: under a constant rate a
  conversion cancels in every return ratio. Commented, because that stops
  being true when a historical FX series lands.
- A missing rate on a foreign holding no longer passes as parity --
  degraded fx names the ticker instead of valuing a shekel as a dollar.
- summarize() gained benchmark_cagr_pct / excess_cagr_pct so every excess
  is available on the same basis as the return beside it; the card put a
  total-period excess next to an annualized CAGR.
- sw.js iw-v19 -> iw-v20.

Silent because no test called _performance_from and test_performance.py
uses two NYSE tickers -- a single-currency book, where the defect cannot
appear. tests/test_performance_fx.py asserts on the computed series, and
pins the invariant that a one-currency book's returns are unchanged.

Also carries T0.2's window field on this payload (5 lines, same file):
{start, end, sessions, kind holdings_backfill}. Additive, nothing reads
it yet; its twin lands in backtest_service with the rest of T0.
'@

# -F, never -m: see commit-t0.ps1 for the full write-up. An expandable
# here-string ate every backtick and quote in this message the first time it
# ran, and a message quoting a phrase with spaces in it breaks the commit
# outright.
$mf = Join-Path $env:TEMP ("iw_t03_msg_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nSHIPPED. CI is the gate -- watch it before calling this done." -ForegroundColor Green
Write-Host "  gh run watch    (or the Actions tab)" -ForegroundColor DarkGray
