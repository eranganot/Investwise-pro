# SHIP - Phase T0: benchmarks, windows, and the staleness rule.
#
#   .\scripts\ship-t0.ps1                 # verify only
#   .\scripts\ship-t0.ps1 -Ship           # verify, then commit + push if green
#   .\scripts\ship-t0.ps1 -Ship -NoMigrate  # skip the local alembic check (CI still runs it)
#
# T0.3 ships separately via verify-t03.ps1 and should already be on main. This
# script covers T0.1 (two benchmarks), T0.2 (windows) and T0.4 (benchmark_ticker
# + staleness), which is the batch carrying a migration.
#
# Rules carried over:
#   * A SKIP is never a PASS.
#   * The migration is checked BEFORE the suite. A revision that cannot apply is
#     a deploy that dies after the code is already out -- the 0011 shape, where
#     the deploy shipped before `alembic upgrade head` ran and every SELECT hit
#     an undefined column, taking out GET /strategies entirely.
#   * Never ship on a partial pass.

param(
    [switch]$Ship,
    [switch]$NoMigrate
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

# --- 1. The migration, first ------------------------------------------------
if (-not $NoMigrate) {
    Step "alembic upgrade head (throwaway sqlite)" {
        $tmp = Join-Path $env:TEMP ("iw_t0_{0}.db" -f (Get-Random))
        $env:DATABASE_URL = "sqlite+aiosqlite:///$($tmp -replace '\\','/')"
        try {
            & $Py -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed" }
            # Downgrade too: a revision that cannot be undone is a revision you
            # cannot roll back a bad deploy past.
            & $Py -m alembic downgrade -1
            if ($LASTEXITCODE -ne 0) { throw "alembic downgrade -1 failed" }
        } finally {
            Remove-Item $tmp -ErrorAction SilentlyContinue
            Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
        }
        $global:LASTEXITCODE = 0
    }

    Step "0015 is idempotent against create_all" {
        # 0001_initial runs Base.metadata.create_all, so on a fresh DB the column
        # already exists when 0015 runs. This is the exact failure that broke the
        # chain at 0013. Applying head twice must not raise.
        $tmp = Join-Path $env:TEMP ("iw_t0b_{0}.db" -f (Get-Random))
        $env:DATABASE_URL = "sqlite+aiosqlite:///$($tmp -replace '\\','/')"
        try {
            & $Py -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw "first upgrade failed" }
            & $Py -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw "re-running head failed -- 0015 is not guarded" }
        } finally {
            Remove-Item $tmp -ErrorAction SilentlyContinue
            Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
        }
        $global:LASTEXITCODE = 0
    }
} else {
    $results["alembic"] = "SKIPPED (-NoMigrate)"
    Write-Host "`n--- alembic" -ForegroundColor Cyan
    Write-Host "    SKIPPED -- this is NOT a pass." -ForegroundColor Yellow
}

# --- 2. The T0 tests, on their own ------------------------------------------
Step "T0 tests" {
    & $Py -m pytest tests/test_t0_benchmarks_and_windows.py tests/test_performance_fx.py `
        -q --no-header -p no:randomly
}

Step "no skips in the T0 files" {
    $out = & $Py -m pytest tests/test_t0_benchmarks_and_windows.py `
        tests/test_performance_fx.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") { throw "$($Matches[1]) skipped -- a skip is not a pass" }
    $global:LASTEXITCODE = 0
}

# --- 3. The consumers of the engine version ---------------------------------
# ENGINE_VERSION a3 -> a4 marks every stored row stale on purpose. Anything that
# renders a backtest must survive "stale with good numbers" -- P0.2's bug was a
# card showing "Couldn't measure" while holding a kept measurement.
Step "backtest + strategy card tests" {
    & $Py -m pytest -q --no-header -k "backtest or strategy or card_claims"
}

# --- 4. Full suite ----------------------------------------------------------
Step "full suite" { & $Py -m pytest -q --no-header }

# --- 5. Lint ----------------------------------------------------------------
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ PHASE T0 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-38} {1}" -f $k, $v) -ForegroundColor $c
}
$failed  = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
$skipped = ($results.Values | Where-Object { $_ -like "SKIPPED*" }).Count
Write-Host ("  ---- {0} pass / {1} fail / {2} skipped" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed, $skipped)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }
if ($skipped -gt 0) { Write-Host "`nRefusing to ship with a skipped step." -ForegroundColor Yellow; exit 1 }

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing T0" -ForegroundColor Cyan
$files = @(
    "app/engines/strategy_backtest.py",
    "app/services/backtest_service.py",
    "app/services/performance_service.py",
    "app/models/tables.py",
    "alembic/versions/0015_backtest_benchmark_ticker.py",
    "tests/test_t0_benchmarks_and_windows.py"
)
git add $files
git status --short $files

$msg = @'
feat(t0): two benchmarks, provenance on every figure, staleness on benchmark change

Foundation for the target solver. Every figure after this is an excess
measured against a benchmark across a window; settling both AFTER the
engine is built would make every number it produced quietly wrong.

T0.1 -- a strategy is now measured against the thing it levers as well as
against the book benchmark. base_series() builds a buy-and-hold index of
the catalog's own `base` (QQQ for the TQQQ sleeves, SMH for SOXL) and
_base_metrics adds base_cagr_pct / base_max_drawdown_pct /
excess_over_base_cagr_pct as SEPARATE fields from the benchmark ones.
Does the rule work, and does the book beat the market, are different
questions -- a strategy can post a positive excess over its base while the
book still loses to SPY, which is roughly the current picture. A strategy
with no base (the factor stack) reports base_tickers null rather than a
zero, because absent and zero are not the same measurement.

T0.2 -- every payload carries start, end, sessions and kind, kind being
strategy_backtest or holdings_backfill. A ten-year backtest and a 250-day
backfill of today's holdings will disagree and both are correct; a screen
can only say so if each figure carries its own provenance.

T0.4 -- strategy_backtests.benchmark_ticker (migration 0015, guarded
against create_all the way 0013/0014 are). _is_stale invalidates on a
benchmark change, and stale_reason gains a benchmark value -- an excess rendered
under a benchmark it was not measured against is a wrong number, not a
stale one. Empty on pre-a4 rows and deliberately not treated as a
mismatch: those are already stale by version, and guessing a benchmark for
them would invent the fact the column exists to record.

ENGINE_VERSION a3 -> a4: new fields on a stored row, per the rule that got
missed at a2.
'@

# -F, never -m. PowerShell re-parses embedded double quotes when marshalling a
# string to a native exe, so a message quoting a phrase WITH SPACES splits into
# several arguments and git reads the extras as pathspecs. A literal here-string
# (@'...'@) stops $ and backtick expansion; -F keeps the text out of native
# argument parsing entirely. Both are needed.
$mf = Join-Path $env:TEMP ("iw_t0_msg_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. Not done yet -- CI is the gate." -ForegroundColor Green
Write-Host @"

After CI is green, in this order:
  1. Confirm alembic ran on the deploy BEFORE trusting /strategies.
     0011 shipped code ahead of its migration and every SELECT hit an
     undefined column, taking out GET /strategies entirely.
  2. .\scripts\smoke\smoke-beat-market.ps1     # the cards still render
  3. Recompute the backtests: a4 marks every stored row stale on purpose,
     so until the nightly job runs the cards say "stale", not "wrong".
  4. Check one card shows BOTH excesses -- vs its base and vs the book
     benchmark -- and that they are labelled differently.
"@ -ForegroundColor DarkGray
