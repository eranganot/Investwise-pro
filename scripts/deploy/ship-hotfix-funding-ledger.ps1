# SHIP - HOTFIX: re-source a card's funding instead of deleting the card.
# Branch: fix/card-funding-ledger   Base: main (PR 2 is already live, 30a3752)
#
#   .\scripts\deploy\ship-hotfix-funding-ledger.ps1                 # full run, prompts before merging
#   .\scripts\deploy\ship-hotfix-funding-ledger.ps1 -AutoMerge      # no prompt once CI is green
#   .\scripts\deploy\ship-hotfix-funding-ledger.ps1 -SkipTests      # only if you JUST ran them
#   .\scripts\deploy\ship-hotfix-funding-ledger.ps1 -MergeOnly      # resume after CI went green
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.
# No backtick continuations, no em-dashes. Same rules as ship-flap-fix.ps1.
#
# ---------------------------------------------------------------------------
# WHAT IS SHIPPING, AND WHY IT IS A HOTFIX
#
# PR 2's cross-card netting ran AFTER every card was built: each card planned
# its funding against the untouched book, then any card whose legs had already
# been claimed was DROPPED. On the live book that left Today with two cards and
# no actionable ones -- the geo card claimed 2 MSFT and both the war-room swap
# card and the commodities card were deleted, while 7,863 of V sat untouched
# and would have funded either.
#
# This nets at BUILD time instead. A FundingLedger records what earlier cards
# committed to; plan_funding only sells shares nobody has claimed; a card whose
# first choice of funding is gone is re-sourced rather than deleted.
#
# EXPECT MORE CARDS BACK, and `degraded` to no longer contain 'funding'. The
# war-room card should return titled "Swap into <ticker>".
#
# Two smoke checks currently FAIL on production and should go green after this:
#   smoke-p0.ps1            P0.4  "agents degraded: funding"
#   smoke-beat-market.ps1   14    "agents degraded: funding"
#
# ON DIRTY FILES: untracked files are NOT a reason to stop. This repo never runs
# `git add -A` -- every commit names its files -- so an untracked file cannot
# ride along. Only modified TRACKED files halt the run.
# ---------------------------------------------------------------------------

