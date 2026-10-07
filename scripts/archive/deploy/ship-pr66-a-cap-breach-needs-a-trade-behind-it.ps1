# ship-pr66-a-cap-breach-needs-a-trade-behind-it.ps1
# PR 66 - a cap that fires on a sleeve it just funded, and a card that cannot
# say how much. Run AFTER PR 65. It applies its own patch.
#
# Eran, 2026-09-06, on two cards: "why do I still getting guidance cards? all
# these cards should be actionable", and then precisely: "1. it doesn't tell me
# what to do (how much to sell) 2. it doesn't tell me what do do instead (buy
# something else or keep it in cash) 3. it doesn't do the action for me".
#
# He was right that something was wrong. It is not the missing button.
#
# WHAT THE NUMBERS SAID, measured on the live book the same minute:
#
#   SOXL  38.002% against a 38% cap  ->  0.44 shekels to trim
#   QQQ   37.004% against a 37% cap  ->  0.87 shekels to trim
#
# Both fired, both latched, and both rendered a card whose own how read "There's
# no single obvious trade here, so the app won't guess one." The Rules tab said
# it outright: "Triggered - 37.0% of book vs a 37% cap". The cards were guidance
# because there was NOTHING TO TRADE.
#
# THE CAUSE IS STRUCTURAL, AND IT IS OURS:
#   1. _arm_sleeve_caps arms a max_weight cap at EXACTLY the sleeve's target.
#   2. _fund_sleeve funds the sleeve to EXACTLY that target.
#   3. _evaluate fired on weight >= level - AT EQUALITY.
#
# So every correctly funded sleeve tripped its own cap the moment it was funded.
# QQQ's card exists because PR 61 worked: Eran tapped "Finish the move", the
# sleeve funded to 37%, and the cap armed at 37% fired on it.
#
# Then execution_plan refused to name a trade - twice over, since weight_pct is
# rounded to one decimal so 38.0 <= 38.0, and its own MIN_TRADE_ILS guard would
# have refused 0.44 anyway. And clears_with_margin wanted SOXL down at 37.05%
# before it would re-arm: a nag with no exit.
#
# THE FIX, IN THREE PARTS.
#
# A cap breach is a breach when there is a trade behind it. MIN_TRADE_ILS, not a
# new tolerance band - the app already has a number for "not worth the friction".
# execution_plan has applied this test since the MSFT case, one layer too late,
# where it could only downgrade the card instead of preventing it. cap_trim_ils
# is now the single answer the firing test, the re-arm test and execution_plan
# all read.
#
# The amount is in shekels, net of tax, through the function Accept runs. The
# card computed shares * price - the position's NATIVE currency, no FX, no tax,
# unlabelled on a screen where every other number is shekels. On SOXL at $117.28
# a six-share trim reads "~737" while Accept credits about 2,700 to cash.
#
# And it says where the money lands: the net proceeds, the resulting cash
# percentage against the 7.5% target, and which side of it that falls on.
# Deliberately NOT a replacement buy inside the same card - that is a second
# sizing decision in a card that already has one, the shape PR 60 fixed.
#
# WHAT TO EXPECT AFTER: the two cards disappear, because they were about 44 and
# 87 agorot. Step 6 asks production. A real breach fires, says how much, says
# where the money goes, and carries kind "trim", which is actionable.
#
# TWO EXISTING TESTS CAUGHT THE CHANGE AND BOTH WERE FAIR - one grepped a
# function whose body moved, the other built its fixture as "10% exactly", a
# breach of zero shekels. Both updated in the same commit, with the reasons.
#
# Gates on these exact bytes: 1774 SQLite, 1774 PostgreSQL on a dropped and
# recreated database, ruff clean. 4 of 12 new tests confirmed RED against
# 2edba83.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

Write-Host '=== 0. PR 65 must be in first ===' -ForegroundColor Cyan
$log = (git log --oneline -3 | Out-String)
if ($log -notmatch 'PR 65') {
  throw 'PR 65 is not in the last three commits. Ship it first - pr66.patch is cut against its tree.'
}
git rev-parse --short HEAD
git status --short

