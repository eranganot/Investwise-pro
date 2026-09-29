# ship-pr86-hold-reserves-real-cash.ps1
# ship 2026-09-30 - PR 86: Eran invested part of the SOXL buy-back reserve
# elsewhere and got "Sell Equities 758" - the book target still parked the
# whole SOXL gap (1,996) in Cash though only 553 was left. A hold now reserves
# only cash that exists. Also: held-cash accepts now reach the trade ledger,
# the share guard and the conservation report (PR 83 skipped all three).
# PROVEN live. Built on PR 85. Ships and waits for the deploy only; the smoke
# test is a separate script.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 86: a buy-back hold reserves only cash that exists'

$landed = ((git log --oneline -1 --format=%s).Trim() -eq $subject)
if ($landed) {
    Write-Host 'PR 86 already landed - nothing to ship.' -ForegroundColor Yellow
    $sha = (git rev-parse --short HEAD).Trim()
} else {
    Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
    Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
    git fetch origin
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
    $sb = (git status -sb | Select-Object -First 1)
    Write-Host "  $sb"
    if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }
    $headSubject = (git log -1 --format=%s).Trim()
    if ($headSubject -notlike 'PR 85:*') {
        Write-Host "  HEAD is '$headSubject'; the patch was built on PR 85." -ForegroundColor Yellow
        Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
    }

    Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
    $patch = Join-Path $repo 'pr86-hold-reserves-real-cash.patch'
    if (-not (Test-Path $patch)) { throw "patch not found: $patch" }
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    git apply --check --reverse $patch 2>&1 | Out-Null
    $reverseOk = ($LASTEXITCODE -eq 0)
    git apply --check $patch 2>&1 | Out-Null
    $forwardOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $eap
    if ($reverseOk) {
        Write-Host '  already applied in full - skipping.' -ForegroundColor Yellow
    } else {
        if (-not $forwardOk) { throw 'the PR 86 patch does not apply cleanly - stop here' }
        git apply $patch
        if ($LASTEXITCODE -ne 0) { throw 'the PR 86 patch failed to apply' }
        Write-Host '  applied.' -ForegroundColor Green
    }
    if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 86 ') {
        throw 'STATUS.md has no PR 86 section - the patch did not land fully'
    }

    Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
    python -m pytest -q tests/test_held_cash_has_a_card.py tests/test_advanced_rules.py
    if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
    Write-Host '  Green.' -ForegroundColor Green

    Write-Host '=== 4. commit ===' -ForegroundColor Cyan
    $files = @(
        'app/services/recommendations.py',
        'app/services/book_target.py',
        'tests/test_held_cash_has_a_card.py',
        'scripts/smoke/smoke-pr83-held-cash-and-buy-back-rules.ps1',
        'scripts/deploy/ship-pr86-hold-reserves-real-cash.ps1',
        'STATUS.md'
    )
    $msg = @(
        $subject,
        '',
        'Eran 2026-09-30 00:21: invested 1,160 of the 1,713 SOXL reserve elsewhere',
        'and got a Sell Equities 758 card to undo it. Target Cash was 0.64% floor +',
        '8.50% SOXL hold (1,996) with 703 of cash left: Cash -6.14%, Equities',
        '+3.23%. book_target now caps held_back at spendable cash / NAV (cap_held),',
        'as the held-cash card already did; live drifts all under 1.3 points.',
        'Also: held_cash was dispatched before the before-snapshot and returned,',
        'so its buys never reached the ledger (3 buys, 0 rows), the share guard or',
        'the conservation report. Now it runs through the same boundary.',
        '',
        'Sandbox gate: 2106 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
        '',
        'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>',
        'Claude-Session: https://claude.ai/code/session_01Kunu6tAS8L56Fna5wnDrin'
    )
    if (-not (New-Commit -Files $files -Message $msg)) { throw 'nothing committed' }
    $sha = (git rev-parse --short HEAD).Trim()
    Write-Host "  committed $sha" -ForegroundColor Green

    Write-Host '=== 5. push ===' -ForegroundColor Cyan
    git push origin main
    if ($LASTEXITCODE -ne 0) { throw 'push failed' }

    & (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base
    Remove-Item $patch -ErrorAction SilentlyContinue
}


Write-Host ''
Write-Host "PR 86 is live as $sha. Now run the smoke test:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1 -Sha $sha" -ForegroundColor Green
