# SHIP - PR 1: funded-buy narration becomes structurally honest.
# Branch: fix/card-claim-invariant   Commit: f127d25 (already made by hand)
#
#   .\scripts\deploy\ship-pr1-card-claims.ps1                 # full run, prompts before merging to main
#   .\scripts\deploy\ship-pr1-card-claims.ps1 -AutoMerge      # no prompt once CI is green
#   .\scripts\deploy\ship-pr1-card-claims.ps1 -SkipPr         # merge straight to main, no PR
#   .\scripts\deploy\ship-pr1-card-claims.ps1 -SkipTests      # only if you JUST ran them
#   .\scripts\deploy\ship-pr1-card-claims.ps1 -MergeOnly      # resume after CI went green
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.
# No backtick continuations, no em-dashes. Same rules as ship-flap-fix.ps1.
#
# ---------------------------------------------------------------------------
# WHAT IS SHIPPING, AND WHAT TO EXPECT AFTER IT LANDS
#
# This PR is deliberately BEHAVIOUR-NEUTRAL on the Today screen. The three
# contradictory war-room cards ("buy Equities to move Equities from 97% toward
# an 80% target") are still there and still wrong after this deploys. PR 2 fixes
# them. Do not smoke-test Today expecting a change.
#
# It is NOT behaviour-neutral for SLEEVE FUNDING. Two real changes:
#   1. plan_funding now raises NET proceeds, so the phantom "still leaves N
#      short" line disappears from plans that were actually complete. A sleeve
#      that was previously reported unfundable purely because of the tax
#      artifact will now fund.
#   2. plan_funding spends ONE overweight budget per asset class instead of
#      re-spending the same overweight once per holding. A class 40% over target
#      used to authorise selling 40% of NAV out of EVERY holding in it. Funding
#      plans will now sell LESS, and a large sleeve may be correctly refused
#      where it used to look affordable.
#
# So the smoke that matters here is smoke-c3 (sleeve funding), read-only.
#
# ON DIRTY FILES: untracked files are NOT a reason to stop. This repo never runs
# `git add -A` -- every commit names its files -- so an untracked file cannot
# ride along. Only modified TRACKED files can, and only those halt the run. Two
# known-good items (.gitignore, this script) are committed onto the branch in
# step 2b rather than being something you have to clear out by hand first.
# ---------------------------------------------------------------------------

