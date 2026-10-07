# ship-pr78-core-realign-pays.ps1
# ship 2026-09-29 - PR 78: realigning the core paid for its buys with cash
# that was never debited (credit_cash(-x) is a no-op). PROVEN on production
# (+3,678.49 ILS phantom on 09-29) and by reproduction (4/4 new tests fail on
# the old code). Run from anywhere; it applies its own patch. Built on 4349e16.
#
# NO MIGRATION. The production cash correction (-3,678.49) was ALREADY applied
# by Claude through the API - this script does not touch your book.
#
# Gate: focused tests + ruff here (the full suite ran in the sandbox on SQLite
# and Postgres 16 - see the numbers printed below). CI runs everything.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 78: realigning the core debits the cash it spends'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 78 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }
$head = (git rev-parse --short HEAD).Trim()
if ($head -ne '4349e16') {
    Write-Host "  HEAD is $head, the patch was built on 4349e16." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr78-core-realign-pays.patch'
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
    if (-not $forwardOk) { throw 'the PR 78 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 78 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 78 ') {
    throw 'STATUS.md has no PR 78 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_realigning_the_core_pays_for_what_it_buys.py tests/test_the_core_card_acts.py tests/test_the_ledger_counts_every_share_once.py tests/test_an_unpriceable_buy_sells_nothing.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green. (Sandbox full suite: see SANDBOX_GATE line in the commit message.)' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/recommendations.py',
    'app/services/intake_service.py',
    'app/services/ledger_service.py',
    'app/api/routes/intake.py',
    'tests/test_realigning_the_core_pays_for_what_it_buys.py',
    'scripts/deploy/ship-pr78-core-realign-pays.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Root cause (proven live + by reproduction): _apply_core_to_basket topped',
    'its buy budget up with credit_cash(-cash_top). credit_cash is a no-op for',
    'amounts <= 0 and the handler checked "is not None", so every cash-funded',
    'realign bought ETFs with money the book never had, and any remainder was',
    'credited twice. Live since PR 39 (4076ef1). 09-29: +3,678.49 ILS of NAV',
    'from one accept with no market open.',
    '',
    'Fix: debit with set_cash(balance - cash_top), clamped to the balance.',
    'credit_cash raises on a negative amount. Realign buys record value_ils',
    'and price in the ledger. New read-only GET /api/v1/portfolio/ledger.',
    'Tests apply the handler to a real book and re-measure NAV and cash.',
    '',
    'Sandbox gate: 2068 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Remove-Item (Join-Path $repo '_ship_tmp') -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "PR 78 pushed as $sha. Tell Claude 'shipped' - it verifies the rest." -ForegroundColor Green