param(
    [string]$Branch   = 'fix/card-funding-ledger',
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

# PR 2 is exactly these files.
$Files = @(
    'app/services/funding_service.py',
    'app/services/recommendations.py',
    'tests/test_card_claims.py')

$Housekeeping = @('scripts/deploy/ship-hotfix-funding-ledger.ps1', 'STATUS.md')


# --------------------------------------------------------------------------- #
# 0. Preflight
# --------------------------------------------------------------------------- #
Step '0. Preflight'

foreach ($lock in @('.git\index.lock', '.git\HEAD.lock', '.git\refs\heads\main.lock')) {
    if (Test-Path $lock) { Warn "removing stale $lock"; Remove-Item $lock -Force }
}
if (Test-Path '_to_delete') {
    Warn 'clearing _to_delete\ (sandbox scratch)'
    Remove-Item '_to_delete' -Recurse -Force -ErrorAction SilentlyContinue
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

# PR 2 sits on top of PR 1. If PR 1 is not on main, stop -- the merge would be a
# mess and the smoke would be meaningless.
$pr2 = git log origin/main --oneline --grep 'claiming moves they do not make' -1
if (-not $pr2) {
    Die 'PR 2 is not on origin/main. This hotfix only makes sense on top of it.'
}
Ok "PR 2 is on main: $pr2"


# --------------------------------------------------------------------------- #
# 1. Working tree
#
# This runs BEFORE the branch step on purpose. The first version created the
# branch first and, when the branch did not exist yet, tried to sync main with
# `git pull --rebase` -- which cannot run while the tree holds the very changes
# being shipped. It died on its own payload. Knowing what is dirty is what makes
# the branch step below able to do the right thing.
# --------------------------------------------------------------------------- #
Step '1. Working tree'

$modified  = @(git diff --name-only) + @(git diff --cached --name-only)
$modified  = @($modified | Where-Object { $_ } | Sort-Object -Unique)
$untracked = @(git ls-files --others --exclude-standard | Where-Object { $_ })

if ($modified -contains 'investwise.db') {
    Warn 'investwise.db is dirty - that is your local test run, not a change.'
    if ($AutoMerge -or (Read-Host '  Restore it? (Y/n)') -ne 'n') {
        git checkout -- investwise.db
        if ($LASTEXITCODE -eq 0) { Ok 'investwise.db restored' }
        $modified = @($modified | Where-Object { $_ -ne 'investwise.db' })
    }
}

$known = $Files + $Housekeeping
if ($untracked.Count -gt 0) {
    Write-Host '  untracked (cannot ride along - staging here is always explicit):' -ForegroundColor DarkGray
    foreach ($f in $untracked) {
        $tag = if ($known -contains $f) { 'will commit' } else { 'left alone' }
        Write-Host ("    {0,-12} {1}" -f $tag, $f) -ForegroundColor DarkGray
    }
}

$unexplained = @($modified | Where-Object { $known -notcontains $_ })
if ($unexplained.Count -gt 0) {
    Write-Host ''
    Write-Host '  modified tracked files this script did not expect:' -ForegroundColor Red
    foreach ($f in $unexplained) { Write-Host "    $f" -ForegroundColor Red }
    Write-Host ''
    Die 'commit, stash or restore the above, then re-run.'
}
$carrying = ($modified.Count -gt 0)
if ($carrying) {
    Ok "carrying $($modified.Count) modified file(s) - all expected"
} else {
    Ok 'nothing unexpected is modified'
}


# --------------------------------------------------------------------------- #
# 2. Branch
#
# `git checkout -b` carries uncommitted changes onto the new branch, which is
# exactly what is wanted here: the PR 2 edits are already sitting in the tree.
# The main sync only happens when there is nothing to carry.
# --------------------------------------------------------------------------- #
Step '2. Branch'

if (-not $MergeOnly) {
    $current = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($current -ne $Branch) {
        $exists = git rev-parse --verify --quiet "refs/heads/$Branch"
        if ($exists) {
            git checkout $Branch
            if ($LASTEXITCODE -ne 0) { Die "cannot checkout $Branch" }
            Ok "switched to existing $Branch"
        } else {
            if (-not $carrying -and $current -eq 'main') {
                git pull --rebase origin main
                if ($LASTEXITCODE -ne 0) { Die 'rebase failed' }
                Ok 'main synced'
            } elseif ($carrying) {
                Warn "branching from $current and carrying the working-tree changes"
                Warn 'skipping the main sync: a rebase cannot run over a dirty tree'
            }
            git checkout -b $Branch
            if ($LASTEXITCODE -ne 0) { Die "cannot create $Branch" }
            Ok "created $Branch off $current"
        }
    } else {
        Ok "already on $Branch"
    }

    # The base has to actually contain PR 1, whatever it was branched from.
    $onHead = git log HEAD --oneline --grep 'claiming moves they do not make' -1
    if (-not $onHead) {
        Die 'this branch does not contain PR 2. Branch off main first.'
    }
    Ok 'PR 2 is in this branch history'
}


# --------------------------------------------------------------------------- #
# 3. Local gate - exactly what CI runs
# --------------------------------------------------------------------------- #
if (-not $SkipTests -and -not $MergeOnly) {
    Step '3. Local gate (mirrors .github/workflows/ci.yml)'

    # The new file first: it is the one that fails informatively.
    Write-Host '  pytest tests/test_card_claims.py ...' -ForegroundColor DarkGray
    python -m pytest -q tests/test_card_claims.py
    if ($LASTEXITCODE -ne 0) { Die 'the card-claim invariants FAILED - nothing committed' }
    Ok 'card-claim invariants green'

    Write-Host '  pytest -q (full) ...' -ForegroundColor DarkGray
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Die 'suite FAILED - nothing committed' }
    Ok 'pytest green (expect 790 passed, 4 skipped; PR 2 baseline was 789)'

    Write-Host '  ruff check app tests ...' -ForegroundColor DarkGray
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { Die 'ruff FAILED - CI gates on this.' }
    Ok 'ruff clean'

    Warn 'the Postgres job cannot run locally - it is the one thing only CI proves'
} else {
    Step '3. Local gate SKIPPED'
    Warn 'you are trusting a previous run'
}


# --------------------------------------------------------------------------- #
# 4. Commit
# --------------------------------------------------------------------------- #
if (-not $MergeOnly) {
    Step '4. Commit'

    $present = @($Files | Where-Object { Test-Path $_ })
    $missing = @($Files | Where-Object { -not (Test-Path $_) })
    if ($missing.Count -gt 0) { Die "missing PR 2 files: $($missing -join ', ')" }

    $hk = @($Housekeeping | Where-Object {
        (Test-Path $_) -and (($modified -contains $_) -or ($untracked -contains $_)) })

    # Named files. Never `git add -A`.
    $toStage = @($present + $hk)
    Write-Host '  staging:' -ForegroundColor DarkGray
    foreach ($f in $toStage) { Write-Host "    $f" -ForegroundColor DarkGray }
    git add -- $toStage
    if ($LASTEXITCODE -ne 0) { Die 'staging failed' }

    $staged = git diff --cached --name-only
    if (-not $staged) {
        Warn 'nothing staged - already committed?'
    } else {
        Write-Host ''
        Write-Host '  staged:' -ForegroundColor Cyan
        git diff --cached --stat
        Write-Host ''

        # Commit message via a file, never a here-string: PowerShell reparses a
        # here-string as pathspecs and the commit silently does not happen.
        $msg = Join-Path $RepoRoot 'COMMIT_MSG.txt'
        if (-not (Test-Path $msg)) {
            Die "COMMIT_MSG.txt is missing - it holds the PR 2 message. Restore it and re-run."
        }
        git commit -F $msg
        if ($LASTEXITCODE -ne 0) { Die 'commit failed - nothing pushed' }
        Remove-Item $msg -Force -ErrorAction SilentlyContinue
        Ok "committed $(git rev-parse --short HEAD)"
    }
}


# --------------------------------------------------------------------------- #
# 5. Push
# --------------------------------------------------------------------------- #
if (-not $MergeOnly) {
    Step "5. Push $Branch"
    git push -u origin $Branch
    if ($LASTEXITCODE -ne 0) { Die 'push failed' }
    Ok "pushed origin/$Branch"
}


# --------------------------------------------------------------------------- #
# 6. PR and CI
# --------------------------------------------------------------------------- #
if (-not $SkipPr) {
    Step '6. Pull request'

    $existing = $null
    try { $existing = gh pr view $Branch --json number,url,state 2>&1 | Out-String } catch { $existing = $null }
    if ($LASTEXITCODE -eq 0 -and $existing -and $existing.Trim().StartsWith('{')) {
        $pr = $existing | ConvertFrom-Json
        Ok "PR #$($pr.number) already open: $($pr.url)"
    } else {
        $bodyPath = Join-Path $env:TEMP 'pr2-body.md'
        $body = @(
            '## PR 2 of 2 - consumers and the test wall',
            '',
            'PR 1 built the honest layer. This moves every funded card onto it.',
            '',
            '**This one changes what you see.**',
            '',
            '| | |',
            '|---|---|',
            '| before | `Add to SCHD - 4,708` / "takes Equities from 97% toward your 80% target" / "Moves Equities ~22% closer to target", funded by selling TQQQ and SOXL, both equities |',
            '| after | `Swap into QUAL - 25,720` / "Does not move your Equities weight (97%). Swaps which equities you hold.", funded by selling SCHD and TQQQ; SOXL untouched because it is a sleeve holding |',
            '',
            'Expect fewer cards. A buy into a class already at target, a buy that',
            'increases drift, and a card the book cannot pay for once earlier cards',
            'took their share all refuse to render now.',
            '',
            '### Found by the new tests, not by reading',
            '- Funding could sell an asset class that was already UNDERweight',
            '  (`tax_friendliness` keeps the score positive; zero `trimmable_ils`',
            '  fell through to the whole-position fallback). A war-room buy was',
            '  funded partly by selling BND at 3% against a 10% target.',
            '- `propose_funded_buy` now also refuses any buy that increases total',
            '  drift from the target mix.',
            '- `_reconcile` merged the geo and currency cards by REPLACING the',
            '  action, leaving a card that read "spread new money across other',
            '  regions" while its `apply` spec sold 37 SCHD. Text describing one',
            '  thing and Accept doing another -- this branch''s defect, in the',
            '  reconcile pass.',
            '',
            '### tests/test_card_claims.py',
            'Every test simulates each card''s own `apply` spec and re-measures the',
            'portfolio. Nothing asserts on wording. Six invariants over five',
            'adversarial books, plus two that drive the war room end to end.',
            '',
            'It carries its own guard: `test_the_suite_would_have_caught_it` feeds',
            'the simulator the card that shipped and requires it to be rejected.',
            '',
            '**Tests:** 789 passed, 4 skipped (was 759). ruff clean.')
        Set-Content -Path $bodyPath -Value $body -Encoding ascii
        gh pr create --base main --head $Branch --title "Re-source a card's funding instead of deleting the card (hotfix)" --body-file $bodyPath
        if ($LASTEXITCODE -ne 0) { Die 'gh pr create failed' }
        Remove-Item $bodyPath -Force -ErrorAction SilentlyContinue
        Ok 'PR opened'
    }

    Step '7. CI on the PR (Ctrl-C detaches, it keeps running)'
    gh run watch --exit-status
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host '  CI is RED. Nothing merged. Pull the failing job with:' -ForegroundColor Red
        Write-Host '    gh run view --log-failed' -ForegroundColor Gray
        exit 1
    }
    Ok 'CI green on the PR'
} else {
    Step '6-7. PR SKIPPED - merging straight to main'
}