param(
    [string]$Branch   = 'fix/card-claim-invariant',
    [string]$RepoRoot = 'C:\dev\Investwise-pro',
    [string]$BaseUrl  = 'https://investwise-pro-production.up.railway.app',
    [switch]$SkipTests,
    [switch]$SkipPr,
    [switch]$SkipSmoke,
    [switch]$AutoMerge,
    [switch]$MergeOnly,
    [int]$DeployTimeoutSec = 900
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Step($m) { Write-Host ''; Write-Host "=== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host ''; Write-Host "  STOP  $m" -ForegroundColor Red; exit 1 }

Set-Location $RepoRoot


# --------------------------------------------------------------------------- #
# 0. Preflight
# --------------------------------------------------------------------------- #
Step '0. Preflight'

# The sandbox could not unlink this on the Windows mount, and a failed commit
# leaves the corpse behind. Removing it is safe when no git process is running.
foreach ($lock in @('.git\index.lock', '.git\HEAD.lock', '.git\refs\heads\main.lock')) {
    if (Test-Path $lock) {
        Warn "removing stale $lock"
        Remove-Item $lock -Force
    }
}

# The bridge cannot delete, so it parked its scratch files here. Gitignored, but
# there is no reason to keep them.
if (Test-Path '_to_delete') {
    Warn 'clearing _to_delete\ (sandbox scratch: src.tgz, stale lock corpses)'
    Remove-Item '_to_delete' -Recurse -Force -ErrorAction SilentlyContinue
}

# COMMIT_MSG.txt is a scratch file by house convention (_common.ps1 deletes it
# after committing). If it survived the manual commit, drop it now so it does
# not ride along in a later commit.
if (Test-Path 'COMMIT_MSG.txt') {
    # try/catch, not `2>$null`: under ErrorActionPreference='Stop' a native
    # command writing to a redirected stderr can raise NativeCommandError and
    # abort the script on what is only a "file is untracked" answer.
    $tracked = $null
    try { $tracked = git ls-files --error-unmatch COMMIT_MSG.txt 2>&1 | Out-String } catch { $tracked = $null }
    if ($LASTEXITCODE -ne 0) { $tracked = $null }
    if (-not $tracked) {
        Warn 'removing leftover COMMIT_MSG.txt (untracked scratch)'
        Remove-Item 'COMMIT_MSG.txt' -Force
    } else {
        Warn 'COMMIT_MSG.txt is TRACKED in this repo - leaving it alone'
    }
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Die 'python not on PATH. Activate .venv first: .\.venv\Scripts\Activate.ps1' }
Ok "python: $(python -V)"

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh -and -not $SkipPr) {
    Warn 'gh CLI not found - falling back to -SkipPr (merge straight to main)'
    $SkipPr = $true
}

git fetch origin
if ($LASTEXITCODE -ne 0) { Die 'git fetch failed' }
Ok 'fetched origin'


# --------------------------------------------------------------------------- #
# 1. Get onto the branch, then account for every dirty file
# --------------------------------------------------------------------------- #
Step '1. Branch'

if (-not $MergeOnly) {
    $current = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($current -ne $Branch) {
        Warn "you are on '$current', switching to '$Branch'"
        git checkout $Branch
        if ($LASTEXITCODE -ne 0) { Die "cannot checkout $Branch" }
    }
    $sha = (git rev-parse --short HEAD).Trim()
    $subject = (git log -1 --pretty=%s).Trim()
    Ok "$Branch is at $sha"
    Ok "subject: $subject"
    if ($subject -notmatch 'funded-buy narration') {
        Warn 'HEAD does not look like the PR 1 commit. Check before continuing.'
        if ((Read-Host '  Continue anyway? (y/N)') -ne 'y') { exit 1 }
    }

    # Guard against a half-finished hand commit: PR 1 is exactly these files.
    $expected = @(
        'app/services/funding_service.py',
        'app/services/recommendations.py',
        'app/services/strategy_service.py',
        'tests/test_funding_service.py',
        'tests/test_backlog_fixes.py')
    $touched = @(git show --pretty=format: --name-only HEAD | Where-Object { $_ })
    $missing = @($expected | Where-Object { $touched -notcontains $_ })
    if ($missing.Count -gt 0) {
        Warn "commit does not touch: $($missing -join ', ')"
        if ((Read-Host '  Continue anyway? (y/N)') -ne 'y') { exit 1 }
    } else {
        Ok 'all five PR 1 files are in the commit'
    }
}


# --------------------------------------------------------------------------- #
# 2. Account for the working tree
#
# The first version of this script died on ANY dirty file, which was wrong twice
# over: it stopped on its own untracked self, and it treated untracked files as
# dangerous. They are not. This repo never runs `git add -A` -- every commit
# names its files -- so an untracked file cannot ride along in a commit. Only
# MODIFIED TRACKED files can, and only those are worth stopping for.
# --------------------------------------------------------------------------- #
Step '2. Working tree'

# Housekeeping that legitimately belongs on this branch, rather than being
# something to "sort out" before running.
$Housekeeping = @(
    '.gitignore',                                    # only if genuinely dirty; usually clean
    'scripts/deploy/ship-pr1-card-claims.ps1')       # this script

$modified  = @(git diff --name-only)  + @(git diff --cached --name-only)
$modified  = @($modified | Where-Object { $_ } | Sort-Object -Unique)
$untracked = @(git ls-files --others --exclude-standard | Where-Object { $_ })

# investwise.db is a TRACKED sqlite artifact that local pytest runs dirty. It is
# never a change, always an artifact.
if ($modified -contains 'investwise.db') {
    Warn 'investwise.db is dirty - that is your local test run, not a change.'
    if ($AutoMerge -or (Read-Host '  Restore it? (Y/n)') -ne 'n') {
        git checkout -- investwise.db
        if ($LASTEXITCODE -eq 0) { Ok 'investwise.db restored' }
        $modified = @($modified | Where-Object { $_ -ne 'investwise.db' })
    }
}

if ($untracked.Count -gt 0) {
    Write-Host '  untracked (cannot ride along - staging here is always explicit):' -ForegroundColor DarkGray
    foreach ($f in $untracked) {
        $tag = if ($Housekeeping -contains $f) { 'will commit' } else { 'left alone' }
        Write-Host ("    {0,-12} {1}" -f $tag, $f) -ForegroundColor DarkGray
    }
}

$unexplained = @($modified | Where-Object { $Housekeeping -notcontains $_ })
if ($unexplained.Count -gt 0) {
    Write-Host ''
    Write-Host '  modified tracked files this script did not expect:' -ForegroundColor Red
    foreach ($f in $unexplained) { Write-Host "    $f" -ForegroundColor Red }
    Write-Host ''
    Die 'commit, stash or restore the above, then re-run.'
}
Ok 'nothing unexpected is modified'


# --------------------------------------------------------------------------- #
# 2b. Commit the housekeeping onto the branch
# --------------------------------------------------------------------------- #
$toCommit = @($Housekeeping | Where-Object { (Test-Path $_) -and (($modified -contains $_) -or ($untracked -contains $_)) })
if ($toCommit.Count -gt 0 -and -not $MergeOnly) {
    Step '2b. Housekeeping commit'
    foreach ($f in $toCommit) { Write-Host "    $f" -ForegroundColor Gray }

    $doIt = $true
    if (-not $AutoMerge) { $doIt = ((Read-Host '  Commit these onto the branch? (Y/n)') -ne 'n') }
    if ($doIt) {
        # Named files. Never `git add -A`.
        git add -- $toCommit
        if ($LASTEXITCODE -ne 0) { Die 'staging the housekeeping files failed' }

        # Commit message via a file, never a here-string: PowerShell reparses a
        # here-string as pathspecs and the commit silently does not happen.
        # The message is built from what is ACTUALLY being committed. A fixed
        # message that names a file the commit does not touch is the same class
        # of defect this whole branch exists to fix.
        $hkMsg = Join-Path $env:TEMP 'pr1-housekeeping.txt'
        $what = @()
        if ($toCommit -contains 'scripts/deploy/ship-pr1-card-claims.ps1') {
            $what += 'add the PR 1 ship script'
        }
        if ($toCommit -contains '.gitignore') { $what += 'gitignore _to_delete' }
        $lines = @("chore: $($what -join ', ')", '')
        if ($toCommit -contains 'scripts/deploy/ship-pr1-card-claims.ps1') {
            $lines += @(
                'ship-pr1-card-claims.ps1 is the deploy runner for the funded-buy',
                'narration work: a local gate mirroring ci.yml, merge to main, then',
                'a poll of /health until Railway is actually serving the merged',
                'commit rather than assuming the deploy kept up.',
                '')
        }
        if ($toCommit -contains '.gitignore') {
            $lines += @(
                'Ignores _to_delete/, the staging folder for files the device bridge',
                'cannot delete (it can mv but not rm).',
                '')
        }
        Set-Content -Path $hkMsg -Value $lines -Encoding ascii
        git commit -F $hkMsg
        if ($LASTEXITCODE -ne 0) { Die 'housekeeping commit failed' }
        Remove-Item $hkMsg -Force -ErrorAction SilentlyContinue
        Ok "committed $($toCommit.Count) housekeeping file(s)"
    } else {
        Warn 'skipped - they will not be part of this PR'
    }
}


# --------------------------------------------------------------------------- #
# 2. Local gate - exactly what CI runs
# --------------------------------------------------------------------------- #
if (-not $SkipTests -and -not $MergeOnly) {
    Step '3. Local gate (mirrors .github/workflows/ci.yml)'

    Write-Host '  pytest -q ...' -ForegroundColor DarkGray
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Die 'suite FAILED - nothing pushed' }
    Ok 'pytest green (expect 759 passed; baseline before PR 1 was 731)'

    # CI lints app AND tests. Linting app alone let tests/ quietly accumulate
    # 28 errors nobody saw.
    Write-Host '  ruff check app tests ...' -ForegroundColor DarkGray
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { Die 'ruff FAILED - CI gates on this. Nothing pushed.' }
    Ok 'ruff clean'

    Warn 'the Postgres job cannot run locally - it is the one thing only CI proves'
} else {
    Step '3. Local gate SKIPPED'
    Warn 'you are trusting a previous run'
}


