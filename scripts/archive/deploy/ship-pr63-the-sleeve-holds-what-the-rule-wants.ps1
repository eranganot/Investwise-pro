# ship-pr63-the-sleeve-holds-what-the-rule-wants.ps1
# PR 63 - a sleeve's target was the catalogue's, on every day of the year.
# Run after PR 62 (6dc3c8d). It applies its own patch.
#
# Eran, 2026-09-06: "the swing should be a card that replace the TQQQ with QQQ
# automatically", and on the design fork: "I agree (a) is right."
#
# sleeve_targets read the catalogue's static weights minus base and stopped
# there. So btm_swing_dip targeted TQQQ whether or not its rule was in a setup -
# including the ~85% of the year it is not. The catalogue's own docstring says
# what that means: "base is the core holding, not cash. A swing setup is live
# perhaps 10-15% of the time." For most of the year the Plan tab, every drift
# card, the funding engine and the armed caps described a position the rule was
# not asking for, and nothing in the app could tell "the rule wants TQQQ" apart
# from "the rule has never been consulted".
#
# THE FORK, AND WHY (a):
#
#                       when the rule is off              on Eran's book
#   (a) - chosen        the sleeve targets its BASE       TQQQ -> QQQ
#   (b)                 targets nothing, money to core    TQQQ -> VTI + BND
#
# (b) is cheaper and reads well - "capital sits in the core between setups" is
# the catalogue's own phrase - but his core is Classic 60/40, so it would sell
# the position into bonds. The strategy says the money parks in QQQ.
#
# (a) OVERTURNS A DOCSTRING. sleeve_targets said funding the base means "selling
# your holdings to buy an index fund you never asked for". True of the catalogue
# default; false here. You asked for it by choosing a strategy whose spec parks
# the money in the base between setups.
#
# ONE READ, PASSED DOWN. sleeve_service.live_sleeve_targets loads the stored
# signal once per request and hands it to the still-pure, still-synchronous
# sleeve_targets(..., live=...). Making that function async instead would drag a
# session through card_scope.scope_for, which is documented as pure "so the
# mapping is testable without a book". Threaded through every consumer, because
# a primitive that means one thing in six places and another in a seventh is how
# PR 60 got two cards wanting the same 7,476.
#
# retire_strategy_rules now takes the UNION of both legs: a sleeve that has
# flipped once has armed a cap on the aggressive instrument AND on the base, and
# retiring it with only today's leg would leave the other armed with no owner -
# the exact failure PR 59.1 exists to prevent.
#
# NO STALENESS GATE, DELIBERATELY. The obvious guard - ignore an old target,
# fall back to the catalogue - fails in the dangerous direction, because the
# catalogue default is always the leveraged leg. A dead evaluator would turn
# "the rule says hold QQQ" back into "the sleeve wants TQQQ" and the app would
# recommend its way into a 3x fund on no signal at all. The age travels with the
# answer instead: /plan/sleeves now carries following_signal and signal_as_of.
#
# AND A CARD THAT NEARLY TOLD A LIE. The pending-signal card says "The app is
# not moving anything for you". With the target now following the signal that
# reads as stale, so the first version of this change removed it - and two
# existing tests caught that, correctly: the sentence is still true OF THAT
# CARD. Nothing moves until something is tapped. The promise is kept and a
# pointer to the funding card is added beside it.
#
# WHAT IT ADDS UP TO: once the sleeve targets QQQ and holds none of it, TQQQ is
# a position no sleeve claims - and PR 61's steps_back_to rule makes selling it
# to buy QQQ actionable in one tap, funded from that exact position by PR 61.1.
# Three PRs, one behaviour: the rule steps back, the target follows, and the
# card finishes the move.
#
# Gates on these exact bytes: 1742 SQLite, 1742 PostgreSQL on a dropped and
# recreated database, ruff clean. 8 of 10 new tests confirmed RED against
# 6dc3c8d.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