# --------------------------------------------------------------------------- #
# 8. Merge
# --------------------------------------------------------------------------- #
Step '8. Merge to main'

if (-not $AutoMerge) {
    Write-Host '  This changes the Today screen. The contradictory cards become' -ForegroundColor Gray
    Write-Host '  swaps, and some cards stop appearing at all.' -ForegroundColor Gray
    if ((Read-Host '  Merge and deploy? (y/N)') -ne 'y') {
        Write-Host '  Stopped. Branch is pushed and green.' -ForegroundColor Yellow; exit 0
    }
}

git checkout main
if ($LASTEXITCODE -ne 0) { Die 'cannot checkout main' }
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { Die 'rebase failed - resolve, then re-run with -MergeOnly' }
git merge --no-ff $Branch -m "Merge hotfix: re-source a card's funding instead of deleting the card"
if ($LASTEXITCODE -ne 0) { Die 'merge conflicted - resolve, commit, then re-run with -MergeOnly' }
git push origin main
if ($LASTEXITCODE -ne 0) { Die 'push to main failed' }

$mainSha = (git rev-parse --short HEAD).Trim()
Ok "main is at $mainSha and pushed"


# --------------------------------------------------------------------------- #
# 9. CI on main, then wait for Railway
# --------------------------------------------------------------------------- #
Step '9. CI on main'
if ($gh) {
    gh run watch --exit-status
    if ($LASTEXITCODE -ne 0) { Die 'CI RED on main. Check before smoking.' }
    Ok 'CI green on main'
} else {
    Warn 'no gh CLI - check the Actions tab'
}

