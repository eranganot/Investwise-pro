# Ship the P0 safety batch.
#     .\scripts\deploy\ship-p0.ps1
#
# Stops at the first real problem rather than pushing through it. Nothing here
# is clever - the value is that it refuses to skip a step.
#
# ASCII ONLY. PowerShell 5.1 reads .ps1 as Windows-1252 unless the file has a
# BOM, so a UTF-8 em-dash in a comment becomes three junk bytes and the parser
# dies on a line that looks perfectly fine. That is what happened to the first
# version of this script.
#
# Repo rules baked in, each learned the hard way:
#   * commit with -F COMMIT_MSG.txt, NEVER a PowerShell here-string (it parses
#     as pathspecs and the commit silently does not happen)
#   * never 'git add -A' - frontend/node_modules is tracked and CRLF-noisy
#   * sync before commit - work may already be on origin from another machine
#   * a push is not a deploy

param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
Set-Location C:\dev\Investwise-pro

function Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "STOP  $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 0. sync
Step "0. Sync check - is this branch behind origin?"
git fetch origin
git status -sb | Select-Object -First 1
$behind = git rev-list --count HEAD..origin/main
if ([int]$behind -gt 0) {
    Die "$behind commit(s) behind origin/main. Reconcile first - the change may already be there."
}
Write-Host "  in sync with origin/main" -ForegroundColor Green

# ------------------------------------------------------------- 1. verify
if (-not $SkipTests) {
    Step "1. Full suite (CI gates on this)"
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Die "tests are red - do not push" }

    Step "1b. ruff (CI runs exactly: ruff check app)"
    python -m ruff check app
    if ($LASTEXITCODE -ne 0) { Die "ruff is red - CI will fail" }
} else {
    Write-Host "`n  -SkipTests: pushing unverified. CI is now your only gate." -ForegroundColor DarkYellow
}

# --------------------------------------------------- 2. stage, explicitly
Step "2. Stage the P0 files (explicitly - never git add -A)"
$files = @(
    'app/services/strategy_service.py',
    'app/api/routes/strategy.py',
    'app/api/routes/intake.py',
    'app/services/pricing_service.py',
    'app/services/recommendations.py',
    'app/providers/live.py',
    'app/schemas/market.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_p0_safety.py',
    'tests/test_p0_today_cards.py',
    'scripts/smoke/smoke-p0.ps1',
    'scripts/deploy/ship-p0.ps1',
    'qa/QA-2026-08-10-p0-safety.md',
    'STATUS.md'
)
foreach ($f in $files) {
    if (-not (Test-Path $f)) { Die "expected file missing: $f" }
    git add -- $f
}

Write-Host "`nStaged:" -ForegroundColor Gray
git diff --cached --stat

# A small change showing a huge deletion means the Windows mount truncated a
# file mid-write (see the safe-windows-edits notes). Look before committing.
Write-Host "`nSuspicious deletion count above? Ctrl+C now." -ForegroundColor Yellow
Write-Host "index.html should be roughly +90/-8, not -1600." -ForegroundColor DarkGray
Start-Sleep -Seconds 4

$unstaged = git diff --name-only
if ($unstaged) {
    Write-Host "`nNOTE - modified but NOT staged (left alone deliberately):" -ForegroundColor DarkYellow
    $unstaged | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

# ------------------------------------------------------------- 3. commit
Step "3. Commit (-F, never a here-string)"
if (-not (Test-Path 'COMMIT_MSG.txt')) {
    # This script consumes the message file on a successful push, so a missing
    # one usually means the previous run WORKED and this is a second commit.
    Write-Host "  Check 'git log --oneline -3' - the earlier batch may already be pushed." -ForegroundColor Yellow
    Die "COMMIT_MSG.txt is missing. Write one describing THIS commit, then re-run."
}
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { Die "commit failed" }
git log --oneline -1

# --------------------------------------------------------------- 4. push
Step "4. Push to main (CI: lint + test + test-postgres)"
git push origin main
if ($LASTEXITCODE -ne 0) { Die "push failed" }

Remove-Item COMMIT_MSG.txt -ErrorAction SilentlyContinue

$sha = (git rev-parse --short HEAD)
Write-Host "`nPushed $sha." -ForegroundColor Green

# ------------------------------------------------------- 5. verify deploy
Step "5. Wait for the deploy, then smoke"
Write-Host "  1) CI green?        lint / test / test-postgres" -ForegroundColor Gray
Write-Host "  2) Railway Active?  the running deploy must be $sha" -ForegroundColor Gray
Write-Host "     Two debugging rounds have been lost to a smoke run against a stale" -ForegroundColor DarkGray
Write-Host "     container. Confirm this BEFORE believing any smoke result." -ForegroundColor DarkGray
Write-Host "  3) Read-only smoke: .\scripts\smoke\smoke-p0.ps1" -ForegroundColor Gray
Write-Host "     Then the write path once you trust it:" -ForegroundColor Gray
Write-Host "                       .\scripts\smoke\smoke-p0.ps1 -Execute" -ForegroundColor Gray
Write-Host ""
Write-Host "  'upstream error' means the container did not boot - read the last" -ForegroundColor DarkGray
Write-Host "  ~20 deploy-log lines for the import traceback:" -ForegroundColor DarkGray
Write-Host "                       .\scripts\get-error-log.ps1" -ForegroundColor DarkGray
