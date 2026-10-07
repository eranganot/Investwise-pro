# ship-pr72-a-rule-always-has-a-card.ps1
# PR 72 - items 1 and 5 of PLAN_2026-09-28_six_items.md (approved 2026-09-28 10:10).
# Run from anywhere; it applies its own patch.
#
# 1 - A TRIGGERED RULE WITH NO CARD. Live on e556b64: SOXL take-profit 8cd9f1ff
#     fired at 148.52 (price 151.45). The card WAS built, then the contradiction
#     pass hid it because SOXL sits on its 40% sleeve - and the banner had
#     already counted it. Now the card comes back as a DECISION: sell the slice
#     and lower the sleeve by what you sold, or keep the position and move the
#     rule (never retire it). The banner is reconciled on what is served.
#
# 2 - THE LADDER NEVER RAN. resolve_rule re-armed a take-profit on outcome
#     "accepted"; Accept sends "executed". So the rule came back at 148.52 and
#     fired again on 09-25. Meanwhile each trim was re-bought by the sleeve
#     funder within a minute (09-18, 09-22), tax paid both times.
#
# 3 - "SHOW THEM" RESURRECTED EVERYTHING. It called the bulk restores, which
#     empty the done and ignored lists and make every card acceptable again.
#     Now a read-only history (Done / Ignored / Blocked, when, amount, reason)
#     with a per-card Restore.
#
# NO MIGRATION. Schema stays 0024.
#
# EXPECTED GATE: sandbox measured 1969 passed / 0 failed on SQLite AND on
# Postgres 16, ruff clean. A red suite here is this change's problem.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 72: a triggered rule always has a card; the ladder climbs; history is read-only'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 72 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne 'e556b64') {
    Write-Host "  HEAD is $head, the patch was built on e556b64 (PR 71)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr72-a-rule-always-has-a-card.patch'
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
    if (-not $forwardOk) { throw 'the PR 72 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 72 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 72 ') {
    throw 'STATUS.md has no PR 72 section - the patch did not land fully'
}
if (-not (Test-Path (Join-Path $repo 'PLAN_2026-09-28_six_items.md'))) {
    throw 'PLAN_2026-09-28_six_items.md is missing - it is the approved plan this PR implements'
}
Write-Host '  STATUS.md section and the approved plan are present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 1969 passed / 0 failed on SQLite and Postgres.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/api/routes/recommendations.py',
    'app/services/recommendations.py',
    'app/services/rules_service.py',
    'app/services/sleeve_service.py',
    'app/static_app/index.html',
    'tests/test_the_take_profit_ladder.py',
    'tests/test_a_rule_that_fights_a_sleeve_is_a_decision.py',
    'tests/test_history_is_read_only.py',
    'scripts/smoke/smoke-pr72-a-rule-always-has-a-card.ps1',
    'scripts/deploy/ship-pr72-a-rule-always-has-a-card.ps1',
    'PLAN_2026-09-28_six_items.md',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Items 1 and 5 of PLAN_2026-09-28_six_items.md, approved 2026-09-28.',
    '',
    '1  SOXL take-profit 8cd9f1ff fired (148.52, price 151.45). Its card was',
    '   built, then contradictions.check hid it: SOXL 40.1% on a 40% sleeve.',
    '   The banner was reconciled BEFORE that pass, so it counted a card that',
    '   no longer existed, and nothing on screen showed the suppression.',
    '   Now a rule sell blocked by its sleeve becomes a decision card:',
    '   Sell & lower sleeve (the 25% slice; sleeve drops by the weight sold,',
    '   40% -> ~30%, so the funder does not buy it back), or Keep & update',
    '   rule (next rung or one step above today, 163.37; a breached level is',
    '   refused). The banner is reconciled after the pass and the conversion.',
    '   Other suppressions are listed in the history as Blocked, with reason.',
    '',
    '2  The take-profit ladder never ran: resolve_rule re-armed on "accepted",',
    '   Accept sends "executed". 8cd9f1ff executed 09-22 at 148.52 and fired',
    '   again 09-25 at 148.52. Each trim was re-bought by sleeve_fund within a',
    '   minute (09-18 20:08->20:09, 09-22 18:40->18:41). Now re-arms on',
    '   executed; a second "acknowledged" on a resolved rule is a no-op.',
    '',
    '3  "show them" called the bulk restores: every done/ignored mark erased,',
    '   every card live again with a working Accept. Now a read-only history',
    '   (Done / Ignored / Blocked, when, amount) and a per-card Restore.',
    '',
    'Also: Accept finds cards in more_recommendations (PR 70 served them with',
    'an Accept that answered 404).',
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
# Before this deploy the same script FAILED 7 checks on e556b64 (measured in
# the sandbox) - it reproduces both bugs. After it, it must pass.
& (Join-Path $repo 'scripts\smoke\smoke-pr72-a-rule-always-has-a-card.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 72 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 72 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host 'On Today you should now see the SOXL decision card with two buttons.' -ForegroundColor Green
Write-Host 'Next: PR 73 (plan section 3) starts with an investigation step only.' -ForegroundColor DarkGray
