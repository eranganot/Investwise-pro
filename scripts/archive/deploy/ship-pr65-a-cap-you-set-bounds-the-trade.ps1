# ship-pr65-a-cap-you-set-bounds-the-trade.ps1
# PR 65 - V5's "PR C" was two claims, and one of them was wrong.
# Run AFTER PR 64. It refuses to start otherwise and applies its own patch.
#
# The row has been carried since V5 on 2026-09-02, re-verified into V6 and V7 as
# "the veto and the cap that nothing reads", the oldest untouched item on the
# board. Both halves were finally measured against the running code.
#
# CLAIM A - "nothing on the live path stops a card buying into an overweight
# class" - IS FALSE. AllocationAgent.review_buy really is reachable only from
# pipeline.py:102 / POST /decision-feed/generate. But the live card path does not
# need it, because propose_funded_buy enforces a STRICTER rule of its own.
# Measured 2026-09-06 on a book 100% in Equities against a Grow target:
#
#   buy into Equities      -> REFUSED (None)
#   buy into Fixed Income  -> card for 1,500
#
# review_buy asks one question: is this class overweight. propose_funded_buy asks
# three - does the trade move the class TOWARD its target, is it a within-class
# swap that must be LABELLED as one, and does TOTAL BOOK DRIFT get worse. The
# weaker check is the one on the quieter path.
#
# So the fix is not the one three plans proposed. Wiring review_buy into Today
# would add a second, laxer implementation of a rule already enforced - the exact
# shape this codebase has spent PRs removing. review_buy stays where it is, and
# the guarantee the live path gives is now pinned by a test.
#
# CLAIM B - "a hand-set cap plays no part in what gets trimmed" - IS TRUE, AND
# WORSE THAN TRIMMING. It played no part in BUYING either, and the buy is what
# creates the breach. Measured with NVDA at 10%, a hand-set 12% cap, and Equities
# under its Grow target so the purchase was otherwise legitimate:
#
#                                       before              after
#   sized buy                           4,500 -> 40.0%      300 -> 12.0%
#   same position at 20%, for trimming  3rd, "no drift"     1st, "above your
#                                                            own cap on it"
#
# Bounded only by the plan-wide concentration_cap, twenty-eight points past the
# limit the user set - and the max_weight rule then fired a card telling him the
# cap had been breached. A cap that alerts after the trade it could have
# prevented is not a cap.
#
# THE FIX. rules_service.name_caps loads ticker -> cap as a FRACTION from active
# max_weight rules (the column is a percentage for this rule type; the /100
# happens once, there). funding_service.effective_name_cap returns the TIGHTER of
# the plan-wide cap and the ticker's own - neither may relax the other - and it
# is read by name_room_ils on the buy side, by name_over and the name_allow
# budget on the sell side, and by the three redeploy sizings.
#
# Threaded like prefer (PR 61.1) and live (PR 63): one read at the TOP of the
# builder, handed to every producer. At the top deliberately - the first version
# loaded it beside the screener and left the geographic card still reading the
# plan-wide cap alone.
#
# Only ACTIVE rules. A disabled cap is a cap the user switched off, and a
# constraint that outlives its own switch is worse than no constraint.
#
# THE LESSON: this row survived three re-verification passes and half of it was
# wrong the whole time. Each pass confirmed that review_buy has one caller, which
# is true, and inferred from that what the live path does, which is not the same
# thing. grep proves where a function is called; only running the other path
# proves what it does instead.
#
# Gates on these exact bytes: 1762 SQLite, 1762 PostgreSQL on a dropped and
# recreated database, ruff clean. 3 of 8 new tests confirmed RED against
# 8292c89; the other five pin behaviour that already holds.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

Write-Host '=== 0. PR 64 must be in first ===' -ForegroundColor Cyan
$log = (git log --oneline -3 | Out-String)
if ($log -notmatch 'PR 64') {
  throw 'PR 64 is not in the last three commits. Ship it first - pr65.patch is cut against its tree.'
}
git rev-parse --short HEAD
git status --short

Write-Host '=== 0b. the caps you have set right now ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  $rules = Invoke-RestMethod -Uri "$base/api/v1/rules" -Headers $apiHeaders -TimeoutSec 60
  $caps = @($rules.rules | Where-Object { $_.rule_type -eq 'max_weight' })
  if ($caps.Count -eq 0) {
    Write-Host '  none. This PR changes nothing on your book until you set one.'
  } else {
    foreach ($c in $caps) {
      $state = 'active'
      if (-not $c.active) { $state = 'disabled' }
      Write-Host ("  {0,-8} max_weight {1,6:N1}%   [{2}]" -f $c.ticker, $c.level, $state)
    }
  }
}

Write-Host '=== 1. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr65.patch'
if (-not (Test-Path $patch)) { throw 'pr65.patch is not in the repo root' }
$already = (Select-String -Path 'app\services\funding_service.py' -Pattern 'effective_name_cap' -Quiet)
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
frag = io.open("PR65_STATUS.md", encoding="utf-8").read().rstrip("\n")
s = io.open("STATUS.md", encoding="utf-8").read()
if "PR 65 " in s.split("## PR 64")[0]:
    print("already present")
