# ship-pr67-1-the-cards-that-cried-wolf.ps1
# PR 67.1 / 67.2 - the cards that replaced a silence with a noise, the ghost
# that was hiding the top-ranked commodity, and the test that aged out.
# Run AFTER PR 67 (0965e18). It applies its own patch.
#
# ALL THREE DEFECTS WERE FOUND BY RUNNING THE SMOKE SCRIPT AGAINST THE LIVE
# BUILD, an hour after PR 67 shipped. Two of them were in code written that
# morning. Nothing here came from reading the diff again.
#
# 1 - THE TRAILING-STOP CARD FIRED ON THREE POSITIONS AT THEIR PEAKS.
#
# A trailing stop RATCHETS to peak x (1 - trail), so a 10% trail sits ~10%
# below the price BY CONSTRUCTION whenever the position is at its peak. PR 67
# measured nearness as distance from the level as a share of price - which is
# the trail's WIDTH, not danger. Live, all three at once:
#
#     QQQ   9.3% above its stop  ->   7.8% of the trail actually given back
#     VTI   8.8%                 ->  12.9%
#     BND   8.7%                 ->  14.2%
#
# QQQ was 7.8% of the way from its peak to its stop and the app told him it was
# "closing on your trailing stop". Now measured as
# (peak - price) / (peak - stop), firing at 60%. All three go quiet.
#
# Eran's "within 10% of the level" answer was right for a TAKE-PROFIT, a level
# approached from below. Applying it to a mechanism that moves with the price
# was mine to get right.
#
# 2 - THE NEAR-CAP CARD SHIPPED est_amount 0.0.
#
# The trim required at a cap you have not breached is zero, and moved_on
# un-mutes on the amount MOVING. So the card could never un-mute - the exact
# defect these cards were written to fix. Now the headroom in shekels (SOXL 47,
# QQQ 195), which moves whenever the position does.
#
# And the unit test asserted "est_amount is not None" and PASSED on 0.0: a
# check that could not fail for the thing it was checking. Now asserts > 0.
#
# 3 - THE GHOST WAS HIDING THE APP'S OWN TOP-RANKED COMMODITY.
#
# _buy_idea_recs skips a ticker the book already holds - correct, that card
# exists to surface names the book does NOT hold. But 'held' was built from
# every position row with no quantity filter. USO sat at 0.0 shares and ranks
# 85.4 on the commodity screen, ABOVE the COMT at 79.2 the card was
# recommending. The ghost made the app skip its own best candidate and name the
# runner-up, with nothing on screen to say why. Third consequence of that one
# row, after the three armed rules and Commodities reading 0%.
#
# 4 - CI HAD BEEN RED FOR DAYS, AND NOT BECAUSE OF PR 67.
#
# test_a_real_quote_afterwards_heals_the_row failed on every run since
# 2026-09-09 with no app code changing. _quote() hardcoded
# as_of="2026-09-01T20:00:00+00:00"; refresh_all_positions refuses a quote
# older than STALE_AFTER_TRADING_DAYS (5) and does not write it. Measured at
# the fix: 9 trading days old against a 5-day window, so 'updated' was 0 and
# the case had become impossible to pass - for the guard working perfectly.
#
# The other cases in that file kept passing because they assert a REFUSAL, and
# a stale quote is refused too. The suite read 1815/1816 healthy while one test
# was unpassable. A date literal in a fixture is a test that schedules its own
# failure.
#
# EXPECT A FULLY GREEN GATE: 1820 passed, 0 failed. That is the first clean run
# on this repo in at least a fortnight, so if the suite is non-zero here, read
# it rather than waving it through - the known exception is gone.
#
# ALSO COMMITS THE TWO PR 67 SCRIPTS, which are still untracked: the ship
# script and the smoke script. Every other one in scripts/deploy and
# scripts/smoke is tracked, and a script that exists only in one working tree
# is the same class of thing as a service that does.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 67.1: the cards that cried wolf, the ghost that hid USO, and the test that aged out'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 67.1 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }

Write-Host '=== 2. apply pr67.2 (67.1 is already in your tree) ===' -ForegroundColor Cyan
# ALREADY-APPLIED IS THE NORMAL CASE. The lesson from the PR 67 run: the patch
# ships with a "run git apply" instruction beside it, so applying it by hand
# first is the obvious thing to do and must not be an error here.
# --reverse --check is the definitive test: if it reverses cleanly every hunk
# is present and none is partial. A HALF-applied tree fails it and still
# reaches the throw, which is the case that must not be waved through. Neither
# --check nor --reverse touches the index, so neither can strand a lock.
$patch = Join-Path $repo 'pr67.2-the-ghost-was-hiding-uso.patch'
if (-not (Test-Path $patch)) { throw "patch not found: $patch" }
# REDIRECTING STDERR TO $null IS NOT SAFE HERE. Under
# $ErrorActionPreference = 'Stop', Windows PowerShell 5.1 turns a native
# command's STDERR into a TERMINATING error -- so the moment this check does
# its job and reports "does not apply", git's own error text kills the script
# before the exit code is ever read.
#
# It survived the PR 67 run only because the reverse-check SUCCEEDED there and
# printed nothing. The first time it correctly said no, it threw:
#   NativeCommandError ... at ship-pr67-1...ps1:106
#
# PowerShell 7 does not behave this way, which is exactly why a sandbox test on
# pwsh 7 could not reproduce it -- evidence from the wrong instance. Merging
# stderr into the success stream and discarding it yields no ErrorRecord on
# either version.
$eap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
git apply --check --reverse $patch 2>&1 | Out-Null
$reverseOk = ($LASTEXITCODE -eq 0)
git apply --check $patch 2>&1 | Out-Null
$forwardOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $eap

