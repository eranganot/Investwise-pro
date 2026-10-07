# Shared helpers for the phased deploy scripts.
# Dot-source this from each phase script: . "$PSScriptRoot\_common.ps1"
#
# House rules encoded here (see CLAUDE.md / STATUS.md "Known sharp edges"):
#   * never `git add -A`  -- frontend/node_modules is tracked and CRLF-noisy
#   * never a here-string for the commit message -- PowerShell reparses it as
#     pathspecs and the commit silently doesn't happen. Always `-F` a file.
#   * run the suite locally before pushing; CI is the gate, keep it green.

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path

function Enter-Repo {
    Set-Location $RepoRoot
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne "main") {
        Write-Host "You are on '$branch', not 'main'." -ForegroundColor Yellow
        if ((Read-Host "Continue anyway? (y/N)") -ne "y") { exit 1 }
    }
    Write-Host "Syncing with origin..." -ForegroundColor Cyan
    git fetch origin
    $status = git status -sb | Select-Object -First 1
    if ($status -match "behind") {
        Write-Host "Local main is behind origin. Pull/rebase first." -ForegroundColor Red
        Write-Host $status
        exit 1
    }
}

function Invoke-Suite {
    param([string[]]$Focus = @())
    Write-Host "`nRunning tests..." -ForegroundColor Cyan
    if ($Focus.Count -gt 0) {
        Write-Host "  (focused: $($Focus -join ', '))" -ForegroundColor DarkGray
        python -m pytest -q @Focus
        if ($LASTEXITCODE -ne 0) { Write-Host "Focused tests FAILED - not committing." -ForegroundColor Red; exit 1 }
    }
    Write-Host "`nFull suite + lint..." -ForegroundColor Cyan
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Host "Suite FAILED - not committing." -ForegroundColor Red; exit 1 }
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { Write-Host "ruff FAILED - CI gates on this. Not committing." -ForegroundColor Red; exit 1 }
    Write-Host "Green." -ForegroundColor Green
}

