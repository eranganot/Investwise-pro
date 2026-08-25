# COMMIT - Phase T0. Recovery path for the ship-t0.ps1 quoting bug.
#
#   .\scripts\commit-t0.ps1          # commit what is already staged
#   .\scripts\commit-t0.ps1 -Push    # commit and push
#
# ship-t0.ps1 ran every check green (803 passed / 4 skipped, ruff clean,
# migrations up and down) and then failed at `git commit -m $msg`. The files
# were left correctly STAGED, so nothing needs re-verifying -- this commits what
# is already there.
#
# The bug: $msg was an EXPANDABLE here-string, @"..."@, containing double quotes.
# PowerShell re-parses embedded quotes when marshalling a string to a native exe,
# so `"Does the rule work?" and "does the book beat the market?" are different`
# became several arguments. git read the extras as pathspecs -- hence
# `pathspec 'the' did not match`, `'rule'`, `'work?'`, `'market? are different'`.
#
# Two things fix it, and both are used here:
#   * a LITERAL here-string, @'...'@ -- no $ expansion, no backtick escapes.
#     (The T0.3 commit did land, but its message lost every backtick and quote
#     to that same expansion. Cosmetic there; fatal here only because the T0
#     text quoted a phrase WITH SPACES in it.)
#   * `git commit -F <file>` instead of -m, so the message never passes through
#     native-argument parsing at all.

param([switch]$Push)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$expected = @(
    "alembic/versions/0015_backtest_benchmark_ticker.py",
    "app/engines/strategy_backtest.py",
    "app/models/tables.py",
    "app/services/backtest_service.py",
    "tests/test_t0_benchmarks_and_windows.py"
)

$staged = @(git diff --cached --name-only)
if (-not $staged) {
    Write-Host "Nothing staged. Re-run ship-t0.ps1 (it stages, then commits)." -ForegroundColor Yellow
    exit 1
}

Write-Host "--- staged" -ForegroundColor Cyan
$staged | ForEach-Object { Write-Host "    $_" }

$unexpected = $staged | Where-Object { $expected -notcontains $_ }
if ($unexpected) {
    # Committing something nobody looked at, under a message describing T0, is
    # the shape of "a commit that claims a change it does not make".
    Write-Host "`nRefusing: files staged that are not part of T0:" -ForegroundColor Red
    $unexpected | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    Write-Host "Unstage them, or commit them separately." -ForegroundColor Red
    exit 1
}
$missing = $expected | Where-Object { $staged -notcontains $_ }
if ($missing) {
    Write-Host "`nWarning: expected T0 files NOT staged:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
}

$msg = @'
feat(t0): two benchmarks, provenance on every figure, staleness on benchmark change

Foundation for the target solver. Every figure after this is an excess
measured against a benchmark across a window; settling either AFTER the
engine is built would make every number it produced quietly wrong.

T0.1 -- a strategy is now measured against the thing it levers as well as
against the book benchmark. base_series() builds a buy-and-hold index of
the catalog own base (QQQ for the TQQQ sleeves, SMH for SOXL) and
_base_metrics adds base_cagr_pct / base_max_drawdown_pct /
excess_over_base_cagr_pct as SEPARATE fields from the benchmark ones.
Does the rule work, and does the book beat the market, are different
questions with different answers: a strategy can post a positive excess
over its base while the book still loses to SPY, which is roughly the
current picture. A strategy with no base (the factor stack) reports
base_tickers null rather than a zero, because absent and zero are not the
same measurement.

T0.2 -- every payload carries start, end, sessions and kind, kind being
strategy_backtest or holdings_backfill. A ten-year backtest and a 250-day
backfill of today holdings will disagree and both are correct; a screen
can only say so if each figure carries its own provenance.

T0.4 -- strategy_backtests.benchmark_ticker (migration 0015, guarded
against create_all the way 0013 and 0014 are). _is_stale invalidates on a
benchmark change and stale_reason gains a benchmark value -- an excess
rendered under a benchmark it was not measured against is a wrong number,
not a stale one. Empty on pre-a4 rows and deliberately not treated as a
mismatch: those are already stale by version, and guessing a benchmark for
them would invent the fact the column exists to record.

ENGINE_VERSION a3 -> a4: new fields on a stored row, per the rule that got
missed at a2. Every stored backtest is stale until the nightly job reruns;
cards read stale, not wrong.

Verified: alembic up + down + re-upgrade idempotent, 803 passed / 4
skipped, ruff clean on app and tests.
'@

$f = Join-Path $env:TEMP ("iw_t0_msg_{0}.txt" -f (Get-Random))
# UTF-8 with NO BOM: git treats a leading BOM as part of the subject line.
[System.IO.File]::WriteAllText($f, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $f
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally {
    Remove-Item $f -ErrorAction SilentlyContinue
}

Write-Host "`n--- committed" -ForegroundColor Green
git log -1 --format="%h %s"

if (-not $Push) {
    Write-Host "`nRe-run with -Push, or: git push origin (git rev-parse --abbrev-ref HEAD)" -ForegroundColor DarkGray
    exit 0
}

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. CI is the gate." -ForegroundColor Green
Write-Host @'

After CI is green, in this order:
  1. Confirm alembic ran on the deploy BEFORE trusting /strategies. 0011
     shipped code ahead of its migration and every SELECT hit an undefined
     column, taking out GET /strategies entirely.
  2. .\scripts\smoke\smoke-beat-market.ps1
  3. Recompute the backtests -- a4 marks every stored row stale on purpose.
  4. Check one card shows BOTH excesses (vs its base, vs the book
     benchmark) and that they are labelled differently.
'@ -ForegroundColor DarkGray