if ($reverseOk) {
    Write-Host '  already applied in full (reverse-check clean) - skipping.' -ForegroundColor Yellow
} else {
    if (-not $forwardOk) { throw 'pr67.2 does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'pr67.2 failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. the pieces this commit needs ===' -ForegroundColor Cyan
foreach ($f in @('scripts/deploy/ship-pr67-the-market-was-shut.ps1',
                 'scripts/smoke/smoke-pr67-the-market-was-shut.ps1')) {
    if (-not (Test-Path (Join-Path $repo $f))) { throw "missing: $f" }
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 67\.1') {
    throw 'STATUS.md has no PR 67.1 section - re-splice it before committing'
}
Write-Host '  both scripts present, STATUS.md section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    # NO KNOWN EXCEPTION ANY MORE. The quote-of-zero fixture is fixed, so a
    # non-zero suite here is this change's problem and nothing else's.
    Write-Host ''
    Write-Host '  Suite non-zero. The pre-existing failure is FIXED in this change,' -ForegroundColor Red
    Write-Host '  so there is no longer an expected red. Sandbox measured' -ForegroundColor Red
    Write-Host '  1820 passed / 0 failed. Read the failures - do not skip past this.' -ForegroundColor Red
    throw 'suite failed'
}
Write-Host '  Green, with nothing waved through.' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/recommendations.py',
    'tests/test_the_app_says_when_to_take_profits.py',
    'tests/test_a_position_at_zero_is_not_a_holding.py',
    'tests/test_a_quote_of_zero_is_not_a_quote.py',
    'scripts/deploy/ship-pr67-the-market-was-shut.ps1',
    'scripts/deploy/ship-pr67-1-the-cards-that-cried-wolf.ps1',
    'scripts/smoke/smoke-pr67-the-market-was-shut.ps1',
    'STATUS.md'
)

$msg = @(
    $subject,
    '',
    'All three found by RUNNING the PR 67 smoke script against the live build,',
    'an hour after it shipped. Two were in code written that morning.',
    '',
    '1  A trailing stop ratchets to peak x (1 - trail), so a 10% trail sits ~10%',
    '   below the price BY CONSTRUCTION at the peak. Measuring nearness as a',
    '   share of price measured the trail WIDTH, not danger, and fired on three',
    '   positions at once: QQQ had given back 7.8% of its trail, VTI 12.9%, BND',
    '   14.2%. Now (peak - price) / (peak - stop), firing at 60%. All go quiet.',
    '   The 10%-of-price rule was right for take_profit and wrong here.',
    '',
    '2  The near-cap card shipped est_amount 0.0 - the trim at a cap you have',
    '   not breached - and moved_on un-mutes on the amount MOVING, so it could',
    '   never un-mute: the exact defect these cards fix. Now the headroom in',
    '   shekels. The test asserted "is not None" and PASSED on 0.0; now > 0.',
    '',
    '3  _buy_idea_recs built "held" from every position row with no quantity',
    '   filter, so the 0-share USO ghost made the app skip its own top-ranked',
    '   commodity (USO 85.4) and recommend the runner-up (COMT 79.2), silently.',
    '   Third consequence of that row, after the armed rules and Commodities 0%.',
    '',
    '4  CI had been red since 2026-09-09 with no app code changing. _quote()',
    '   hardcoded as_of=2026-09-01 and refresh_all_positions refuses a quote',
    '   older than 5 trading days: 9 days old, updated=0, the "heals the row"',
    '   case unpassable - for the staleness guard working perfectly. The other',
    '   cases pass because they assert a REFUSAL, and stale is refused too, so',
    '   the suite read healthy while one test could not pass. Fixture stamps',
    '   now(). A date literal in a fixture schedules its own failure.',
    '',
    'Also tracks the two PR 67 scripts, which existed only in a working tree.',
    '',
    'Gate: 1820 passed, 0 failed, ruff clean. First fully green run in a fortnight.',
    '',
    'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_01L3Ar15DWSZgSVtcgCFu9ZN'
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
# The whole point: the same script that found these defects now proves they are
# gone, against the build that just landed. It resolves the agent key through
# scripts\agent-key.ps1 (IW_AGENT_KEY, or scripts\.agent-key) - never inlined.
& (Join-Path $repo 'scripts\smoke\smoke-pr67-the-market-was-shut.ps1') -Sha $sha -BaseUrl $base
$smoke = $LASTEXITCODE

Write-Host ''
if ($smoke -ne 0) {
    Write-Host 'PR 67.1 PUSHED, BUT THE SMOKE FAILED.' -ForegroundColor Red
    Write-Host 'Read the FAIL lines above before doing anything else.' -ForegroundColor Red
    exit 1
}
Write-Host 'PR 67.1 SHIPPED and smoke-clean.' -ForegroundColor Green
Write-Host 'Still outstanding by design:' -ForegroundColor DarkGray
Write-Host '  * the trading-day guard needs a SATURDAY to prove (smoke check 1b)' -ForegroundColor DarkGray
Write-Host '  * the USO row at 0 shares is yours to delete - the code only stops new ones' -ForegroundColor DarkGray
Write-Host '  * candidate_search.py and its test are STILL untracked' -ForegroundColor DarkGray
