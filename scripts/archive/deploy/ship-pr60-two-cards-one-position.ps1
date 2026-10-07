# ship-pr60-two-cards-one-position.ps1
# PR 60 - two cards wanted the same 7,476, and the headline understated one by
# 17x. Run after PR 59.1.
#
# WHAT WAS ON SCREEN after the B2 swap:
#
#   "Bring your core back to Classic 60/40"   [The app can do this for you]
#     Sell all TQQQ - about 7,476 (not in the core's basket)
#     Buy VTI - about 422
#     Headline: "Rebalance about 422 inside your own book - no new money."
#
#   "You chose a 33% QQQ sleeve and hold none of it"        [Guidance]
#     Closing it means moving about 7,238 into QQQ.
#
# Same money, two destinations, neither card aware of the other.
#
# WHY TQQQ WAS "IN THE CORE". The core is the REMAINDER - everything no sleeve
# claims. PR 59 swapped btm_swing_dip for btm_qqq_hold, so nothing claimed TQQQ,
# so it fell into the core region, so rule 1 of plan_realign said "not in the
# core's basket, sell it in full". Every step correct, conclusion wrong: retire
# the overlay and hold the base means the money goes to QQQ.
#
# The collision was real. _fund_sleeve excludes only tickers a sleeve CURRENTLY
# claims, so the sleeve's own funding plan was free to sell the same TQQQ.
#
# THE HEADLINE. _moved read buys_ils - only what the basket's legs were short -
# so a card whose step 1 sold 7,476 was titled "Rebalance about 422". Every leg
# was honest; the sentence on top was not. And nothing said where the other
# ~7,040 went: it became cash, about a third of the book.
#
# Reproduced before fixing, and the numbers match the screen exactly:
#   sells 7476.0   buys 422.4   tax 14.02   to_cash 7039.58
#
# WHAT TO EXPECT AFTER: the core card stops offering to sell TQQQ while the QQQ
# sleeve is unfunded, and says so; if it ever does sell into surplus, the cash
# gets its own line. Step 5 shows the cards before and after.
#
# THIS DOES NOT YET GIVE YOU THE ONE-TAP SWAP. That is the rest of item 4 -
# the sleeve target following its signal, and a single card that moves TQQQ to
# QQQ. This stops the app proposing the WRONG destination.
#
# Gates already run on these exact bytes: 1710 SQLite, 1710 PostgreSQL on a
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
  Write-Host '=== 0b. what the core card says right now ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  foreach ($c in ($r.recommendations | Where-Object { $_.title -like 'Bring your core*' })) {
    Write-Host ("  {0}" -f $c.title)
    Write-Host ("    action: {0}" -f $c.action)
    foreach ($h in $c.how) { Write-Host ("    - {0}" -f $h) }
  }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the card-claim guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_two_cards_do_not_want_the_same_money.py tests/test_card_claims.py tests/test_a_core_is_a_strategy_too.py tests/test_core_choice.py tests/test_accept_conserves_nav.py
if ($LASTEXITCODE -ne 0) { throw 'the card guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 60: two cards wanted the same 7,476, and the headline understated one by 17x

Eran, looking at Today after the B2 swap: "I don't understand these cards...
why is TQQQ part of the core and not the sleeves?"

Two cards were on screen at once. One offered to sell all 7,476 of TQQQ into the
core's Classic 60/40 basket, headlined "Rebalance about 422 inside your own
book". The other said the 33% QQQ sleeve held nothing and needed about 7,238.
Same money, two destinations, neither card aware of the other.

WHY TQQQ WAS "IN THE CORE". The core is the REMAINDER - PlanSleeve's docstring
says it, "the core is the implicit remainder, not a row". PR 59 swapped
btm_swing_dip for btm_qqq_hold, so nothing claimed TQQQ any more, so it fell
into the core region, so rule 1 of plan_realign said "not in the core's basket,
sell it in full". Every step correct and the conclusion wrong: "retire the
overlay, hold the base" means the money goes to QQQ, not to VTI and cash.

The collision was real rather than theoretical. _fund_sleeve builds
exclude = set(targets) | sleeve_tickers(...) - only tickers a sleeve CURRENTLY
claims. TQQQ was no longer one, so the sleeve's own funding plan was free to
sell exactly the same position.

THE HEADLINE WAS WRONG BY 17x.

    _moved = float(_plan.get("buys_ils") or 0.0)   # = 422

buys_ils is only what the basket's legs are SHORT. Step 1 sold 7,476. Every
individual leg was honest - describe() listed them - but the sentence on top of
them was not, and est_amount feeds the mute, re-fire and merge logic, so the
card was sized as a small one.

AND NOTHING SAID WHERE THE REST WENT. Sells 7,476, buys 422, tax 14 - about
7,040 becoming cash, roughly a third of the book, with no line on the card.
describe() was written as the card-claim invariant in one function and listed
every LEG; a CONSEQUENCE of the legs, at that size, is a change the reader has
to be told about too.

Reproduced exactly before fixing: sells 7476.0, buys 422.4, tax 14.02, to_cash
7039.58 - the same three numbers that were on the screen.

WHAT SHIPS.

  * to_cash_ils on the plan, and a describe() line naming it.
  * deferred on the plan: holdings the caller pulled out of the core region,
    WITH THE REASON, so the card says what it is deliberately not touching.
    Silence there reads as "the app did not notice", which is how two cards came
    to propose two destinations for one position.
  * _core_plan reserves unclaimed non-basket holdings while any sleeve is
    under-funded. The tie breaks toward the sleeve because a sleeve size is a
    decision made on the Plan tab and the core is what is left over. Reserved
    WHOLE rather than sliced: the funding plan decides how much it needs, and a
    core card selling part of it first is the pre-emption being removed. A
    basket leg is never deferred - that is core money doing its job.
  * The headline reports the money that moves, and names the leftover cash.

STILL OPEN, and it is the rest of item 4: the sleeve's target does not follow
its signal, so a swing card cannot be actionable yet, and there is no single
card that says "move 7,476 from TQQQ to QQQ" in one tap. This PR stops the app
proposing the wrong destination; it does not yet propose the right one in one
action.

Gates: 1710 passed on SQLite, 1710 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean. Three of the four new assertions
were confirmed RED against the pre-fix tree.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/core_realign.py app/services/recommendations.py
git add -- tests/test_two_cards_do_not_want_the_same_money.py
git add -- STATUS.md scripts/deploy/ship-pr60-two-cards-one-position.ps1
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
  Write-Host '=== 5. what the core card says now ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $core = @($r2.recommendations | Where-Object { $_.title -like 'Bring your core*' })
  if ($core.Count -eq 0) {
    Write-Host '  the core card is gone: with TQQQ reserved for the unfunded sleeve,' -ForegroundColor Green
    Write-Host '  the core has nothing left to correct.' -ForegroundColor Green
  } else {
    foreach ($c in $core) {
      Write-Host ("  action: {0}" -f $c.action)
      foreach ($h in $c.how) { Write-Host ("    - {0}" -f $h) }
    }
    $sellsTqqq = @($core | Where-Object { ($_.how -join ' ') -match 'Sell all TQQQ' })
    if ($sellsTqqq.Count -gt 0) {
      Write-Host '  STILL SELLING TQQQ. The deferral did not take - check the deploy SHA.' -ForegroundColor Red
    } else {
      Write-Host '  it is no longer selling TQQQ into the core basket' -ForegroundColor Green
    }
  }
}

Write-Host ''
Write-Host 'PR 60 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: the rest of item 4 - the sleeve target follows its signal, and'
Write-Host '  one card that moves TQQQ to QQQ in a single tap.'
