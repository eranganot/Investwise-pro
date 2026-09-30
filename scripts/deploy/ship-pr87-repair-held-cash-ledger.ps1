# ship-pr87-repair-held-cash-ledger.ps1
# ship 2026-09-30 - PR 87: no Today card may spend the cash kept for a
# buy-back (Add to BND 841 took the 553 SOXL reserve), no overshoot sized as
# 'toward target', and a repair for the three held-cash buys (VTI 620,
# USO 270, BND 270) that PR 83 put in the book but never in the ledger. Adds a
# preview/apply endpoint and scripts/repair/repair-pr86-held-cash-ledger.ps1.
# This script ships and waits for the deploy only; it WRITES NO DATA. Run the
# repair script after it, then the smoke test. Built on PR 86.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 87: no card spends a buy-back reserve; repair the missing ledger rows'

$landed = ((git log --oneline -1 --format=%s).Trim() -eq $subject)
if ($landed) {
    Write-Host 'PR 87 already landed - nothing to ship.' -ForegroundColor Yellow
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
    if ($headSubject -notlike 'PR 86:*') {
        Write-Host "  HEAD is '$headSubject'; the patch was built on PR 86." -ForegroundColor Yellow
        Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
    }

    Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
    $patch = Join-Path $repo 'pr87-repair-held-cash-ledger.patch'
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
        if (-not $forwardOk) { throw 'the PR 87 patch does not apply cleanly - stop here' }
        git apply $patch
        if ($LASTEXITCODE -ne 0) { throw 'the PR 87 patch failed to apply' }
        Write-Host '  applied.' -ForegroundColor Green
    }
    if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 87 ') {
        throw 'STATUS.md has no PR 87 section - the patch did not land fully'
    }

    Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
    python -m pytest -q tests/test_held_cash_ledger_repair.py tests/test_held_cash_has_a_card.py tests/test_funding_service.py
    if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
    python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
    Write-Host '  Green.' -ForegroundColor Green

    Write-Host '=== 4. commit ===' -ForegroundColor Cyan
    $files = @(
        'app/services/ledger_repair.py',
        'app/services/funding_service.py',
        'app/services/recommendations.py',
        'tests/test_funding_service.py',
        'tests/test_held_cash_has_a_card.py',
        'app/api/routes/intake.py',
        'tests/test_held_cash_ledger_repair.py',
        'scripts/repair/repair-pr86-held-cash-ledger.ps1',
        'scripts/smoke/smoke-pr83-held-cash-and-buy-back-rules.ps1',
        'scripts/deploy/ship-pr87-repair-held-cash-ledger.ps1',
        'STATUS.md'
    )
    $msg = @(
        $subject,
        '',
        'Eran 2026-09-30 00:59: Add to BND 841 took 553.49 from cash - the exact',
        'SOXL reserve he kept. Only the cash card subtracted a hold reserve; it is',
        'now committed to the build''s shared FundingLedger before any card plans.',
        'And the 841: FI gap ~80 (< min) sized to the name''s whole room but paid',
        'from cash + USO (no swap), 14.3% -> 17.5% labelled toward target. With a',
        'gap under the minimum only a within-class swap passes now. Replayed from',
        'his positions: without the fix, the production card to the shekel.',
        '',
        'Eran 2026-09-30 00:52: add a repair that fix those missing entries.',
        'GET/POST /portfolio/ledger/repair-held-cash write the Buy rows a PR 83',
        'held-cash accept left out, dated at the accept (completion mark);',
        'shares = amount / (quote x FX), the accept''s own arithmetic, from the',
        'position''s stored quote when it predates the accept. Idempotent; never',
        'touches positions or cash. The repair script refuses unless BND before =',
        '13.8723, the ledger''s own 07:42 basket buy.',
        '',
        'Sandbox gate: 2112 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Write-Host "PR 87 is live as $sha. Next, the ledger repair (it asks before writing), then the smoke:" -ForegroundColor Green
Write-Host "  .\scripts\repair\repair-pr86-held-cash-ledger.ps1" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1 -Sha $sha" -ForegroundColor Green
