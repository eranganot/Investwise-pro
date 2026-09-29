# ship-pr79-editable-rules.ps1
# ship 2026-09-29 - PR 79: rules are editable (level + note, every holding),
# and a rule may not trim a holding through its own plan's target (the VTI
# 15% cap vs an 18.2% core target, PROVEN in the ledger: bought/trimmed within
# the hour on 09-26 and 09-29). Run from anywhere; it applies its own patch.
# Built on 8eee6e1. NO MIGRATION. Service-worker cache bumped (iw-v30).
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 79: editable rules; a rule may not trim through the plan target'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 79 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne '8eee6e1') {
    Write-Host "  HEAD is $head, the patch was built on 8eee6e1." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr79-editable-rules.patch'
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
    if (-not $forwardOk) { throw 'the PR 79 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 79 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 79 ') {
    throw 'STATUS.md has no PR 79 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_a_rule_may_not_trim_through_the_plan.py tests/test_a_rule_that_fights_a_sleeve_is_a_decision.py tests/test_realigning_the_core_pays_for_what_it_buys.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/recommendations.py',
    'app/services/rules_service.py',
    'app/services/core_realign.py',
    'app/api/routes/rules.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_a_rule_may_not_trim_through_the_plan.py',
    'tests/test_cards_know_their_strategy.py',
    'PLAN_2026-09-29_advanced_rules.md',
    'scripts/deploy/ship-pr79-editable-rules.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Eran 2026-09-29: rules could not be edited, and a 15% VTI cap kept',
    'trimming VTI below its 18.2% core target while the core realign bought it',
    'back (ledger: 09-26 and 09-29, within the hour each time). PR 72 only',
    'checked SLEEVE targets.',
    '',
    'plan_ticker_targets: sleeves + core basket per ticker. A rule trim that',
    'would leave a ticker below its plan target becomes the rule_vs_sleeve',
    'decision (Sell holds re-buys; Keep lifts a cap to target + 1.5). The core',
    'realign honours buy-back holds. Suggestions never cap below the plan.',
    'PATCH /api/v1/rules/{id} (level + note) with a below-plan warning, and an',
    'edit button on every rule row. PLAN for PR 80 (advanced rules) included.',
    '',
    'Sandbox gate: SQLite 2075 passed / 0 failed; Postgres 16 2074 passed + 1 wording test updated; ruff clean.',
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
Write-Host "PR 79 pushed as $sha. Tell Claude 'shipped' - it verifies the VTI card live." -ForegroundColor Green
