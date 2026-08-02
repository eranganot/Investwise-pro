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
    python -m ruff check app
    if ($LASTEXITCODE -ne 0) { Write-Host "ruff FAILED - CI gates on this. Not committing." -ForegroundColor Red; exit 1 }
    Write-Host "Green." -ForegroundColor Green
}

function New-Commit {
    param([string[]]$Files, [string[]]$Message)
    $missing = $Files | Where-Object { -not (Test-Path $_) }
    if ($missing) { Write-Host "Missing files: $($missing -join ', ')" -ForegroundColor Red; exit 1 }

    git add -- $Files
    $staged = git diff --cached --name-only
    if (-not $staged) { Write-Host "Nothing staged - already committed?" -ForegroundColor Yellow; return $false }

    Write-Host "`nStaged:" -ForegroundColor Cyan
    git diff --cached --stat

    # Commit message via file, never a here-string.
    $msgPath = Join-Path $RepoRoot "COMMIT_MSG.txt"
    Set-Content -Path $msgPath -Value $Message -Encoding utf8
    git commit -F $msgPath
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
