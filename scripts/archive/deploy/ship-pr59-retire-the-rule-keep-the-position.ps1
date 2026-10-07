# ship-pr59-retire-the-rule-keep-the-position.ps1
# PR 59 - B2. Retiring a rule and retiring an allocation stop being the same
# button. Run after PR 58 (50e155f).
#
# THE MEASUREMENT, shipped engine, 2016-09-06 to 2026-09-04, 2514 sessions:
#
#   variant                     CAGR     vol     maxDD   in TQQQ  trades/yr
#   hold TQQQ, do nothing      40.25%   66.74%   81.66%    100%       0.1
#   hold QQQ, do nothing       20.73%   22.56%   35.12%      0%       0.1
#   btm_swing_dip as shipped   17.19%   34.35%   40.55%   15.6%      45.4
#
# The overlay turned a 40.25% instrument into 17.19% - BELOW simply holding the
# base - while carrying a DEEPER drawdown than the base and paying ~7 points a
# year to 45 round-trips. Leverage decay was not the cause; being out of the
# market during the compounding was.
#
# WHY THAT NEEDED CODE. DELETE /plan/sleeves/btm_swing_dip retires the whole
# SLEEVE: 36.05% collapses into the core, the core goes 27% -> ~55%, and the
# book's Fixed Income target swings 7.8% -> ~22%, after which the app starts
# asking to sell equities into BND. Dropping a RULE and dropping an ALLOCATION
# were one operation and only one of them was ever wanted. So:
#
#   btm_qqq_hold  - a catalogue entry that holds the index and does nothing
#                   else. No 'base' key, and that is the definition: a base is
#                   where a rule goes when it is off, and there is no rule.
#                   Needs no engine support - a spec with no overlay already
#                   falls through to buy_hold.
#   swap_sleeve   - one operation that moves the size across. The order is
#                   forced (adding first would be 109% and refused) and the
#                   two-step leaves a window where the core is oversized.
#
# NOTHING IS BOUGHT OR SOLD BY THE SWAP. It changes what the app steers toward.
# Afterwards TQQQ is a holding no sleeve wants and QQQ is unfunded, and the
# existing cards say both - which is how you then execute it.
#
# THIS PR ONLY SHIPS THE CAPABILITY. It does not touch your book. Step 5 shows
# you the command to run when you want to pull the trigger, and shows what your
# sleeves look like before and after so you can decide.
#
# Gates already run on these exact bytes: 1703 SQLite, 1703 PostgreSQL on a
# dropped and recreated database, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) { $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() } }