# --------------------------------------------------------------------------- #
# 3. Push the branch
# --------------------------------------------------------------------------- #
if (-not $MergeOnly) {
    Step "4. Push $Branch"
    git push -u origin $Branch
    if ($LASTEXITCODE -ne 0) { Die 'push failed' }
    Ok "pushed origin/$Branch"
}


# --------------------------------------------------------------------------- #
# 4. Open the PR and wait for CI
# --------------------------------------------------------------------------- #
if (-not $SkipPr) {
    Step '5. Pull request'

    $existing = $null
    try { $existing = gh pr view $Branch --json number,url,state 2>&1 | Out-String } catch { $existing = $null }
    if ($LASTEXITCODE -eq 0 -and $existing -and $existing.Trim().StartsWith('{')) {
        $pr = $existing | ConvertFrom-Json
        Ok "PR #$($pr.number) already open: $($pr.url)"
    } else {
        # Body via a file: a here-string on the command line gets reparsed as
        # pathspecs, the same trap that eats commit messages in this repo.
        $bodyPath = Join-Path $env:TEMP 'pr1-body.md'
        $body = @(
            '## PR 1 of 2 - shared layer only',
            '',
            'Three Today cards proposed buying Equities to move Equities from 97%',
            'toward an 80% target, funded by selling Equities. Net class movement',
            'was zero. The same bug was fixed once already in C3, at the CALL SITE,',
            'so `recommendations.py` inherited nothing and shipped it again.',
            'This PR moves the guarantee to the boundary.',
            '',
            '- `describe_funding` requires `buying_class` (no default). Omitting it',
            '  is a TypeError, not a silently missing disclaimer.',
            '- `size_purchase` deleted. Split into `class_gap_ils` (what the plan',
            '  wants) and `name_room_ils` (the cap ceiling) so a ticker weight can',
            '  no longer be passed where a class target belongs.',
            '- `plan_funding` raises NET proceeds. Kills the phantom shortfall.',
            '- `plan_funding` spends ONE overweight budget per class. Found while',
            '  probing: a class 40% over target authorised selling 40% of NAV out',
            '  of every holding in it.',
            '- New `propose_funded_buy()`: sizes, funds, SIMULATES the post-trade',
            '  mix, and derives the impact sentence from the measured before/after.',
            '',
            '`recommendations.py` is behaviour-neutral here on purpose (four sizing',
            'sites rewritten at identical value, three `describe_funding` sites pass',
            'an explicit `None` with a PR2 marker), so the shared layer can be',
            'reviewed before Today output changes.',
            '',
            '**Tests:** 759 passed (was 731; +16 new). Three `test_c3_funding` tests',
            'were passing for the wrong reason and now pass for the right one.',
            '',
            'Plan: `FIX_PLAN_card_claims.md`. PR 2 moves the consumers over and adds',
            '`tests/test_card_claims.py`.')
        Set-Content -Path $bodyPath -Value $body -Encoding ascii

        gh pr create --base main --head $Branch --title 'Make funded-buy narration structurally honest (PR 1: shared layer)' --body-file $bodyPath
        if ($LASTEXITCODE -ne 0) { Die 'gh pr create failed' }
        Remove-Item $bodyPath -Force -ErrorAction SilentlyContinue
        Ok 'PR opened'
    }

    Step '6. CI on the PR (Ctrl-C detaches, it keeps running)'
    gh run watch --exit-status
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host '  CI is RED. Nothing merged. Pull the failing job with:' -ForegroundColor Red
        Write-Host '    gh run view --log-failed' -ForegroundColor Gray
        exit 1
    }
    Ok 'CI green on the PR (pytest sqlite + pytest Postgres + ruff)'
} else {
    Step '5-6. PR SKIPPED - merging straight to main'
}