Write-Host '=== 0. PR 62 must be in first ===' -ForegroundColor Cyan
$log = (git log --oneline -3 | Out-String)
if ($log -notmatch 'PR 62') {
  throw 'PR 62 is not in the last three commits. Ship it first - PR 63 builds on its tree.'
}
git rev-parse --short HEAD
git status --short

Write-Host '=== 1. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr63.patch'
if (-not (Test-Path $patch)) { throw 'pr63.patch is not in the repo root' }
$already = (Select-String -Path 'app\services\sleeve_service.py' -Pattern 'live_sleeve_targets' -Quiet)
if ($already) {
  Write-Host '  already applied' -ForegroundColor Yellow
} else {
  git apply -p1 $patch
  if ($LASTEXITCODE -ne 0) { throw 'git apply failed - do not continue' }
  Write-Host '  applied' -ForegroundColor Green
}

Write-Host '=== 2. the STATUS entry ===' -ForegroundColor Cyan
$py = @'
import io
frag = io.open("PR63_STATUS.md", encoding="utf-8").read().rstrip("\n")
s = io.open("STATUS.md", encoding="utf-8").read()
if "PR 63 " in s.split("## PR 62")[0]:
    print("already present")
else:
    anchor = "## PR 62 "
    i = s.index(anchor)
    s = s[:i] + frag + "\n\n" + s[i:]
    s = s.replace("**HEAD is PR 62.**", "**HEAD is PR 63.**", 1)
    io.open("STATUS.md", "w", encoding="utf-8").write(s)
    print("inserted")
'@
$pyPath = Join-Path $repo '_status_insert.py'
[System.IO.File]::WriteAllText($pyPath, $py)
python $pyPath
if ($LASTEXITCODE -ne 0) { throw 'STATUS insert failed' }
Remove-Item $pyPath -Force

