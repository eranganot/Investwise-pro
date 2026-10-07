# Ship the rule-flap fix (MSFT notification every few hours).
# ASCII only. PowerShell 5.1 safe: no backtick continuations, no em-dashes.
#
# Why you are running this instead of me: the sandbox staged everything but
# cannot commit -- it is unable to unlink .git\index.lock on the Windows mount.
# Removing that lock is safe. No git process is running; it is the corpse of the
# sandbox's failed commit (the commit aborted on an unset user.email, leaving the
# lock behind).
#
# Already verified in the sandbox before handing this over:
#   ruff check app          -> All checks passed
#   pytest (3 chunks)       -> 593 passed, 0 failed
#   git fetch + status -sb  -> main...origin/main, in sync, nothing to reconcile

$ErrorActionPreference = 'Stop'
Set-Location 'C:\dev\Investwise-pro'

# --- 0. clear the stale lock ------------------------------------------------
$lock = '.git\index.lock'
if (Test-Path $lock) {
    Write-Host 'Removing stale .git\index.lock' -ForegroundColor Yellow
    Remove-Item $lock -Force
}

# --- 1. sync check ----------------------------------------------------------
# Cheap, and a previous session was wasted committing onto a stale clone.
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
Write-Host ''
Write-Host '--- branch state ---' -ForegroundColor Cyan
git status -sb

# --- 2. stage ---------------------------------------------------------------
# Named explicitly rather than `git add -A`: investwise.db is a tracked sqlite
# test artifact my local runs dirtied, and .claude/ plus scripts/railway-error.log
# are session junk. A narrow list has dropped real work before, so this is the
# full set and it is checked against `git status` below.
git add STATUS.md
git add app/services/rules_service.py
git add app/services/push_service.py
git add tests/test_p42_notifications.py
git add tests/test_rule_flap.py
git add scripts/smoke/verify-flap-fix.md
if ($LASTEXITCODE -ne 0) { throw 'staging failed' }

Write-Host ''
Write-Host '--- about to commit (M/A in the left column) ---' -ForegroundColor Cyan
git status --short
Write-Host ''

# --- 3. commit --------------------------------------------------------------
git commit -F scripts/commit-flap-fix.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed -- nothing pushed' }
Remove-Item 'scripts\commit-flap-fix.txt' -Force

# --- 4. push ----------------------------------------------------------------
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { throw 'rebase failed -- resolve, then re-run push only' }

git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

Write-Host ''
Write-Host 'Pushed to main.' -ForegroundColor Green
git log --oneline -1

# --- 5. CI is the gate on this repo ----------------------------------------
Write-Host ''
Write-Host 'CI is the deploy gate here, not Railway logs. Watch it:' -ForegroundColor Cyan
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    Write-Host '  gh run watch' -ForegroundColor Gray
    Write-Host ''
    Write-Host 'Current runs:' -ForegroundColor Gray
    gh run list --limit 3
} else {
    Write-Host '  no gh CLI on PATH - open the Actions tab instead' -ForegroundColor Gray
}
Write-Host ''
Write-Host 'Expect: pytest (sqlite), pytest (Postgres), ruff check app - all green.' -ForegroundColor Gray
Write-Host 'Then verify on the Pixel: scripts\smoke\verify-flap-fix.md' -ForegroundColor Green