if ($haveKey) {
  Write-Host '=== 0b. your sleeves before ===' -ForegroundColor Cyan
  $sl = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl.sleeves) {
    Write-Host ("  {0,-22} target {1,6:N2}%  held {2,6:N2}%  {3}" -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), ($s.tickers -join ','))
  }
  Write-Host ("  core (the remainder)   {0,6:N2}%" -f $sl.core_pct)
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the B2 guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_a_rule_can_be_retired_without_dropping_the_position.py tests/test_card_claims.py tests/test_plan_sleeves.py
if ($LASTEXITCODE -ne 0) { throw 'the sleeve guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 59: retiring a rule and retiring an allocation stop being the same button

B2. Eran decided this on 2026-09-04 and parked execution. Executing it needed
two things the app did not have.

THE MEASUREMENT, on the shipped engine over 2016-09-06 to 2026-09-04, 2514
sessions of adjusted closes:

  variant                     CAGR     vol     maxDD   in TQQQ  trades/yr
  hold TQQQ, do nothing      40.25%   66.74%   81.66%    100%       0.1
  hold QQQ, do nothing       20.73%   22.56%   35.12%      0%       0.1
  btm_swing_dip as shipped   17.19%   34.35%   40.55%   15.6%      45.4

The overlay turned a 40.25% instrument into 17.19% - below simply holding the
base - while carrying a DEEPER drawdown than the base (40.55% against 35.12%)
and paying roughly 7 points a year to 45 round-trips. Gross of costs it makes
24.11%, so even before fees it trails holding TQQQ by 16 points. Leverage decay
was not the cause. Being out of the market during the compounding was.

WHY "DROP THE RULE, KEEP THE POSITION" WAS NOT EXPRESSIBLE.
DELETE /plan/sleeves/btm_swing_dip retires the whole SLEEVE. On this book that
is 36.05% collapsing into the core, the core going 27% to ~55%, and the book's
Fixed Income target swinging 7.8% to ~22% - after which the app starts asking to
sell equities into BND. Dropping a rule and dropping an allocation were one
operation, and only one of them was ever wanted.

btm_qqq_hold. The catalogue had no way to say "hold the index" - every other
entry is a rule, so a retired overlay had nowhere to land. It declares NO base,
which is the definition rather than an omission: a base is where a rule goes
when it is off, and there is no rule. It is also what makes the sleeve fundable
at all, since sleeve_targets funds weights minus base - an entry naming QQQ in
both would ask the app to fund nothing and the sleeve would sit permanently
empty. The engine needed no new support: a spec with no overlay already falls
through to buy_hold, so it is measured by the same code path as everything
else, and backtestable() picks it up for the nightly refresh and the solver.

swap_sleeve. One operation that moves the size across. The order is forced:
validate measures the new sleeve against the FREE share of the book while the
old one still occupies its own, so adding first would put this book at 109% and
be refused. Done as two API calls it leaves a window in which the book is
under-allocated and the core oversized - harmless for seconds, except that
anything reading the book in that window (a refresh, a card, the 06:15 job)
sees an allocation nobody chose. Caps re-level ONCE, at the end, for the reason
retire_sleeve already documents: a row removed without re-levelling leaves a
live max_weight on a position now held for some other reason, and re-levelling
between the halves would arm caps against a book that exists for one statement.

It also checks the destination is a real strategy, which validate never did -
it only ever asked about size and duplication, so a typo'd id would have been
accepted as a sleeve for a strategy that does not exist, sized, capped and
funded against nothing. The refusal comes BEFORE the removal, so a typo cannot
cost you the sleeve you had.

NOTHING IS BOUGHT OR SOLD. The swap changes what the app steers toward, not
what is held. Afterwards TQQQ is a holding no sleeve wants and QQQ is unfunded,
and the existing cards already say both. Asserting otherwise would be the
failure test_card_claims exists to catch, so a test pins it directly.

A STALE COMMENT THE CHANGE SURFACED. sleeve_count.py read "Seven is the whole
backtestable() pool today, so MAX_SLEEVES only ever binds if that pool grows" -
with MAX_SLEEVES = 7 typed beneath it. The pool became eight, which is exactly
the condition that sentence described, and nothing would have noticed:
MAX_SLEEVES would have started binding, and a search that stops because it hit
a backstop reads as a search that converged, which is the distinction the whole
module exists to report. The number is now derived from the catalogue and the
test asserts the derivation rather than the digit.

Gates: 1703 passed on SQLite, 1703 on PostgreSQL against a dropped and
recreated database, ruff check app tests alembic clean. Eight of the nine new
assertions were confirmed RED against the pre-PR-59 tree; the ninth is the
derived-ceiling check, which passes on both sides by construction and fails
only when the two diverge.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/strategy_catalog.py app/services/sleeve_count.py
git add -- app/services/strategy_service.py app/api/routes/plan.py
git add -- tests/test_a_rule_can_be_retired_without_dropping_the_position.py
git add -- STATUS.md scripts/deploy/ship-pr59-retire-the-rule-keep-the-position.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# --------------------------------------------- the capability, not the act
if ($haveKey) {
  Write-Host '=== 5. the new entry is live ===' -ForegroundColor Cyan
  $cat = Invoke-RestMethod -Uri "$base/api/v1/strategies" -Headers $apiHeaders -TimeoutSec 90
  $hold = @($cat.strategies | Where-Object { $_.id -eq 'btm_qqq_hold' })
  if ($hold.Count -eq 1) {
    Write-Host '  btm_qqq_hold is in the catalogue' -ForegroundColor Green
  } else {
    Write-Host '  btm_qqq_hold is MISSING - check the deploy SHA' -ForegroundColor Red
  }

  Write-Host ''
  Write-Host '  YOUR BOOK IS UNCHANGED. This deploy shipped the capability.' -ForegroundColor Yellow
  Write-Host '  When you want to execute B2, run this:' -ForegroundColor Yellow
  Write-Host ''
  Write-Host '    $h = @{ ''x-agent-key'' = (Get-Content scripts\.agent-key -Raw).Trim() }'
  Write-Host ("    Invoke-RestMethod -Method Post -Headers " + '$h' + " -Uri '{0}/api/v1/plan/sleeves/btm_swing_dip/swap?to=btm_qqq_hold'" -f $base)
  Write-Host ''
  Write-Host '  That moves the 36.05% from the rule to holding QQQ. It sells and' -ForegroundColor Yellow
  Write-Host '  buys NOTHING. Afterwards Today will carry two cards: TQQQ is held' -ForegroundColor Yellow
  Write-Host '  by no sleeve, and the QQQ sleeve is unfunded. Those are the trade.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'PR 59 SHIPPED.' -ForegroundColor Green
Write-Host '  Still open: .\scripts\ops\ops-check.ps1 - AUTO_CREATE_TABLES, branch protection, VAPID.'