# --------------------------------------------------------------------------- #
# 6. Merge to main. This is the deploy trigger.
# --------------------------------------------------------------------------- #
Step '7. Merge to main'

if (-not $AutoMerge) {
    Write-Host '  Merging to main pushes to Railway. Today output does NOT change;' -ForegroundColor Gray
    Write-Host '  sleeve funding DOES (see the header of this script).' -ForegroundColor Gray
    if ((Read-Host '  Merge and deploy? (y/N)') -ne 'y') { Write-Host '  Stopped. Branch is pushed and green.' -ForegroundColor Yellow; exit 0 }
}

git checkout main
if ($LASTEXITCODE -ne 0) { Die 'cannot checkout main' }

git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { Die 'rebase failed - resolve, then re-run with -MergeOnly' }

# --no-ff keeps PR 1 legible as one unit in the history, which matters when
# PR 2 lands on top of it and you need to bisect which half moved behaviour.
git merge --no-ff $Branch -m "Merge PR 1: funded-buy narration becomes structurally honest"
if ($LASTEXITCODE -ne 0) { Die 'merge conflicted - resolve, commit, then re-run with -MergeOnly' }

git push origin main
if ($LASTEXITCODE -ne 0) { Die 'push to main failed' }

$mainSha = (git rev-parse --short HEAD).Trim()
Ok "main is at $mainSha and pushed"


