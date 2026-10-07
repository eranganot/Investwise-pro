# ship-pr81-advanced-rules.ps1
# ship 2026-09-29 - PR 81: four advanced trading rules (rebalance band,
# profit-protect trail, trend-break exit, whole-book drawdown guard), tuning
# cards, and the one-time caps-to-bands card. Needs PR 80 (schema 0025) LIVE:
# the script refuses to push if /health does not report 0025_rule_params.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 81: advanced trading rules - band, profit-protect, trend break, drawdown guard'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 81 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 0. PR 80 schema must be live ===' -ForegroundColor Cyan
$hb0 = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 60
if ($hb0.schema_revision -ne '0025_rule_params') { throw 'production is not on schema 0025 - ship PR 80 first' }
Write-Host "  schema $($hb0.schema_revision)" -ForegroundColor Green

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }
$headSubject = (git log -1 --format=%s).Trim()
if ($headSubject -notlike 'PR 80:*') {
    Write-Host "  HEAD is '$headSubject'; the patch was built on PR 80." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr81-advanced-rules.patch'
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
    if (-not $forwardOk) { throw 'the PR 81 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 81 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 81 ') {
    throw 'STATUS.md has no PR 81 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_advanced_rules.py tests/test_a_rule_may_not_trim_through_the_plan.py tests/test_cards_know_their_strategy.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/advanced_rules.py',
    'app/services/rules_service.py',
    'app/services/recommendations.py',
    'app/services/contradictions.py',
    'app/services/buyback_hold.py',
    'app/services/holding_review.py',
    'app/api/routes/rules.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'tests/test_advanced_rules.py',
    'tests/test_cards_know_their_strategy.py',
    'PLAN_2026-09-29_advanced_rules.md',
    'scripts/deploy/ship-pr81-advanced-rules.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Eran 2026-09-29: more advanced trading rules; chose all four and decided',
    'params column (PR 80), a one-time caps-to-bands card, an actionable',
    'drawdown de-risk, and tuning cards when a default is wrong for a holding.',
    '',
    'rebalance_band follows the plan target both ways (buy via',
    'propose_funded_buy); profit_protect arms at +20% and trails the peak;',
    'trend_break reads the holding review signals and sells only with a',
    'measured edge, else arms a stop; drawdown_guard is flow-adjusted and',
    'holds re-buys until it re-arms. Standing rules wait for their condition',
    'to clear after being dealt with. A rule card is no longer dropped by the',
    'redeploy card filter (found in test: band buy vanished, rule was healed).',
    '',
    'Sandbox gate: 2091 passed / 0 failed on SQLite AND Postgres 16; ruff clean.',
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
Write-Host "PR 81 pushed as $sha. Tell Claude 'shipped' - it verifies the rules live." -ForegroundColor Green