Write-Host '=== 3. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 4. the guards, then everything ===' -ForegroundColor Cyan
$guards = @(
  'tests/test_the_sleeve_holds_what_the_rule_wants.py',
  'tests/test_strategy_signals.py',
  'tests/test_sleeves_mean_what_they_say.py',
  'tests/test_plan_sleeves.py',
  'tests/test_the_card_finishes_the_move_it_recommended.py',
  'tests/test_a_rule_can_be_retired_without_dropping_the_position.py',
  'tests/test_card_claims.py'
)
$have = @()
foreach ($g in $guards) { if (Test-Path (Join-Path $repo $g)) { $have += $g } }
if ($have.Count -lt 5) { throw 'the guard list did not resolve - check the test filenames' }
python -m pytest -q @have
if ($LASTEXITCODE -ne 0) { throw 'the sleeve guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$msg = @'
PR 63: a sleeve's target was the catalogue's, on every day of the year

Eran: "the swing should be a card that replace the TQQQ with QQQ automatically",
and on the design fork: "I agree (a) is right."

sleeve_targets read the catalogue's static weights minus base and stopped
there. So btm_swing_dip targeted TQQQ whether or not its rule was in a setup -
including the ~85% of the year it is not. The catalogue's own docstring says
what that means: "base is the core holding, not cash. A swing setup is live
perhaps 10-15% of the time." For most of the year the Plan tab, every drift
card, the funding engine and the armed caps described a position the rule was
not asking for, and nothing in the app could tell "the rule wants TQQQ" apart
from "the rule has never been consulted".

THE FORK, AND WHY (a):

                      when the rule is off              on Eran's book
  (a) - chosen        the sleeve targets its BASE       TQQQ -> QQQ
  (b)                 targets nothing, money to core    TQQQ -> VTI + BND

(b) is cheaper and reads well - "capital sits in the core between setups" is the
catalogue's own phrase - but his core is Classic 60/40, so it would sell the
position into bonds. The strategy says the money parks in QQQ; (b) parks it
somewhere else.

(a) OVERTURNS A DOCSTRING, and the overturned sentence is worth keeping in view.
sleeve_targets said funding the base would mean "selling your holdings to buy an
index fund you never asked for". True of the catalogue default; false here. You
asked for it by choosing a strategy whose spec parks the money in the base
between setups.

ONE READ, PASSED DOWN. sleeve_service.live_sleeve_targets loads the stored
StrategySignalState once per request and hands it to the still-pure, still-
synchronous sleeve_targets(..., live=...). The alternative - making that
function async - drags a session through card_scope.scope_for, which is
documented as pure "so the mapping is testable without a book".

Threaded through every consumer, because a primitive that means one thing in six
places and another in a seventh is how PR 60 got two cards wanting the same
7,476: all_sleeve_targets, sleeve_occupancy, _fund_sleeve, _arm_sleeve_caps,
sleeve_cap_plan, swap_sleeve, card_scope and the Today drift loop. strategy.py's
apply-preview keeps the catalogue - it previews a strategy that is not applied
yet, so there is no signal to follow.

retire_strategy_rules now takes the UNION of both legs. A sleeve that has
flipped once has armed a cap on the aggressive instrument AND on the base;
retiring it with only today's leg in hand would leave the other armed with no
owner, which is the exact failure PR 59.1 exists to prevent.

NO STALENESS GATE, DELIBERATELY. The obvious guard - ignore a target older than
N days, fall back to the catalogue - fails in the dangerous direction. The
catalogue default is always the leveraged leg, so a dead evaluator would turn
"the rule says hold QQQ" back into "the sleeve wants TQQQ", and the app would
recommend its way into a 3x fund on the strength of no signal at all. The last
thing the rule actually said is strictly more informative than a default
asserting a signal nobody produced. The age travels with the answer instead:
/plan/sleeves now carries following_signal and signal_as_of per sleeve.

THE CARD THAT NEARLY TOLD A LIE, AND THE TEST THAT STOPPED IT. The pending-
signal card said "The app is not moving anything for you - place the change at
your broker and mirror it in Holdings." With the target now following the signal
that reads as stale, so the first version of this change replaced the line. Two
existing tests failed and they were right: the sentence is still true OF THAT
CARD. Nothing moves until something is tapped and no brokerage order is ever
placed. What was missing was not a correction but a pointer, so the promise is
kept and a line is added saying the funding card on Today can now size and apply
the tracked-book side.

WHAT IT ADDS UP TO. Once the sleeve targets QQQ and holds none of it, TQQQ is a
position no sleeve claims - and PR 61's steps_back_to rule makes selling it to
buy QQQ actionable in one tap, funded from that exact position by PR 61.1. The
three PRs are one behaviour: the rule steps back, the target follows, and the
card finishes the move.

tests/test_the_sleeve_holds_what_the_rule_wants.py - 10 tests, 8 confirmed RED
against 6dc3c8d.

Gates: 1742 passed on SQLite, 1742 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/strategy_service.py app/services/sleeve_service.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- app/services/card_scope.py app/services/recommendations.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- app/services/strategy_signal_service.py app/api/routes/plan.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_the_sleeve_holds_what_the_rule_wants.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr63-the-sleeve-holds-what-the-rule-wants.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

foreach ($tmp in @('PR63_STATUS.md', 'pr63.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 6. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  Write-Host '=== 7. which leg does each sleeve want ===' -ForegroundColor Cyan
  $sl = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl.sleeves) {
    $sig = 'catalogue'
    if ($s.following_signal) { $sig = ('signal ' + $s.signal_as_of) }
    $line = "  {0,-16} target {1,6:N2}%  held {2,6:N2}%  wants {3,-12} [{4}]"
    Write-Host ($line -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), ($s.tickers -join ','), $sig)
  }
  Write-Host '  Your book runs btm_trend_soxl and btm_qqq_hold: one trend rule and one'
  Write-Host '  buy-and-hold, so unless the SOXL trend has flipped both should still read'
  Write-Host '  their catalogue leg. This lands the machinery; the visible change arrives'
  Write-Host '  the first time a rule steps back.'
}

Write-Host ''
Write-Host 'PR 63 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: PR 64 - cash as a soft target with a 150 hard floor.'
Write-Host '        PR 65 - C: the veto and the cap that nothing reads.'
