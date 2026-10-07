# ship-pr74-goals-on-money-put-in.ps1
# PR 74 - item 4 of PLAN_2026-09-28_six_items.md (plan section 4, C1-C3), approved 10:10.
# Run from anywhere; it applies its own patch. Needs PR 73 on main (de8cffb).
#
# FOUR GOALS, NOT ONE. /plan derived the target live from NAV, so progress
# was NAV / (NAV x 1.3^6) = 0.2072 forever; the behind card used the stored
# target, 5 years and the static 8.5%; two cards rewrote the target and the
# horizon to meet the projection.
#
#   C1  goal_service: every deposit compounded at your ROI from its own date
#       to the deadline (31 Dec 2031). Moves only when money moves or you
#       edit ROI/deadline. Progress = NAV / target; on-track line shown.
#   C2  one return basis: the card's headline is /plan's; the conservative
#       table figure a second line; every amount sized on the same engine.
#   C3  "Set a target you'll actually hit" and "Give it more time" removed.
#
# NO MIGRATION. Schema stays 0024.
#
# EXPECTED GATE: sandbox measured 2015 passed / 0 failed on SQLite AND Postgres 16, ruff clean. A red suite here is this change's problem.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 74: the goal is the money you put in; one goal everywhere; no card moves it'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 74 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne 'de8cffb') {
    Write-Host "  HEAD is $head, the patch was built on de8cffb (PR 73 redeploy)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr74-goals-on-money-put-in.patch'
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
    if (-not $forwardOk) { throw 'the PR 74 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 74 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 74 ') {
    throw 'STATUS.md has no PR 74 section - the patch did not land fully'
}
Write-Host '  STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 2015 passed / 0 failed on SQLite and Postgres 16.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/goal_service.py',
    'app/api/routes/plan.py',
    'app/api/routes/intake.py',
    'app/services/intake_service.py',
    'app/services/recommendations.py',
    'app/services/portfolio_risk_service.py',
    'app/services/context_service.py',
    'app/static_app/index.html',
    'tests/test_goal_ignores_market_moves.py',
    'tests/test_goal_moves_only_on_contribution.py',
    'tests/test_one_goal_everywhere.py',
    'tests/test_no_card_rewrites_the_target.py',
    'tests/test_goal_edges.py',
    'tests/test_recos_apply.py',
    'tests/test_a_mute_does_not_outlive_its_number.py',
    'scripts/smoke/smoke-pr74-goals-on-money-put-in.ps1',
    'scripts/deploy/ship-pr74-goals-on-money-put-in.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Item 4 of PLAN_2026-09-28_six_items.md (plan section 4), approved 2026-09-28.',
    '',
    'Root cause, proven: four goals. /plan derived the target from live NAV',
    '(NAV x 1.3^6), so progress was the constant 0.2072 - reproduced on the',
    'PR 73 code to four decimals. The behind card read the stored target,',
    'counted 5 years where /plan counted 6, and used the static 8.5% where /plan',
    'used the measured return. Two cards rewrote the target and the horizon.',
    '',
    'C1  goal_service: each contribution compounded at the ROI from its own',
    '    date to the deadline (31 Dec of the deadline year). Moves only when',
    '    money moves or ROI/deadline change. Progress = NAV / target, plus the',
    '    on-track line. /plan, /plan/projection, the behind card, the risk',
    '    report and the context all call it. Opening balances keep their date.',
    'C2  one return basis; the conservative table figure is a second line;',
    '    the monthly amount is searched on the same Monte-Carlo engine.',
    'C3  "Set a target you will actually hit" and "Give it more time" removed.',
    '',
    'Review: 10 findings, 9 fixed (see STATUS.md PR 74).',
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
# On the PR 73 code the same script FAILED 12 checks (local server seeded with
# your shape: 20,000 put in, 30%/yr, 2031). On PR 74: 0. After this deploy it must pass.
& (Join-Path $repo 'scripts\smoke\smoke-pr74-goals-on-money-put-in.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 74 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 74 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host '  Open Plan: the target is now built from what you put in (about 82,700).' -ForegroundColor DarkGray
