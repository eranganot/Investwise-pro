# ship-pr82-rules-layout-drawdown.ps1
# ship 2026-09-29 - PR 82: the Rules tab pushed the page to 489px on a 360px
# phone (the PR 79 edit button), hiding the bottom tabs; and the drawdown
# guard measured 13.3% instead of 5.26% (called without the trade ledger).
# Both PROVEN by reproduction. Built on PR 81.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 82: Rules rows fit a phone; drawdown measured with the trade ledger'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 82 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($headSubject -notlike 'PR 81:*') {
    Write-Host "  HEAD is '$headSubject'; the patch was built on PR 81." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr82-rules-layout-drawdown.patch'
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
    if (-not $forwardOk) { throw 'the PR 82 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 82 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 82 ') {
    throw 'STATUS.md has no PR 82 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_advanced_rules.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/advanced_rules.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_advanced_rules.py',
    'scripts/deploy/ship-pr82-rules-layout-drawdown.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Eran 2026-09-29: layout broken, bottom tabs missing on Rules. Reproduced',
    'at 360px on live data: scrollWidth 489; every overflowing element was a',
    'rule row action group (the PR 79 edit button). Rows are now two lines;',
    'verified 360 and 412: page width == screen, nav visible.',
    '',
    'Drawdown guard fired at 13.3%: book_drawdown called time_weighted without',
    'the trade ledger, so 13 shape-change days (incl. the SOXL rally) were',
    'unmeasured and dropped, and dates shifted. Fixed: 5.26%, peak 09-28.',
    '',
    'Sandbox gate: 2092 passed / 0 failed on SQLite AND Postgres 16; ruff clean; Playwright 360+412px.',
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
Write-Host "PR 82 pushed as $sha. Tell Claude 'shipped' - it verifies the rules live." -ForegroundColor Green