# --------------------------------------------------------------------------- #
# 7. CI on main, then wait for Railway to actually be serving it
# --------------------------------------------------------------------------- #
Step '8. CI on main'
if ($gh) {
    gh run watch --exit-status
    if ($LASTEXITCODE -ne 0) { Die 'CI RED on main. Railway may still deploy - check before smoking.' }
    Ok 'CI green on main'
} else {
    Warn 'no gh CLI - check the Actions tab'
}

Step "9. Waiting for Railway to serve $mainSha"
Write-Host '  /health reports the commit actually serving the request, which is' -ForegroundColor DarkGray
Write-Host '  the only honest way to know a deploy landed.' -ForegroundColor DarkGray

$deadline = (Get-Date).AddSeconds($DeployTimeoutSec)
$live = ''
$landed = $false
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -and $live -ne 'unknown' -and ($live.StartsWith($mainSha) -or $mainSha.StartsWith($live))) {
            $landed = $true
            break
        }
    } catch {
        $live = 'unreachable'
    }
    Write-Host "  serving '$live', waiting for '$mainSha' ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}

if (-not $landed) {
    Warn "Railway is still serving '$live' after $DeployTimeoutSec s."
    Warn 'Check the Railway deploy log. Do not smoke until the SHA matches.'
    exit 1
}
Ok "Railway is serving $live"


# --------------------------------------------------------------------------- #
# 9. Smoke the path this PR actually changed
# --------------------------------------------------------------------------- #
if (-not $SkipSmoke) {
    Step '10. Smoke - sleeve funding (read-only)'
    if (-not $env:IW_AGENT_KEY) {
        Warn 'IW_AGENT_KEY not set - skipping. Set it and run:'
        Write-Host "    .\scripts\smoke\smoke-c3.ps1 -Sha $mainSha" -ForegroundColor Gray
    } else {
        # No -AllowExecute. A dry run is the thing worth proving in production.
        & "$RepoRoot\scripts\smoke\smoke-c3.ps1" -Sha $mainSha -BaseUrl $BaseUrl
        if ($LASTEXITCODE -ne 0) { Warn 'smoke-c3 reported failures - read the output above' }
    }
}


# --------------------------------------------------------------------------- #
# 10. What to look at with your own eyes
# --------------------------------------------------------------------------- #
Step '11. Done'
Write-Host ''
Write-Host "  main:     $mainSha" -ForegroundColor Green
Write-Host "  serving:  $live" -ForegroundColor Green
Write-Host ''
Write-Host '  EXPECT NO CHANGE on Today. The three contradictory cards are still' -ForegroundColor Yellow
Write-Host '  there and still wrong. That is PR 2.' -ForegroundColor Yellow
Write-Host ''
Write-Host '  DO check a sleeve funding preview (Plan tab):' -ForegroundColor Cyan
Write-Host '    - a plan that funds should no longer say "still leaves N short"' -ForegroundColor Gray
Write-Host '    - funding plans should sell LESS than before' -ForegroundColor Gray
Write-Host '    - a large sleeve may now be refused that used to look affordable;' -ForegroundColor Gray
Write-Host '      that is the per-class budget working, not a regression' -ForegroundColor Gray
Write-Host ''
Write-Host '  Then update STATUS.md and start PR 2.' -ForegroundColor Cyan
Write-Host ''
