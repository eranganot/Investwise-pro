# ship-pr76-one-return-convention.ps1
# PR 76 - the projection counted volatility twice; one return convention now.
# Run from anywhere; it applies its own patch. Needs PR 75 on main (3c89e43).
#
# Every return the app projects is a COMPOUND rate (measured CAGR, the
# instrument table, the 8.5% objective table -- Eran, 2026-09-28). The plan
# projection and the behind-goal card fed it to the engine as an arithmetic
# drift, so the volatility drag was taken twice: on your book the 6-year median
# was 61,049 where compounding gives 95,840. After: 94,449. The risk report's
# median compounded continuously (17% high); it now compounds annually too.
# Also: cash research is like for like (a fund only by a fund).
#
# NO MIGRATION. Schema stays 0024.
#
# EXPECTED GATE: sandbox measured 2053 passed / 0 failed on SQLite AND Postgres 16, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 76: one return convention - projections stop counting volatility twice'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 76 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne '3c89e43') {
    Write-Host "  HEAD is $head, the patch was built on 3c89e43 (PR 75)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr76-one-return-convention.patch'
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
    if (-not $forwardOk) { throw 'the PR 76 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 76 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 76 ') {
    throw 'STATUS.md has no PR 76 section - the patch did not land fully'
}
Write-Host '  STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 2053 passed / 0 failed on SQLite and Postgres 16.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/engines/return_convention.py',
    'app/engines/portfolio_risk.py',
    'app/api/routes/plan.py',
    'app/services/recommendations.py',
    'app/services/target_solver.py',
    'app/services/redeploy_research.py',
    'app/services/context_service.py',
    'app/static_app/index.html',
    'tests/_redeploy_fixtures.py',
    'tests/test_projection_compounds_at_the_stated_rate.py',
    'tests/test_redeploy_is_like_for_like.py',
    'tests/test_one_goal_everywhere.py',
    'tests/test_the_forward_numbers_say_what_they_are.py',
    'scripts/smoke/smoke-pr76-one-return-convention.ps1',
    'scripts/deploy/ship-pr76-one-return-convention.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Approved 2026-09-28 22:06 (separate PR before PR E); table rates are compound.',
    '',
    'Root cause, proven: SimulationEngine takes an arithmetic drift; every',
    'return the app projects is a compound rate. /plan/projection (and so the',
    'digest and plan health) and the behind-goal card passed the compound rate',
    'as the drift, taking the volatility drag twice. Eran''s book: 6y median',
    '61,049 vs 95,840 compounding. The target solver already converted.',
    '',
    'engines/return_convention.drift_pct is the one converter; the projection',
    '(base, deposits, +1pp) and the card (headline, conservative, amount',
    'search) use it. The risk report compounded continuously (17% high); now',
    'annual. Cash research: a held fund is replaced only by a fund, a stock',
    'only by a stock (option B; option A in the backlog).',
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
# On the PR 75 code the same script FAILED 3 checks (local server); on PR 76: 0.
# After this deploy it must pass.
& (Join-Path $repo 'scripts\smoke\smoke-pr76-one-return-convention.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 76 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 76 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host '  Plan screen: the projection median now compounds at your stated return.' -ForegroundColor DarkGray
