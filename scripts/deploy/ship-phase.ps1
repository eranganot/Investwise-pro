# Ship one phase: verify, stage an explicit file list, commit, push, then tell
# you what to check. Replaces ship-p0/p1/p2.ps1, which were three near-identical
# copies of this with a different $files array.
#
#   .\scripts\deploy\ship-phase.ps1 -Phase p3 -Files @(
#       'app/engines/regime.py',
#       'tests/test_p3_regime.py',
#       'STATUS.md')
#
#   ...with the optional extras that were the only other thing that varied:
#
#   .\scripts\deploy\ship-phase.ps1 -Phase p3 -Files @(...) `
#       -Title 'the regime proxy' `
#       -DiffHint 'index.html should be roughly +12/-3 (CSS + three chips).' `
#       -Sw 'iw-v16 -> iw-v17' `
#       -Smoke '.\scripts\smoke\smoke-p3.ps1' `
#       -Then @('On the phone, Plan tab: the gated-vs-ungated table renders.')
#
#   -SkipTests pushes unverified and leaves CI as the only gate.
#   -DryRun    does every check and stages nothing.
#
# Why one script instead of four: an afternoon was lost to a DUPLICATED REPRICE
# LOOP -- one guard, two code paths, and the fix landed on whichever one you
# happened to call. Four near-copies of a ship script is the same shape in the
# tooling: the P2 copy grew a genuinely better "nothing staged" message that
# P0 and P1 never got. Now there is one place to improve.
#
# ASCII ONLY. PowerShell 5.1 reads .ps1 as Windows-1252 unless the file has a
# BOM, so a UTF-8 em-dash in a comment becomes three junk bytes and the parser
# dies on a line that looks perfectly fine.
#
# Repo rules baked in, each learned the hard way:
#   * commit with -F COMMIT_MSG.txt, NEVER a PowerShell here-string (it parses
#     as pathspecs and the commit silently does not happen)
#   * never 'git add -A' - frontend/node_modules is tracked and CRLF-noisy
#   * sync before commit - work may already be on origin from another machine
#   * a push is not a deploy

param(
    [Parameter(Mandatory = $true)][string]$Phase,
    [Parameter(Mandatory = $true)][string[]]$Files,
    [string]$Title = '',
    [string]$DiffHint = '',
    [string]$Sw = '',
    [string]$Smoke = '',
    [string[]]$Then = @(),
    [switch]$SkipTests,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path "$PSScriptRoot\..\..").Path

function Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "STOP  $m" -ForegroundColor Red; exit 1 }

$label = if ($Title) { "$Phase ($Title)" } else { $Phase }
Write-Host "`nShipping $label" -ForegroundColor White

# ---------------------------------------------------------------- 0. sync
Step "0. Sync check - is this branch behind origin?"
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne 'main') {
    Write-Host "  You are on '$branch', not 'main'." -ForegroundColor Yellow
    if ((Read-Host "  Continue anyway? (y/N)") -ne 'y') { exit 1 }
}
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

    Step "1b. ruff (CI runs exactly: ruff check app tests)"
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { Die "ruff is red - CI will fail" }
} elseif ($DryRun) {
    # Do NOT say "pushing unverified" here -- a dry run exits at step 2 and
    # pushes nothing. A message that claims a push happened when it did not is
    # how a smoke run against a stale container starts.
    Step "1. Suite skipped (-SkipTests, and -DryRun pushes nothing anyway)"
} else {
    Step "1. Suite SKIPPED (-SkipTests)"
    Write-Host "  Pushing unverified. CI is now your only gate." -ForegroundColor DarkYellow
}

# --------------------------------------------------- 2. stage, explicitly
Step "2. Stage the $Phase files (explicitly - never git add -A)"
foreach ($f in $Files) {
    if (-not (Test-Path $f)) { Die "expected file missing: $f" }
    if (-not $DryRun) { git add -- $f }
}
if ($DryRun) {
    Write-Host "`n  -DryRun: all $($Files.Count) files exist. Nothing staged." -ForegroundColor Yellow
    git status -s
    exit 0
}

# Nothing staged means every file already matches HEAD -- almost always because
# a previous run of this script pushed and then consumed COMMIT_MSG.txt. Say so
# here rather than failing two steps later with "COMMIT_MSG.txt is missing",
# which reads like a different problem entirely.
if (-not (git diff --cached --name-only)) {
    Write-Host "`nNothing staged: every $Phase file already matches HEAD." -ForegroundColor Yellow
    Write-Host "This almost certainly means $Phase is ALREADY PUSHED - check:" -ForegroundColor Yellow
    Write-Host "  git log --oneline -3" -ForegroundColor Gray
    $stillDirty = git diff --name-only
    if ($stillDirty) {
        Write-Host "`nStill uncommitted, but NOT in this phase's file list:" -ForegroundColor Yellow
        $stillDirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
        Write-Host "If any of those belong in this commit, add them to -Files or commit separately." -ForegroundColor Gray
    }
    Die "nothing to commit"
}

Write-Host "`nStaged:" -ForegroundColor Gray
git diff --cached --stat

# A small change showing a huge deletion means the Windows mount truncated a
# file mid-write (see the safe-windows-edits notes). Look before committing.
if ($DiffHint) { Write-Host "`n$DiffHint" -ForegroundColor Yellow }
Write-Host "Hundreds of unexpected deletions = the mount truncated a file mid-write." -ForegroundColor Yellow
Write-Host "If so: git checkout -- <file>, and re-do the edit. Ctrl+C now." -ForegroundColor DarkGray
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
Step "5. Wait for the deploy, then verify"
$n = 1
Write-Host "  $n) CI green?        lint / test / test-postgres" -ForegroundColor Gray; $n++
Write-Host "  $n) Railway Active?  the running deploy must be $sha" -ForegroundColor Gray; $n++
Write-Host "     Two debugging rounds have been lost to a smoke run against a stale" -ForegroundColor DarkGray
Write-Host "     container. Confirm this BEFORE believing any smoke result." -ForegroundColor DarkGray
if ($Sw) {
    Write-Host "  $n) PWA:             SW went $Sw. Close and reopen the" -ForegroundColor Gray
    Write-Host "                      installed app once or you keep the old shell." -ForegroundColor Gray
    $n++
}
if ($Smoke) {
    Write-Host "  $n) Smoke:           $Smoke" -ForegroundColor Gray; $n++
}
foreach ($t in $Then) {
    Write-Host "  $n) $t" -ForegroundColor Gray; $n++
}
Write-Host ""
Write-Host "  'upstream error' means the container did not boot - read the last" -ForegroundColor DarkGray
Write-Host "  ~20 deploy-log lines for the import traceback:" -ForegroundColor DarkGray
Write-Host "                       .\scripts\get-error-log.ps1" -ForegroundColor DarkGray