$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  Write-Host '=== 0b. the two cards, before ===' -ForegroundColor Cyan
  $r0 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $caps0 = @($r0.recommendations | Where-Object { $_.id -like 'rule_*' -and $_.action -like '*cap*' })
  foreach ($c in $caps0) {
    $k = 'none'
    if ($c.apply) { $k = $c.apply.kind }
    Write-Host ("  {0}  [apply {1}]" -f $c.title, $k)
  }
  if ($caps0.Count -eq 0) { Write-Host '  no cap cards right now.' }
}

Write-Host '=== 1. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr66.patch'
if (-not (Test-Path $patch)) { throw 'pr66.patch is not in the repo root' }
$already = (Select-String -Path 'app\services\rules_service.py' -Pattern 'cap_breach_is_material' -Quiet)
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
frag = io.open("PR66_STATUS.md", encoding="utf-8").read().rstrip("\n")
s = io.open("STATUS.md", encoding="utf-8").read()
if "PR 66 " in s.split("## PR 65")[0]:
    print("already present")
else:
    anchor = "## PR 65 "
    i = s.index(anchor)
    s = s[:i] + frag + "\n\n" + s[i:]
    s = s.replace("**HEAD is PR 65.**", "**HEAD is PR 66.**", 1)
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
  'tests/test_a_cap_breach_needs_a_trade_behind_it.py',
  'tests/test_live_contradictions.py',
  'tests/test_backlog_fixes.py',
  'tests/test_rule_actions.py',
  'tests/test_rule_flap.py',
  'tests/test_p4_rules_and_drift.py',
  'tests/test_card_claims.py'
)
$have = @()
foreach ($g in $guards) { if (Test-Path (Join-Path $repo $g)) { $have += $g } }
if ($have.Count -lt 5) { throw 'the guard list did not resolve - check the test filenames' }
python -m pytest -q @have
if ($LASTEXITCODE -ne 0) { throw 'the rule guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$msg = @'
PR 66: a cap breach needs a trade behind it

Eran, on two cards he could not act on: "why do I still getting guidance cards?
all these cards should be actionable", and then precisely: "1. it doesn't tell
me what to do (how much to sell) 2. it doesn't tell me what do do instead (buy
something else or keep it in cash) 3. it doesn't do the action for me".

He was right that something was wrong, and it is not the missing button.

WHAT THE NUMBERS SAID, measured on the live book the same minute:

  SOXL  38.002% against a 38% cap  ->  0.44 shekels to trim
  QQQ   37.004% against a 37% cap  ->  0.87 shekels to trim

Both fired, both latched, and both rendered a card whose own how line read "There's
no single obvious trade here, so the app won't guess one." The Rules tab said it
in as many words: "Triggered - 37.0% of book vs a 37% cap". The cards were
guidance because there was NOTHING TO TRADE.

THE CAUSE IS STRUCTURAL, AND IT IS OURS.

  1. _arm_sleeve_caps arms a max_weight cap at EXACTLY the sleeve's target. Both
     rules carry _cap_note's wording, so both are sleeve-armed.
  2. _fund_sleeve funds the sleeve to EXACTLY that target.
  3. _evaluate fired on weight >= level - AT EQUALITY.

So every correctly funded sleeve tripped its own cap the moment it was funded.
QQQ's card exists because PR 61 worked: Eran tapped "Finish the move", the
sleeve funded to 37%, and the cap armed at 37% fired on it.

Then execution_plan refused to name a trade - twice over, since weight_pct is
rounded to one decimal so 38.0 <= 38.0, and its own MIN_TRADE_ILS guard would
have refused 0.44 anyway. And clears_with_margin wanted SOXL down at 37.05%
before it would re-arm: a nag with no exit, which is the exact phrase the
previous re-arm fix used about the last one.

THE FIX, IN THREE PARTS.