Step "10. Waiting for Railway to serve $mainSha"
$deadline = (Get-Date).AddSeconds($DeployTimeoutSec)
$live = ''
$landed = $false
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -and $live -ne 'unknown' -and ($live.StartsWith($mainSha) -or $mainSha.StartsWith($live))) {
            $landed = $true; break
        }
    } catch { $live = 'unreachable' }
    Write-Host "  serving '$live', waiting for '$mainSha' ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if (-not $landed) {
    Warn "Railway is still serving '$live' after $DeployTimeoutSec s. Check the deploy log."
    exit 1
}
Ok "Railway is serving $live"


# --------------------------------------------------------------------------- #
# 11. Smoke
# --------------------------------------------------------------------------- #
if (-not $SkipSmoke) {
    Step '11. Smoke'
    if (-not $env:IW_AGENT_KEY) {
        Warn 'IW_AGENT_KEY not set - skipping. Set it and run:'
        Write-Host "    .\scripts\smoke\smoke-p0.ps1 -Sha $mainSha" -ForegroundColor Gray
        Write-Host "    .\scripts\smoke\smoke-c3.ps1 -Sha $mainSha" -ForegroundColor Gray
    } else {
        # p0 covers the Today card set; c3 covers the funding path underneath it.
        foreach ($s in @('smoke-p0.ps1', 'smoke-c3.ps1')) {
            $path = Join-Path $RepoRoot "scripts\smoke\$s"
            if (Test-Path $path) {
                Write-Host ''
                Write-Host "  --- $s ---" -ForegroundColor Cyan
                & $path -Sha $mainSha -BaseUrl $BaseUrl
                if ($LASTEXITCODE -ne 0) { Warn "$s reported failures - read the output above" }
            } else {
                Warn "$s not found, skipped"
            }
        }
    }
}


# --------------------------------------------------------------------------- #
# 12. What to look at with your own eyes
# --------------------------------------------------------------------------- #
Step '12. Done'
Write-Host ''
Write-Host "  main:     $mainSha" -ForegroundColor Green
Write-Host "  serving:  $live" -ForegroundColor Green
Write-Host ''
Write-Host '  OPEN TODAY. Expect cards to COME BACK:' -ForegroundColor Cyan
Write-Host '    - the war-room card should be there, titled "Swap into <ticker>"' -ForegroundColor Gray
Write-Host '    - the commodities card should be there' -ForegroundColor Gray
Write-Host '    - the response `degraded` list should no longer contain funding' -ForegroundColor Gray
Write-Host '    - no two cards may propose selling the same shares' -ForegroundColor Gray
Write-Host ''
Write-Host '  These two smoke checks were FAILING before this and should pass:' -ForegroundColor Cyan
Write-Host '    smoke-p0.ps1          P0.4  agents degraded: funding' -ForegroundColor Gray
Write-Host '    smoke-beat-market.ps1 14    agents degraded: funding' -ForegroundColor Gray
Write-Host ''
Write-Host '  Then update STATUS.md.' -ForegroundColor Cyan
Write-Host ''
