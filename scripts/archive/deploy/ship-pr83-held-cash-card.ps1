# ship-pr83-held-cash-card.ps1
# ship 2026-09-29 - PR 83: cash above the 1,500 trigger that a SOXL buy-back
# hold was keeping back produced NO card (reserved all spendable cash,
# returned nothing). Now a card: keep waiting / buy back now / invest elsewhere
# (top-5 places with reasons, a suggested split, invest some and hold some).
# Buy-back holds become visible, editable buy_back rules (fire below the level).
# PROVEN from the live payload. Built on PR 82.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 83: a choice card for cash a buy-back hold keeps back'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 83 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($headSubject -notlike 'PR 82:*') {
    Write-Host "  HEAD is '$headSubject'; the patch was built on PR 82." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr83-held-cash-card.patch'
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
    if (-not $forwardOk) { throw 'the PR 83 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 83 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 83 ') {
    throw 'STATUS.md has no PR 83 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_held_cash_has_a_card.py tests/test_advanced_rules.py tests/test_a_take_profit_sale_keeps_the_sleeve.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/recommendations.py',
    'app/services/buyback_hold.py',
    'app/services/advanced_rules.py',
    'app/services/rules_service.py',
    'app/api/routes/recommendations.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_held_cash_has_a_card.py',
    'tests/test_a_take_profit_sale_keeps_the_sleeve.py',
    'scripts/deploy/ship-pr83-held-cash-card.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Eran 2026-09-29: more than 1500 in cash, no card. Live payload: held_back',
    'SOXL 0.0837 (the buy-back hold from his rule-vs-sleeve SELL at 142.29,',
    'SOXL 147.86) reserved all 1,713 of spendable cash; _redeploy_cash_recs',
    'returned [] silently. Now a held_cash decision card: keep waiting / buy',
    'back now (ends the hold) / invest elsewhere: under-target classes + top 5',
    'ideas with reasons, a suggested split (keep = P(touch level) x reserved),',
    'editable amounts; only the set amounts are bought, the rest stays held.',
    'Holds are now buy_back rules (visible, editable, fire below the level with',
    'a push + buy-back card); the old KV blob migrates on first read.',
    '',
    'Sandbox gate: 2099 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Write-Host "PR 83 pushed as $sha. Tell Claude 'shipped' - it verifies the rules live." -ForegroundColor Green