A cap breach is a breach when there is a trade behind it. MIN_TRADE_ILS rather
than a new tolerance band - the app already has a number for "not worth the
friction" and it governs every other sizing decision here. execution_plan has
applied this test since the MSFT case, one layer too late, where it could only
downgrade the card instead of preventing it. cap_trim_ils is now the single
answer the firing test, the re-arm test and execution_plan all read.

The amount is in shekels, net of tax, through the function Accept runs. The card
computed shares * price - the position's NATIVE currency, no FX and no tax,
rendered without a symbol on a screen where every other number is shekels. On
SOXL at $117.28 a six-share trim reads "~737" while Accept credits about 2,700
to cash. The same defect as _rebalance_to's "sell 4,324" against a 12,139
execution, one order of magnitude quieter. It now calls _sale_value_ils, the
same function the apply handler uses.

And it says where the money lands. A trim credits cash, so the card names the
net proceeds, the resulting cash percentage against the 7.5% target, and which
side of it that falls on - above, and the redeploy card will size where it goes;
at or under, and it is dry powder doing its job. Deliberately NOT a replacement
buy inside the same card: that is a second sizing decision inside a card that
already has one, which is the shape PR 60 fixed when two cards wanted the same
7,476.

TWO EXISTING TESTS CAUGHT THE CHANGE, AND BOTH WERE FAIR.
test_the_index_carries_an_ils_value_not_a_native_one grepped _positions_index,
whose body moved into _positions_index_and_nav - the cap card needs the NAV the
index was measured against, and loading the book twice to get it is how two
numbers on one screen come to disagree. The guard follows the body and now pins
the delegation. test_a_cap_that_has_corrected_itself_stops_nagging built its
fixture as "1 unit at 200 in a 2,000 book = 10% exactly -> breach" - a breach of
zero shekels, used as a convenient way to get a rule into a triggered state. Its
subject is the RE-ARM, so the fixture now starts from a breach worth acting on.

tests/test_a_cap_breach_needs_a_trade_behind_it.py - 12 tests, 4 confirmed RED
against 2edba83.

Gates: 1774 passed on SQLite, 1774 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/rules_service.py app/services/portfolio_analytics.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_a_cap_breach_needs_a_trade_behind_it.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_backlog_fixes.py tests/test_live_contradictions.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr66-a-cap-breach-needs-a-trade-behind-it.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

foreach ($tmp in @('PR66_STATUS.md', 'pr66.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 6. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

if ($haveKey) {
  Write-Host '=== 7. the two cards, after ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $caps = @($r2.recommendations | Where-Object { $_.id -like 'rule_*' -and $_.action -like '*cap*' })
  if ($caps.Count -eq 0) {
    Write-Host '  gone. Both breaches were worth under a shekel, so there was' -ForegroundColor Green
    Write-Host '  nothing to act on and the cards no longer claim otherwise.' -ForegroundColor Green
  } else {
    foreach ($c in $caps) {
      $k = 'none'
      if ($c.apply) { $k = $c.apply.kind }
      Write-Host ("  {0}" -f $c.title)
      Write-Host ("    action: {0}" -f $c.action)
      Write-Host ("    apply : {0}" -f $k)
      foreach ($h in $c.how) { Write-Host ("      - {0}" -f $h) }
      if ($k -eq 'none') {
        Write-Host '    STILL GUIDANCE. Check the deploy SHA first.' -ForegroundColor Red
      }
    }
  }

  Write-Host ''
  Write-Host '=== 8. and the rules themselves ===' -ForegroundColor Cyan
  $rules = Invoke-RestMethod -Uri "$base/api/v1/rules" -Headers $apiHeaders -TimeoutSec 60
  foreach ($c in @($rules.rules | Where-Object { $_.rule_type -eq 'max_weight' })) {
    $t = 'armed'
    if ($c.triggered) { $t = 'TRIGGERED' }
    Write-Host ("  {0,-8} cap {1,5:N1}%   [{2}]" -f $c.ticker, $c.level, $t)
  }
}

Write-Host ''
Write-Host 'PR 66 SHIPPED.' -ForegroundColor Green
