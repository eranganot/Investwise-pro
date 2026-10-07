# ship-pr77-holding-review.ps1
# PR 77 (plan item 6, "PR E") - a holding review per holding. It sells only
# when selling on the signal has paid on that holding's own history; otherwise
# the card arms a stop. Run from anywhere; it applies its own patch. Needs PR 76
# on main (38fd04e).
#
# Also fixes a PROVEN defect: a trim or stop on a ticker held in two accounts
# sold one account's shares and deleted both rows (6 + 4 shares, trim 5: all 10
# gone, 590 credited, 900 lost). Now every account counts, oldest lot first.
#
# NO MIGRATION. Schema stays 0024. After the deploy this script measures your
# holdings once (POST /holding-review/refresh) - a measurement, not a change
# to your book - then runs the smoke.
#
# EXPECTED GATE: sandbox measured 2078 passed / 0 failed on SQLite AND Postgres 16, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 77: holding review - sell only with a measured edge, otherwise a stop'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 77 already landed - nothing to do.' -ForegroundColor Yellow
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
if ($head -ne '38fd04e') {
    Write-Host "  HEAD is $head, the patch was built on 38fd04e (PR 76)." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
# Already-applied is normal (you may have run git apply by hand). --reverse
# --check proves every hunk is present; neither check touches the index.
# Stderr is merged and discarded so PS 5.1 does not turn git's message into a
# terminating error before the exit code is read (see ship-pr67-1).
$patch = Join-Path $repo 'pr77-holding-review.patch'
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
    if (-not $forwardOk) { throw 'the PR 77 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 77 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 77 ') {
    throw 'STATUS.md has no PR 77 section - the patch did not land fully'
}
Write-Host '  STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    Write-Host '  Suite non-zero. Sandbox: 2078 passed / 0 failed on SQLite and Postgres 16.' -ForegroundColor Red
    Write-Host '  Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/holding_review.py',
    'app/services/recommendations.py',
    'app/services/rules_service.py',
    'app/services/portfolio_analytics.py',
    'app/api/routes/recommendations.py',
    'app/worker/scheduler.py',
    'app/static_app/index.html',
    'tests/_review_fixtures.py',
    'tests/test_reviewer_states.py',
    'tests/test_reviewer_sell_requires_measured_edge.py',
    'tests/test_reviewer_defers_to_sleeve_rule.py',
    'tests/test_reviewer_card_claims.py',
    'tests/test_reviewer_measures_on_demand.py',
    'tests/test_a_position_at_zero_is_not_a_holding.py',
    'tests/test_position_history.py',
    'scripts/smoke/smoke-pr77-holding-review.ps1',
    'scripts/deploy/ship-pr77-holding-review.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'Plan item 6 (PR E); decisions 2026-09-28/29: sell only with a measured edge,',
    'otherwise the card acts (a stop); measure unmeasured holdings on demand.',
    '',
    'services/holding_review: per holding, 20/50/200-day trend state; two signals',
    '(20/50 trim half, 50/200 exit) backtested nightly on the holding own ~10y,',
    'net of costs and 25% CGT, vs holding. A sell card only when fresh and the',
    'rule beat holding with no deeper drawdown; else a stop-loss card. Sleeves',
    'defer to their own rule. Nightly job 04:40, on-demand background pass,',
    'POST /api/v1/holding-review/refresh.',
    '',
    'Root cause fixed, proven by reproduction: trim/sell_position resolved a',
    'ticker to ONE row and delete_position removed every row, so a holding in',
    'two accounts lost shares with no proceeds (trim 5 of 6+4: 900 ILS gone).',
    'The rules index had the same last-row fault. One _plan_sale now decides the',
    'lots (oldest first) for every card estimate and every Accept.',
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
Write-Host '=== 7a. measure your holdings once (a measurement, not a trade) ===' -ForegroundColor Cyan
. (Join-Path $repo 'scripts\agent-key.ps1')
$key = Get-AgentKey -BaseUrl $base
try {
    $m = Invoke-RestMethod -Method Post -Uri "$base/api/v1/holding-review/refresh" -Headers @{ 'x-agent-key' = $key } -TimeoutSec 900
    foreach ($r in @($m.measured)) { Write-Host ("  {0,-6} ok={1}  short edge={2}  long edge={3}" -f $r.ticker, $r.ok, $r.short, $r.long) }
} catch {
    Write-Host "  refresh failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host '  Not fatal: Today measures unmeasured holdings itself, and the nightly job at 04:40 does too.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '=== 7b. smoke ===' -ForegroundColor Cyan
# On the PR 76 code the same script FAILED 2 checks (local server); on PR 77: 0.
# After this deploy it must pass.
& (Join-Path $repo 'scripts\smoke\smoke-pr77-holding-review.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 77 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 77 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host '  Today: a folded Holding review card, one row per holding.' -ForegroundColor DarkGray
