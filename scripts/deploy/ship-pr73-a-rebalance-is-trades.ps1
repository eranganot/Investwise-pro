# ship-pr73-a-rebalance-is-trades.ps1
# PR 73 - item 2 of PLAN_2026-09-28_six_items.md (plan section 3, B2-B4), approved 16:04.
# Run from anywhere; it applies its own patch. Needs PR 72 (ea8029e) on main.
#
# A REBALANCE WROTE SHARE COUNTS AND NO TRADES. _rebalance_to scaled every
# holding in a class by one factor - no sale, no buy, no tax, no ledger row.
# Live: the 09-26 13:14 accept moved USO/UGA x1.0295 and SOXL/VTI x1.00268;
# QQQ also carries two rebalances from 09-20. That is why "Your position"
# could not be drawn and fell back to the price.
#
#   B2  a rebalance is real Buy/Sell legs, sales taxed (Q3); the card quotes
#       the tax; a guard at Accept turns any unrecorded share change into an
#       Adjust leg + an ERROR log line.
#   B3  the history repair: preview in /portfolio/ledger/repair; writing is
#       a separate step, scripts\ops\repair-ledger-pr73.ps1 (asks y/N).
#   B4  the chart says when it cannot draw your position.
#
# NO MIGRATION. Schema stays 0024.
#
# EXPECTED GATE: sandbox measured 1984 passed / 0 failed on SQLite AND Postgres 16,
# ruff clean. A red suite here is this change's problem.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 73: a rebalance is trades; the history it never wrote, reconstructed'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 73 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne 'ea8029e') {
    Write-Host "  HEAD is $head, the patch was built on ea8029e (PR 72)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr73-a-rebalance-is-trades.patch'
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
    if (-not $forwardOk) { throw 'the PR 73 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 73 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 73 ') {
    throw 'STATUS.md has no PR 73 section - the patch did not land fully'
}
Write-Host '  STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 1984 passed / 0 failed on SQLite and Postgres.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/api/routes/intake.py',
    'app/services/ledger_repair.py',
    'app/services/recommendations.py',
    'app/static_app/index.html',
    'tests/test_a_rebalance_is_real_trades.py',
    'tests/test_unrecorded_rebalances_are_reconstructed.py',
    'tests/test_recos_apply.py',
    'scripts/smoke/smoke-pr73-a-rebalance-is-trades.ps1',
    'scripts/ops/repair-ledger-pr73.ps1',
    'scripts/deploy/ship-pr73-a-rebalance-is-trades.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Item 2 of PLAN_2026-09-28_six_items.md (plan section 3), approved 2026-09-28.',
    '',
    'Root cause, proven: _rebalance_to scaled every holding in a class by one',
    'factor and wrote the counts into the book - no sale, no buy, no tax, no',
    'ledger row. Reproduced (Equities x1.584, FI x0.2475, Commodities x1.1, 0',
    'transactions) and matched live: the 09-26 13:14 accept moved USO and UGA',
    'x1.0295, SOXL and VTI x1.00268. QQQ also carries two 09-20 rebalances.',
    '16 rebalance accepts since 08-21, none recorded - so the position chart',
    'could not rebuild a share count and showed the price instead.',
    '',
    'B2  _rebalance_plan is the one sizing for card and Accept: falling counts',
    '    are Sells taxed via _sale_value_ils (Q3), rises are Buys funded by the',
    '    net proceeds. The card quotes the tax. A guard at the Accept boundary',
    '    turns any share change the ledger does not explain into an Adjust leg',
    '    and an ERROR log, naming the kind and card.',
    'B3  ledger_repair reconstructs each unrecorded rebalance from audit_log:',
    '    one factor per class solved by elimination, confirmed by a second',
    '    holding where one exists; one dated Adjust per holding per step, value',
    '    0. Preview only here; scripts/ops/repair-ledger-pr73.ps1 writes on y.',
    'B4  the chart says when it cannot draw your position, and labels the',
    '    price as the price.',
    '',
    'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_016aSYTmfN1q8K1z2RjiwFEh'
)

if (-not (New-Commit -Files $files -Message $msg)) { throw 'nothing committed' }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  committed $sha" -ForegroundColor Green

Write-Host '=== 6. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

Write-Host ''
Write-Host '=== 7. smoke ===' -ForegroundColor Cyan
# Before this deploy the same script FAILED 4 checks on ea8029e (measured in
# the sandbox) - one per part of the fix. After it, it must pass.
& (Join-Path $repo 'scripts\smoke\smoke-pr73-a-rebalance-is-trades.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 73 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 73 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host 'NEXT, when you are ready: .\scripts\ops\repair-ledger-pr73.ps1' -ForegroundColor Green
Write-Host '  It shows every row it would write and writes only on y.' -ForegroundColor DarkGray
