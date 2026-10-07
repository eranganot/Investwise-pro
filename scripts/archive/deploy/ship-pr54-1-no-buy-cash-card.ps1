# ship-pr54-1-no-buy-cash-card.ps1
# PR 54.1 - hotfix on PR 54. Two cards for one 972 ILS, and the wrong one could
# not do what it said. Run this next.
#
# MY DEFECT, introduced by PR 54 and live within minutes of it shipping. I
# predicted the "raise 972" card and did not check what ELSE renders an asset
# class. Putting a Cash key in the book target for the first time reached
# renderers that had never been asked to draw a class you cannot buy.
#
# The card that is RIGHT - liquidity, core scope:
#   "Raise 972 ILS of cash - Sell 4 BND (~972) to rebuild your cash floor."
#
# The card that is WRONG, twice over - allocation, book scope:
#   "Buy Cash - Buy about 971 ILS of Cash to move toward 8%"
#   how: "Buy Cash by ~971 - e.g. BIL"
#
#   1. BIL IS A T-BILL ETF. BUYING IT SPENDS CASH. CLASS_ETF["Cash"] = "BIL"
#      had sat unreachable in the source since it was written, because no book
#      target had ever carried a Cash key.
#   2. IT CANNOT DO WHAT IT CLAIMS. Its apply spec is
#      {"kind": "rebalance_to_objective", "asset_class": "Cash"} and
#      _rebalance_to drops cash rows from the classes it moves. Pressing Accept
#      reshuffles Equities and Fixed Income and moves the cash weight by
#      nothing. That is the card-claim invariant.
#
# Both quote the same 972 because PR 54 made the floor equal the target, so two
# generators measure the same gap against the same number.
#
# A SECOND ONE found while reproducing the first: _holding_verdict_recs rated
# the cash balance like a security - "CASH screens well (fundamentals 80/100)".
# It has never reached production only because Yahoo returns no fundamentals
# for CASH, so the loop skips it BY ACCIDENT. An accident is not a guard.
#
# The Cash target still reaches AllocationEngine.compute, and must - it is what
# shrinks the investable legs. What is dropped is the CARD.
#
# Gates already run on these exact bytes: 1655 SQLite, 1655 PostgreSQL, ruff
# clean.
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
  Write-Host '=== 0b. the cards as they stand ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 120
  foreach ($c in $r.recommendations) { Write-Host ("  [{0}] {1}" -f $c.dimension, $c.title) }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard, the card-claim suite, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_a_cash_target_does_not_produce_a_buy_cash_card.py tests/test_card_claims.py tests/test_the_book_can_hold_cash_on_purpose.py
if ($LASTEXITCODE -ne 0) { throw 'the card guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 54.1: no card may offer to buy cash

A defect PR 54 introduced, found on production 6044124 within minutes of it
shipping. I predicted the "raise 972" card and did not check what else renders
an asset class. Putting a Cash key in the book target for the first time
reached renderers that had never been asked to draw a class you cannot buy.

Two cards for one 972 ILS, instructing opposite things.

RIGHT - liquidity, core scope: "Raise 972 ILS of cash - Sell 4 BND (~972) to
rebuild your cash floor. cheapest way to raise it: Fixed Income is the most
overweight class (11% against an 8% target) and BND is among its least-taxed
positions."

WRONG - allocation, book scope: "Buy Cash - Buy about 971 ILS of Cash to move
toward 8%", how: "Buy Cash by ~971 - e.g. BIL". Wrong twice over:

  1. BIL is a T-bill ETF. Buying it SPENDS cash. The card's stated purpose is
     to raise the cash weight. CLASS_ETF["Cash"] = "BIL" had sat in the source
     unreachable since it was written, because no book target had ever carried
     a Cash key.
  2. It cannot do what it claims even in principle. Its apply spec is
     {"kind": "rebalance_to_objective", "asset_class": "Cash"}, and
     _rebalance_to drops cash rows from the classes it moves - with a comment
     ending "Turning cash into securities is a purchase and belongs to the
     funded-buy path." Pressing Accept reshuffles Equities and Fixed Income and
     moves the cash weight by nothing. That is the card-claim invariant.

Both quote the same 972 because PR 54 made the floor equal the target, so two
independent generators measure the same gap against the same number.

A second one, found in the sandbox while reproducing the first:
_holding_verdict_recs walked every row and rated the cash balance like a
security - "CASH screens well (fundamentals 80/100) and is only 1% of your book
- adding on weakness is reasonable". It has never reached production only
because Yahoo returns no fundamentals for CASH, so the score is None and the
loop skips it BY ACCIDENT. An accident is not a guard, and the day the provider
changes it is a card telling you to buy more of your own bank balance.

The Cash target still reaches AllocationEngine.compute, and must: it is what
shrinks the investable legs. What is dropped is the CARD, because raising cash
already has an owner that does it properly - the liquidity card names the
ticker, sizes the lot and explains the tax choice, none of which the drift
renderer can do for a class you cannot buy.

The first version of the test PASSED AGAINST THE BUG, and that is worth
recording. It asserted on apply.asset_class, and _reconcile had merged four
legs into one card whose top-level class was Equities, with "Buy Cash (~4,271)"
sitting inside apply.legs and meta.merged_legs. A guard that reads only the
summary of a merge does not guard the merge. The shipped version walks the legs.

The RED gate was taken with PR 54 in place and only these two guards removed,
so the failure is the defect rather than a missing column:

    'Rebalance toward your target mix': ['legs: buy 4270.95',
                                         'merged_legs: Buy Cash']
    'Buy more: CASH': 'CASH screens well (fundamentals 80/100)...'

Gates: 1655 passed on SQLite, 1655 on PostgreSQL, ruff check app tests alembic
clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py tests/test_a_cash_target_does_not_produce_a_buy_cash_card.py
git add -- STATUS.md scripts/deploy/ship-pr54-1-no-buy-cash-card.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# -------------------------------------------------------------------- smoke
if ($haveKey) {
  Write-Host '=== 5. the cards now ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 120
  foreach ($c in $r2.recommendations) { Write-Host ("  [{0}] {1}" -f $c.dimension, $c.title) }

  $buyCash = @($r2.recommendations | Where-Object { $_.title -match '(?i)buy .*cash' })
  if ($buyCash.Count -gt 0) { throw "a Buy Cash card is still there: $($buyCash[0].title)" }

  $raise = @($r2.recommendations | Where-Object { $_.title -match '(?i)raise .*cash' })
  if ($raise.Count -lt 1) {
    Write-Host '  NOTE: the raise-cash card is gone too. That is NOT expected -' -ForegroundColor Yellow
    Write-Host '  the fix was meant to remove one card, not both. Look before' -ForegroundColor Yellow
    Write-Host '  accepting anything.' -ForegroundColor Yellow
  } else {
    Write-Host ("  the honest one survived: {0}" -f $raise[0].title) -ForegroundColor Green
  }
}

Write-Host ''
Write-Host 'PR 54.1 SHIPPED.' -ForegroundColor Green
Write-Host '  One card for the 972, and it is the one that names a ticker.'
