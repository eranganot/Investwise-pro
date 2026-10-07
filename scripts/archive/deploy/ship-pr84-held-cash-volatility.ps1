# ship-pr84-held-cash-volatility.ps1
# ship 2026-09-29 - PR 84: the PR 83 held-cash card split its cash 50/50
# because it read the stored BACKTEST for a volatility the Today review rows
# already carried (live: SOXL vol 115.7%). Plus three honesty fixes seen on the
# same live payload: plan-leg labels, suggestions over a leg's limit, and the
# Rules row calling the plan gap "cash waiting". PROVEN live. Built on PR 83.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 84: the held-cash split reads the hold''s volatility'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 84 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }
$headSubject = (git log -1 --format=%s).Trim()
if ($headSubject -notlike 'PR 83:*') {
    Write-Host "  HEAD is '$headSubject'; the patch was built on PR 83." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr84-held-cash-volatility.patch'
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
    if (-not $forwardOk) { throw 'the PR 84 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 84 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 84 ') {
    throw 'STATUS.md has no PR 84 section - the patch did not land fully'
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
    'app/services/rules_service.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_held_cash_has_a_card.py',
    'scripts/smoke/smoke-pr83-held-cash-and-buy-back-rules.ps1',
    'scripts/deploy/ship-pr84-held-cash-volatility.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'PR 83 live: the held-cash card said no volatility measurement for SOXL',
    'while the same payload had holding_review SOXL vol_20d_pct 115.7. It read',
    'holding_review.stored() (the stored backtest, no metrics). Now reads the',
    'review rows built in the same request. Also: plan legs labelled under',
    'target only when the class is under; suggestions rounded down so none',
    'exceeds its limit; the Rules row says plan wants X more, not cash waiting.',
    'Adds the PR 83 smoke script (24 pass / 0 fail on d099406).',
    '',
    'Sandbox gate: 2102 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Write-Host ''
Write-Host "PR 84 pushed as $sha. Tell Claude 'shipped' - it runs the smoke." -ForegroundColor Green
