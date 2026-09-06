# ship-pr60-1-the-key-that-was-never-there.ps1
# PR 60.1 - the deferral shipped dead, and finding out why found something
# worse. Run after PR 60 (d3fa4c8).
#
# PR 60's step 5 asked production and said: STILL SELLING TQQQ. The headline fix
# and the cash line had landed - the card read "Rebalance about 7,476" with
# "About 7,041 of the proceeds would stay as cash" - but the deferral never
# fired.
#
# MY BUG. sleeve_occupancy emits target_pct. I read sleeve_pct, which is not a
# key on that dict, so want/gap was 0.0 for every sleeve, unfunded_ils came to
# 0.00 on a book with a 33% sleeve holding nothing, and the deferral was
# skipped. Green tests, dead feature: the tests asserted plan_realign's
# behaviour GIVEN a deferred list, never that _core_plan produced one.
#
# THE ONE IT LED TO IS WORSE, AND IT PREDATES THIS WORK. The loop directly above
# mine makes the same mistake:
#
#     want_pct = float(s.get("sleeve_pct") or 0.0)     # always 0.0
#     if held_pct > want_pct:
#         over += (held_pct - want_pct) / 100.0 * nav  # the ENTIRE holding
#
# The 'over' value is sleeve_over_ils - the money core_realign may reclaim
# sleeves. Its docstring promises "It will not trim a sleeve BELOW its own
# target... may reclaim what is over that decision and may not overrule it".
# With want_pct pinned at zero, every shekel in every sleeve counted as
# overshoot: SOXL's whole 7,686 position on the live book. The guarantee was
# vacuous. It has never bitten because that funding path only engages when buys
# exceed sells plus spendable cash, which this book has not hit.
#
# AND gap_pct WAS IN TWO UNITS AT ONCE. target_pct is 0-100; held_pct is a
# fraction, a convention the UI documents at index.html:1579. gap_pct was
# target_pct - held_pct, so a sleeve at 33.9% target holding 35.09% of the book
# reported +33.54 - reading as a third of the book under-funded when it is 1.2
# points OVER.
#
#                        before                          after
#   unfunded_ils         0.00                            7,247
#   sleeve_over_ils      7,684.71 (SOXL's whole pos)      0.00
#   gap_pct for SOXL     +33.54                          -1.19
#
# WHAT TO EXPECT AFTER: the core card should stop offering to sell TQQQ, and say
# what it is deferring instead. Step 5 checks it against production.
#
# Gates already run on these exact bytes: 1712 SQLite, 1712 PostgreSQL on a
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
  Write-Host '=== 0b. what the sleeves payload reports now ===' -ForegroundColor Cyan
  $sl = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl.sleeves) {
    Write-Host ("  {0,-16} target {1,6:N2}%  held {2,6:N2}%  gap {3,7:N2}" -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), $s.gap_pct)
  }
  Write-Host '  A sleeve held ABOVE its target should show a NEGATIVE gap.'
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the unit guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_two_cards_do_not_want_the_same_money.py tests/test_plan_sleeves.py tests/test_sleeve.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'the sleeve guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 60.1: the deferral shipped dead, and finding out why found something worse

PR 60's step 5 asked production and printed "STILL SELLING TQQQ. The deferral
did not take." The headline fix and the cash line had landed - the card read
"Rebalance about 7,476" with "About 7,041 of the proceeds would stay as cash" -
but the deferral itself never fired.

MY BUG. sleeve_occupancy emits target_pct. I read sleeve_pct, which is not a key
on that dict, so float(s.get("sleeve_pct") or 0.0) was 0.0 for every sleeve,
unfunded_ils came to 0.00 on a book with a 33% sleeve holding nothing, and the
deferral was skipped. Green tests, dead feature: the tests asserted
plan_realign's behaviour GIVEN a deferred list, and never that _core_plan
produced one.

THE ONE IT LED ME TO IS WORSE, AND IT PREDATES THIS WORK. The loop directly
above mine makes the same mistake:

    want_pct = float(s.get("sleeve_pct") or 0.0)     # always 0.0
    if held_pct > want_pct:
        over += (held_pct - want_pct) / 100.0 * nav  # so: the ENTIRE holding

The 'over' value is sleeve_over_ils - the money core_realign may reclaim from
the sleeves. Its docstring promises "It will not trim a sleeve BELOW its own
target. The sleeves' sizes are a decision the user made on the Plan tab; this
may reclaim what is over that decision and may not overrule it." With want_pct
pinned at zero, every shekel in every sleeve counted as overshoot - measured on
the live book, SOXL's whole 7,686 position. The guarantee was vacuous.

It has never bitten because that funding path only engages when the buys exceed
sells plus spendable cash, which this book has not hit. On an under-funded core
it would have sold a whole sleeve.

AND gap_pct WAS IN TWO UNITS AT ONCE. target_pct is 0-100; held_pct is a
fraction, a convention the UI documents at index.html:1579. gap_pct was
target_pct - held_pct, so a sleeve at a 33.9% target holding 35.09% of the book
reported +33.54: reading as a third of the book under-funded when it is 1.2
points OVER. Wrong by the whole held fraction, and always in the direction of
"you hold nothing".

                       before                          after
  unfunded_ils         0.00                            7,247
  sleeve_over_ils      7,684.71 (SOXL's whole pos)      0.00
  gap_pct for SOXL     +33.54                          -1.19

THE LESSON WORTH KEEPING: the deploy check found this, not the suite. A step 5
that asks production what it is actually saying is worth more than a green test
asserting the layer below the bug - twice now, PR 56.1's leftover key and this.

Gates: 1712 passed on SQLite, 1712 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/sleeve_service.py app/services/recommendations.py
git add -- tests/test_two_cards_do_not_want_the_same_money.py
git add -- STATUS.md scripts/deploy/ship-pr60-1-the-key-that-was-never-there.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

if ($haveKey) {
  Write-Host '=== 5. does the deferral take this time ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $core = @($r2.recommendations | Where-Object { $_.title -like 'Bring your core*' })
  if ($core.Count -eq 0) {
    Write-Host '  the core card is gone: with TQQQ reserved for the unfunded sleeve,' -ForegroundColor Green
    Write-Host '  the core has nothing left to correct. That is the intended outcome.' -ForegroundColor Green
  } else {
    foreach ($c in $core) {
      Write-Host ("  action: {0}" -f $c.action)
      foreach ($h in $c.how) { Write-Host ("    - {0}" -f $h) }
    }
    if ((($core | ForEach-Object { $_.how -join ' ' }) -join ' ') -match 'Sell all TQQQ') {
      Write-Host '  STILL SELLING TQQQ. Check the deploy SHA before anything else.' -ForegroundColor Red
    } else {
      Write-Host '  no longer selling TQQQ into the core basket' -ForegroundColor Green
    }
  }

  Write-Host ''
  Write-Host '=== 6. the sleeve gaps, in one unit ===' -ForegroundColor Cyan
  $sl2 = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl2.sleeves) {
    Write-Host ("  {0,-16} target {1,6:N2}%  held {2,6:N2}%  gap {3,7:N2}" -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), $s.gap_pct)
  }
}

Write-Host ''
Write-Host 'PR 60.1 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: the one-tap TQQQ -> QQQ card, and the sleeve target following its signal.'