else:
    anchor = "## PR 64 "
    i = s.index(anchor)
    s = s[:i] + frag + "\n\n" + s[i:]
    s = s.replace("**HEAD is PR 64.**", "**HEAD is PR 65.**", 1)
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
  'tests/test_a_cap_you_set_bounds_the_trade.py',
  'tests/test_funding_service.py',
  'tests/test_the_cash_target_is_soft_and_the_floor_is_not.py',
  'tests/test_rule_actions.py',
  'tests/test_p4_rules_and_drift.py',
  'tests/test_the_agent_roster_is_what_actually_runs.py',
  'tests/test_card_claims.py'
)
$have = @()
foreach ($g in $guards) { if (Test-Path (Join-Path $repo $g)) { $have += $g } }
if ($have.Count -lt 4) { throw 'the guard list did not resolve - check the test filenames' }
python -m pytest -q @have
if ($LASTEXITCODE -ne 0) { throw 'the cap guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$msg = @'
PR 65: a cap you set bounds the trade

V5's "PR C - the veto and the cap that nothing reads" has been carried since
2026-09-02 and re-verified into V6 and V7 as the oldest untouched item on the
board. Both halves were finally measured against the running code, and they did
not survive equally.

CLAIM A - "nothing on the live path stops a card buying into an overweight
class" - IS FALSE. AllocationAgent.review_buy really is reachable only from
pipeline.py:102 / POST /decision-feed/generate. But the live card path does not
need it, because propose_funded_buy enforces a STRICTER rule of its own.
Measured 2026-09-06 on a book 100% in Equities against a Grow target:

  buy into Equities      -> REFUSED (None)
  buy into Fixed Income  -> card for 1,500

review_buy asks one question: is this class overweight. propose_funded_buy asks
three - does the trade move the class TOWARD its target, is it a within-class
swap that must be LABELLED as one, and does TOTAL BOOK DRIFT get worse. The
weaker check is the one sitting on the quieter path.

So the fix is not the one three plans have proposed. Wiring review_buy into
Today would add a second, laxer implementation of a rule already enforced - the
exact shape this codebase has spent PRs removing (settings.objective, the
duplicate max_weight producers, _core_plan's one sizing and two callers).
review_buy stays where it is, guarding /decision-feed/generate, and the
guarantee the live path gives is now pinned by a test so it cannot regress into
being true.

CLAIM B - "a hand-set cap plays no part in what gets trimmed" - IS TRUE, AND IT
IS WORSE THAN TRIMMING. It played no part in BUYING either, and the buy is what
creates the breach. Measured on a book with NVDA at 10%, a hand-set 12%
max_weight rule, and Equities UNDER its Grow target so the purchase was
otherwise legitimate:

                                      before            after
  sized buy                           4,500 -> 40.0%    300 -> 12.0%
  same position at 20%, for trimming  3rd, "corrects    1st, "above your own
                                      no drift"          cap on it of 12%"

Bounded only by the plan-wide concentration_cap - twenty-eight points past the
limit the user set - and the max_weight rule then fired a card telling him the
cap had been breached. A cap that alerts after the trade it could have prevented
is not a cap.

THE FIX. rules_service.name_caps(session, user) loads ticker -> cap as a
FRACTION from active max_weight rules (trading_rules.level is a PERCENTAGE for
this rule type - _evaluate compares it to weight_pct directly - and the /100
happens once, there). funding_service.effective_name_cap(cap, ticker, name_caps)
returns the TIGHTER of the plan-wide cap and the ticker's own, because neither
may relax the other. It is read by name_room_ils on the buy side, by name_over
and the name_allow budget on the sell side, and by the three redeploy sizings.

Threaded like prefer (PR 61.1) and live (PR 63): one read at the TOP of the
builder, handed to every producer. At the top deliberately - the first version
loaded it beside the screener and left the geographic card at ~1650 still
reading the plan-wide cap alone.

Only ACTIVE rules. A disabled cap is a cap the user switched off, and a
constraint that outlives its own switch is worse than no constraint.

tests/test_a_cap_you_set_bounds_the_trade.py - 8 tests, 3 confirmed RED against
8292c89. The other five pin Claim A's guarantee and the loader's units, which is
the point: they pass today and their job is to keep passing.

THE LESSON WORTH KEEPING. This row survived three re-verification passes - V5
wrote it, V6 re-checked it and kept it, V7 called it the oldest untouched item -
and half of it was wrong the whole time. Each pass confirmed that review_buy has
one caller, which is true, and inferred from that what the live path does, which
is not the same thing. grep proves where a function is called; only running the
other path proves what it does instead.

Gates: 1762 passed on SQLite, 1762 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/rules_service.py app/services/funding_service.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- app/services/recommendations.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_a_cap_you_set_bounds_the_trade.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr65-a-cap-you-set-bounds-the-trade.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

foreach ($tmp in @('PR65_STATUS.md', 'pr65.patch')) {
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
  Write-Host '=== 7. do any cards name a cap now ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $hit = $false
  foreach ($c in $r.recommendations) {
    $txt = ($c.action + ' ' + ($c.how -join ' '))
    if ($txt -match 'own cap on it') { $hit = $true; Write-Host ("  {0}" -f $c.title) -ForegroundColor Green }
  }
  if (-not $hit) {
    Write-Host '  none, which is expected unless a position is over a cap you set.'
    Write-Host '  The change is preventive: it bounds the next buy rather than'
    Write-Host '  reporting the last one.'
  }
}

Write-Host ''
Write-Host 'PR 65 SHIPPED.' -ForegroundColor Green
Write-Host '  V7 said C was the oldest untouched item. Half of it was never true;'
Write-Host '  the other half is closed. STATUS carries both findings.'