function New-Commit {
    param([string[]]$Files, [string[]]$Message)
    $missing = $Files | Where-Object { -not (Test-Path $_) }
    if ($missing) { Write-Host "Missing files: $($missing -join ', ')" -ForegroundColor Red; exit 1 }

    # A MODIFIED TRACKED FILE THAT IS NOT IN $Files IS THIS COMMIT'S BLIND SPOT,
    # AND THE LOCAL GATE CANNOT SEE IT BY CONSTRUCTION.
    #
    # PR 33 rewrote three tests alongside app/services/nav_history.py and its
    # $Files list named only one of them. The gate ran the NEW tests against the
    # NEW code and went green - correctly, because that is the working tree.
    # The commit carried the new code and the OLD tests, so CI ran run #194 and
    # went red on three of them, on both backends. The gate is not at fault: it
    # tests what is on disk, and what was on disk was right.
    #
    # So the check has to live HERE, at the only point where the two diverge.
    # Before the 'git add' below, because after it these files move into the
    # index and drop out of the unstaged diff.
    #
    # The exclusions are the tracked files that are routinely dirty for reasons
    # that have nothing to do with any commit: investwise.db is rewritten by
    # every local pytest run, node_modules is tracked and CRLF-noisy, and
    # COMMIT_MSG.txt is this function's own scratch file.
    $noisy = @("investwise.db", "COMMIT_MSG.txt", "scripts/railway-error.log")
    $left = @(git diff --name-only) | Where-Object {
        $_ -and ($Files -notcontains $_) -and ($noisy -notcontains $_) -and
        ($_ -notlike "frontend/node_modules/*") -and ($_ -notlike ".claude/*")
    }
    if ($left.Count -gt 0) {
        Write-Host ""
        Write-Host "MODIFIED AND NOT IN THIS COMMIT ($($left.Count)):" -ForegroundColor Red
        foreach ($f in $left) { Write-Host "  $f" -ForegroundColor Red }
        Write-Host ""
        Write-Host "  Your local gate tested these files. CI will NOT have them." -ForegroundColor Yellow
        Write-Host "  If any of them is a test this change rewrote, CI runs the OLD" -ForegroundColor Yellow
        Write-Host "  test against the NEW code and goes red. That is CI run #194." -ForegroundColor Yellow
        Write-Host "  Add them to the script's file list, or stash them." -ForegroundColor Yellow
        Write-Host ""
        if ((Read-Host "  Commit WITHOUT them anyway? (y/N)") -ne "y") { exit 1 }
    }

    git add -- $Files

    # PATH-LIMITED, ALL THE WAY THROUGH.
    #
    # This staged $Files and then ran a bare `git commit`, which commits
    # everything in the INDEX -- not just what it was asked to. The two are the
    # same thing only when the index starts clean, and it does not always: an
    # aborted `git stash pop` leaves its files staged, and on 2026-08-25 that
    # was six of Eran's in-progress T5 files sitting staged behind a conflict.
    # Running any ship script at that moment would have committed and PUSHED
    # unfinished work under a message describing something else.
    #
    # The repo's "never git add -A" rule exists to prevent exactly that, and the
    # helper meant to enforce it had a hole underneath. Naming the paths on the
    # commit closes it: whatever else is staged, only $Files is committed.
    $staged = git diff --cached --name-only -- $Files
    if (-not $staged) { Write-Host "Nothing staged - already committed?" -ForegroundColor Yellow; return $false }

    # Anything else sitting in the index is NOT going into this commit. Say so
    # rather than leaving it to be discovered afterwards.
    $all = @(git diff --cached --name-only)
    $others = @($all | Where-Object { $Files -notcontains $_ })
    if ($others.Count -gt 0) {
        Write-Host "`nStaged but NOT part of this commit ($($others.Count)):" -ForegroundColor Yellow
        foreach ($o in $others) { Write-Host "  $o" -ForegroundColor Yellow }
        Write-Host "  (left staged and untouched - path-limited commit)" -ForegroundColor DarkGray
    }

    Write-Host "`nCommitting:" -ForegroundColor Cyan
    git diff --cached --stat -- $Files

    # Commit message via file, never a here-string.
    #
    # WITHOUT A BOM. `Set-Content -Encoding utf8` writes one on Windows
    # PowerShell 5.1, git copies the message through verbatim, and the subject
    # line carries three invisible bytes: `git log --oneline` on 2026-08-25 read
    # "b48b291 <feff>Stop recommending four ETNs...". Harmless until something
    # greps a subject line, at which point it is baffling. UTF8Encoding($false)
    # is the no-BOM constructor and works on both 5.1 and 7.
    $msgPath = Join-Path $RepoRoot "COMMIT_MSG.txt"
    [System.IO.File]::WriteAllLines(
        $msgPath, [string[]]$Message, (New-Object System.Text.UTF8Encoding($false)))
    git commit -F $msgPath -- $Files
    if ($LASTEXITCODE -ne 0) { Write-Host "Commit failed." -ForegroundColor Red; exit 1 }
    Remove-Item $msgPath -ErrorAction SilentlyContinue
    return $true
}

function Push-AndWatch {
    Write-Host "`nPushing to origin/main..." -ForegroundColor Cyan
    git push origin main
    if ($LASTEXITCODE -ne 0) { Write-Host "Push failed." -ForegroundColor Red; exit 1 }
    Write-Host "Pushed. CI is the gate; Railway deploys on green." -ForegroundColor Green
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        Write-Host "`nWatching CI (Ctrl-C to detach)..." -ForegroundColor Cyan
        gh run watch --exit-status
        if ($LASTEXITCODE -ne 0) { Write-Host "CI RED - fix before the next phase." -ForegroundColor Red; exit 1 }
        Write-Host "CI green." -ForegroundColor Green
    } else {
        Write-Host "(gh CLI not found - check Actions in the browser.)" -ForegroundColor DarkGray
    }
}
