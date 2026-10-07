# ship-pr85-elsewhere-prefilled.ps1
# ship 2026-09-29 - PR 85: after PR 84 made the touch chance real (92% for
# SOXL), the invest-elsewhere tile subtracted keep = reserved x 92% first and
# pre-filled every amount with 0. Now the chance only picks the recommended
# option; the tile is always pre-filled (plan legs first, then half of the rest
# across the top two ideas). Eran chose the rule. PROVEN live. Built on PR 84.
# Ships and waits for the deploy. The smoke test is a separate script.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 85: the invest-elsewhere tile is always pre-filled'

$landed = ((git log --oneline -1 --format=%s).Trim() -eq $subject)
if ($landed) {
    Write-Host 'PR 85 already landed - nothing to ship.' -ForegroundColor Yellow
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
    if ($headSubject -notlike 'PR 84:*') {
        Write-Host "  HEAD is '$headSubject'; the patch was built on PR 84." -ForegroundColor Yellow
        Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
    }

    Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
    $patch = Join-Path $repo 'pr85-elsewhere-prefilled.patch'
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
        if (-not $forwardOk) { throw 'the PR 85 patch does not apply cleanly - stop here' }
        git apply $patch
        if ($LASTEXITCODE -ne 0) { throw 'the PR 85 patch failed to apply' }
        Write-Host '  applied.' -ForegroundColor Green
    }
    if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 85 ') {
        throw 'STATUS.md has no PR 85 section - the patch did not land fully'
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
        'tests/test_held_cash_has_a_card.py',
        'scripts/smoke/smoke-pr83-held-cash-and-buy-back-rules.ps1',
        'scripts/deploy/ship-pr85-elsewhere-prefilled.ps1',
        'STATUS.md'
    )
    $msg = @(
        $subject,
        '',
        'Eran 2026-09-29 23:42 (PR 84 screenshot, every box 0): I asked you to',
        'suggest the amounts. The split kept reserved x P(touch)=92% first, leaving',
        '130 under the 250 minimum. Now P(touch) only picks the recommended option',
        '(>=50% keep waiting); the tile is pre-filled: plan legs up to their limit,',
        'then half of the rest across the top two ideas by score, rest reserved.',
        'His live card: VTI 620, USO 270, BND 270, keep 553. Smoke fails a tile',
        'with places and no amount (FAILED on live PR 84, as it should).',
        '',
        'Sandbox gate: 2104 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Write-Host "PR 85 is live as $sha. Now run the smoke test:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1 -Sha $sha" -ForegroundColor Green
