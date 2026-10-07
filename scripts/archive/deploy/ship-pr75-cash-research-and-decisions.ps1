# ship-pr75-cash-research-and-decisions.ps1
# PR 75 - plan item 3 (PR D) plus Eran's 2026-09-28 evening items.
# Run from anywhere; it applies its own patch. Needs PR 74 on main (042d160).
#
#   * Sell on a rule-vs-sleeve card keeps the sleeve target; a buy-back hold
#     stops every Today card re-buying the ticker until it trades below the
#     sale price.
#   * The decision card recommends Keep or Sell from the sleeve's own signal
#     (Sell when there is none); highlighted, still a click.
#   * Cash above your trigger is researched: the held name vs up to 3 screener
#     names per class, a 10-point margin, same factors, no confirmed
#     downtrends; "also considered" on the card. ?simulate_cash= previews it.
#   * The holding popup and the decision card are redesigned.
#
# NO MIGRATION. Schema stays 0024.
#
# EXPECTED GATE: sandbox measured 2038 passed / 0 failed on SQLite AND Postgres 16, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 75: cash is researched; a take-profit sale keeps the sleeve; the decision card recommends'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 75 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne '042d160') {
    Write-Host "  HEAD is $head, the patch was built on 042d160 (PR 74)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr75-cash-research-and-decisions.patch'
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
    if (-not $forwardOk) { throw 'the PR 75 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 75 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 75 ') {
    throw 'STATUS.md has no PR 75 section - the patch did not land fully'
}
Write-Host '  STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 2038 passed / 0 failed on SQLite and Postgres 16.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/buyback_hold.py',
    'app/services/redeploy_research.py',
    'app/services/book_target.py',
    'app/services/recommendations.py',
    'app/services/sleeve_service.py',
    'app/services/strategy_service.py',
    'app/api/routes/recommendations.py',
    'app/static_app/index.html',
    'tests/_redeploy_fixtures.py',
    'tests/test_a_take_profit_sale_keeps_the_sleeve.py',
    'tests/test_redeploy_new_name_needs_margin.py',
    'tests/test_redeploy_never_swaps_a_sleeve_ticker.py',
    'tests/test_redeploy_prefers_gap_then_score.py',
    'tests/test_redeploy_never_breaches_floor_or_caps.py',
    'tests/test_a_rule_that_fights_a_sleeve_is_a_decision.py',
    'tests/test_history_is_read_only.py',
    'scripts/smoke/smoke-pr75-cash-research-and-decisions.ps1',
    'scripts/deploy/ship-pr75-cash-research-and-decisions.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Plan item 3 (PR D, approved 2026-09-28) and Eran''s evening items.',
    '',
    'Sell on a rule-vs-sleeve card no longer resizes the sleeve (Eran: "it',
    'should not change the goal of the sleeve, only the current holding").',
    'buyback_hold records the sale price; while the price is at or above it',
    'the sleeve counts as met at its current weight, the shortfall is parked',
    'in the Cash target and reserved by the cash card, and no Today card or',
    'Today Accept buys the ticker back. The first price below releases it.',
    '',
    'The decision card recommends from the sleeve''s stored signal (in: Keep,',
    'out/stale/none: Sell); highlighted, still a click. Two option tiles.',
    '',
    'Cash research: each core leg vs up to 3 screener names in its class;',
    'swap past a versioned 10-point margin with the same factors only; no',
    'confirmed downtrends; also-considered and pins on the card.',
    '?simulate_cash= is a read-only preview.',
    '',
    'Popups are height-capped and scroll inside; the chart has a fixed box.',
    '',
    'Review: 8 findings fixed, incl. the cash card re-buying the sold ticker',
    'and an uncommitted hold release (see STATUS.md PR 75).',
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
# On the PR 74 code the same script FAILED 4 checks (local server); on PR 75: 0.
# After this deploy it must pass. Section 3 is a read-only ?simulate_cash preview.
& (Join-Path $repo 'scripts\smoke\smoke-pr75-cash-research-and-decisions.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 75 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 75 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host '  Your cash is under the 1,500 trigger, so no cash card today; the smoke showed the preview.' -ForegroundColor DarkGray
