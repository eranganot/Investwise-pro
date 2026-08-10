# Ship P2 (the card: Style + Horizon chips, tab row, risk label).
#     .\scripts\deploy\ship-p2.ps1
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.
# Repo rules: commit with -F COMMIT_MSG.txt (never a here-string), never
# 'git add -A', sync before commit, a push is not a deploy.

param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
Set-Location C:\dev\Investwise-pro

function Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "STOP  $m" -ForegroundColor Red; exit 1 }

Step "0. Sync check"
git fetch origin
git status -sb | Select-Object -First 1
$behind = git rev-list --count HEAD..origin/main
if ([int]$behind -gt 0) { Die "$behind commit(s) behind origin/main. Reconcile first." }
Write-Host "  in sync with origin/main" -ForegroundColor Green

if (-not $SkipTests) {
    Step "1. Full suite"
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Die "tests are red - do not push" }
    Step "1b. ruff (CI runs exactly: ruff check app)"
    python -m ruff check app
    if ($LASTEXITCODE -ne 0) { Die "ruff is red - CI will fail" }
} else {
    Write-Host "`n  -SkipTests: pushing unverified. CI is now your only gate." -ForegroundColor DarkYellow
}

Step "2. Stage the P2 files (explicitly - never git add -A)"
$files = @(
    'app/services/strategy_catalog.py',
    'app/services/strategy_profile.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_p2_cards.py',
    'scripts/smoke/smoke-p2.ps1',
    'scripts/deploy/ship-p2.ps1',
    'STATUS.md'
)
foreach ($f in $files) {
    if (-not (Test-Path $f)) { Die "expected file missing: $f" }
    git add -- $f
}

Write-Host "`nStaged:" -ForegroundColor Gray
git diff --cached --stat

# Nothing staged means every file already matches HEAD -- almost always because
# a previous run of this script pushed and then consumed COMMIT_MSG.txt. Say so
# here rather than failing two steps later with "COMMIT_MSG.txt is missing",
# which reads like a different problem entirely.
if (-not (git diff --cached --name-only)) {
    Write-Host "`nNothing staged: every P2 file already matches HEAD." -ForegroundColor Yellow
    Write-Host "This almost certainly means P2 is ALREADY PUSHED - check:" -ForegroundColor Yellow
    Write-Host "  git log --oneline -3" -ForegroundColor Gray
    $stillDirty = git diff --name-only
    if ($stillDirty) {
        Write-Host "`nStill uncommitted, but NOT in this phase's file list:" -ForegroundColor Yellow
        $stillDirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
        Write-Host "If any of those belong in a commit, add them to `$files or commit separately." -ForegroundColor Gray
    }
    Die "nothing to commit"
}

Write-Host "`nindex.html should be roughly +12/-3 (CSS + three chips)." -ForegroundColor Yellow
Write-Host "Hundreds of deletions means the mount truncated it: git checkout -- it." -ForegroundColor Yellow
Start-Sleep -Seconds 4

$unstaged = git diff --name-only
if ($unstaged) {
    Write-Host "`nNOTE - modified but NOT staged:" -ForegroundColor DarkYellow
    $unstaged | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

Step "3. Commit (-F, never a here-string)"
if (-not (Test-Path 'COMMIT_MSG.txt')) {
    Write-Host "  This script consumes the message file on a successful push." -ForegroundColor Yellow
    Die "COMMIT_MSG.txt is missing. Write one describing THIS commit, then re-run."
}
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { Die "commit failed" }
git log --oneline -1

Step "4. Push to main"
git push origin main
if ($LASTEXITCODE -ne 0) { Die "push failed" }
Remove-Item COMMIT_MSG.txt -ErrorAction SilentlyContinue

$sha = (git rev-parse --short HEAD)
Write-Host "`nPushed $sha." -ForegroundColor Green

Step "5. Verify"
Write-Host "  1) CI green, Railway shows $sha Active" -ForegroundColor Gray
Write-Host "  2) SW iw-v15 -> iw-v16: close and reopen the installed app once," -ForegroundColor Gray
Write-Host "     or you keep the old shell and see none of this." -ForegroundColor Gray
Write-Host "  3) .\scripts\smoke\smoke-p2.ps1" -ForegroundColor Gray
Write-Host "  4) On the phone, Plan tab - the only place the CSS fixes are visible:" -ForegroundColor Gray
Write-Host "     * five goal tabs on ONE line, scrolling sideways" -ForegroundColor Gray
Write-Host "     * 'VERY HIGH RISK' on one line, not two" -ForegroundColor Gray
Write-Host "     * every Beat the Market card shows Style and Horizon chips" -ForegroundColor Gray
