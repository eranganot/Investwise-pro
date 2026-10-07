# ship-pr61-1-the-funder-sells-the-position-the-move-converts.ps1
# PR 61.1 - the card was right and the funding plan was wrong. Run after PR 61
# (446ceaa).
#
# Eran, minutes after PR 61 deployed: "the second card is still guidance only
# and not actionable!!"
#
# /health served 446ceaa, so not a stale deploy. Asking production for the plan
# behind that card is the whole diagnosis:
#
#   SELL VTI   2,925.31  | Equities is the most overweight class (81% vs 80%)
#                          and VTI is among its least-taxed positions
#   SELL TQQQ  4,145.14  | ... and TQQQ is among its least-taxed positions
#   from_cash    179.88
#
# The funder was not selling TQQQ alone. VTI does not step back to QQQ, so PR
# 61's rule correctly refused - to do something that would have been wrong
# anyway: selling 2,925 of core money doing its job to start a sleeve whose
# funding was sitting in the leveraged position the sleeve replaces.
#
# THE DEFECT IS THAT FUNDING IS RANKED ON FIT AND TAX. Right question when paying
# for a purchase out of whatever the book can best spare; wrong question when the
# purchase IS a conversion. rank_trim_candidates scores name_over*400 +
# class_over*100 + tax_friendliness*10, and with Equities barely over and TQQQ
# carrying the larger gain, VTI simply scores higher.
#
# AND MY REPRODUCTION IS WHAT HID IT. PR 61's fixture put TQQQ at 59% of the
# book, which trips the 25% single-name cap, so name_over dominated and TQQQ was
# picked alone. The live plan runs risk_tolerance High - concentration_cap 0.40 -
# and TQQQ sits at ~35%, UNDER its cap. That one setting is the difference
# between a ranking that picks the right leg by luck and one that does not.
#
#                       pre-fix                          post-fix
#   funding legs        VTI 3,420.00 + TQQQ 3,884.37     TQQQ only
#   the card            guidance, apply.kind none        actionable, fund_sleeve
#
# THE FIX IS IN THE FUNDER, NOT THE CARD. rank_trim_candidates and plan_funding
# gain a 'prefer' set: tickers that rank ahead of every fit-and-tax term, are
# never dropped for scoring zero, are eligible on their own terms for the same
# reason buying_class is, and carry their own reason. _fund_sleeve fills it with
# the holdings for which strategy_catalog.steps_back_to(held, sleeve) holds - so
# one rule now decides BOTH whether the card may act and which position the
# funder sells, and the card can no longer be handed a plan it must refuse.
#
# WHAT TO EXPECT AFTER: the QQQ card should read "Finish the move: sell TQQQ,
# hold QQQ" with apply.kind = fund_sleeve. Step 4 asks production and prints it.
# IT DOES NOT ACCEPT IT - that is your tap.
#
# Gates on these exact bytes: 1723 SQLite, 1723 PostgreSQL on a dropped and
# recreated database, ruff clean. Both new tests confirmed RED against 446ceaa.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# The transfer mechanism, not part of the PR.
foreach ($tmp in @('PR611_STATUS.md', 'pr61-1.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the guards this PR could break, then everything ===' -ForegroundColor Cyan
$guards = @(
  'tests/test_the_card_finishes_the_move_it_recommended.py',
  'tests/test_funding_service.py',
  'tests/test_sleeves_mean_what_they_say.py',
  'tests/test_two_cards_do_not_want_the_same_money.py',
  'tests/test_card_claims.py',
  'tests/test_plan_sleeves.py'
)
foreach ($g in $guards) {
  if (-not (Test-Path (Join-Path $repo $g))) { throw ("missing test file: {0}" -f $g) }
}
python -m pytest -q @guards
if ($LASTEXITCODE -ne 0) { throw 'the funding guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 61.1: the card was right and the funding plan was wrong

Eran, minutes after PR 61 deployed: "the second card is still guidance only and
not actionable!!"

/health served 446ceaa, so not a stale deploy. Asking production for the plan
behind that card is the whole diagnosis:

  SELL VTI   2,925.31  | cheapest way to raise it: Equities is the most
                         overweight class (81% against an 80% target) and VTI is
                         among its least-taxed positions
  SELL TQQQ  4,145.14  | ... and TQQQ is among its least-taxed positions
  from_cash    179.88

THE FUNDER WAS NOT SELLING TQQQ ALONE. VTI does not step back to QQQ, so PR 61's
rule correctly refused - to do something that would have been wrong anyway:
selling 2,925 of core money doing its job to start a sleeve whose funding was
sitting in the leveraged position the sleeve replaces. The card was the
messenger.

THE DEFECT IS THAT FUNDING IS RANKED ON FIT AND TAX. That is the right question
when paying for a purchase out of whatever the book can best spare, and the wrong
one when the purchase IS a conversion. rank_trim_candidates scores
name_over*400 + class_over*100 + tax_friendliness*10; with Equities barely over
and TQQQ carrying the larger gain, VTI simply scores higher. Every sentence in
those two reasons is true.

AND MY REPRODUCTION IS WHAT HID IT. PR 61's fixture put TQQQ at 59% of the book,
which trips the 25% single-name cap, so name_over dominated and TQQQ was picked
alone. The live plan runs risk_tolerance High - concentration_cap 0.40 - and
TQQQ sits at ~35%, UNDER its cap. That one number is the difference between a
ranking that picks the right leg by luck and one that does not. The new fixture
pins the risk tolerance for exactly this reason and reproduces the production
legs:

                      pre-fix                          post-fix
  funding legs        VTI 3,420.00 + TQQQ 3,884.37     TQQQ only
  the card            guidance, apply.kind none        actionable, fund_sleeve

THE FIX IS IN THE FUNDER, NOT THE CARD. rank_trim_candidates and plan_funding
gain 'prefer': a set of tickers that rank ahead of every fit-and-tax term
(+10,000, where name_over maxes near 400), are never dropped for scoring zero,
are eligible on their own terms for the same reason buying_class is - a
step-back swap converts one Equities position into another, so the class weight
does not move and the overweight budget has nothing to protect - and carry their
own reason, because "least-taxed position in the most overweight class" does not
describe why that position was chosen. _fund_sleeve fills the set with exactly
the holdings for which strategy_catalog.steps_back_to(held, sleeve_ticker) holds.

ONE RULE, TWO CALLERS. steps_back_to now decides both whether the card may act
and which position the funder sells, so the card can no longer be offered a plan
it has to refuse. Splitting that judgement across two functions is what produced
this.

THE LESSON WORTH KEEPING, AND IT IS NOW THREE IN A ROW. PR 60.1 shipped a dead
deferral because the test asserted the layer below the bug. PR 61's first rule
was caught by an existing test, not mine. This one was caught by Eran, in
production, because my fixture differed from his book in a single setting I
never thought to copy. A reproduction is not a reproduction until the setting
that decides the branch is in it.

Gates: 1723 passed on SQLite, 1723 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean. Both new tests confirmed RED
against 446ceaa.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/funding_service.py app/services/strategy_service.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_the_card_finishes_the_move_it_recommended.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr61-1-the-funder-sells-the-position-the-move-converts.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }

  Write-Host '=== 5. what the funder plans NOW ===' -ForegroundColor Cyan
  $body = '{"mode":"fund","dry_run":true}'
  $prev = Invoke-RestMethod -Method Post -Uri "$base/api/v1/strategies/btm_qqq_hold/load-basket" -Headers $apiHeaders -ContentType 'application/json' -Body $body -TimeoutSec 120
  foreach ($s in $prev.funding.sells) {
    Write-Host ("  SELL {0,-6} {1,10:N2}  | {2}" -f $s.ticker, $s.value_ils, $s.reason)
  }
  Write-Host ("  from cash {0:N2}   shortfall {1:N2}" -f $prev.funding.from_cash_ils, $prev.funding.shortfall_ils)
  $tickers = @($prev.funding.sells | ForEach-Object { $_.ticker })
  if ($tickers -contains 'VTI') {
    Write-Host '  STILL SELLING VTI. Check the deploy SHA before anything else.' -ForegroundColor Red
  } else {
    Write-Host '  TQQQ only - the funder is selling the position the move converts.' -ForegroundColor Green
  }

  Write-Host ''
  Write-Host '=== 6. and the card ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $cold = @($r.recommendations | Where-Object { $_.meta.cold_start -eq $true })
  if ($cold.Count -eq 0) { Write-Host '  no cold-start card.' -ForegroundColor Yellow }
  foreach ($c in $cold) {
    Write-Host ("  title:  {0}" -f $c.title)
    Write-Host ("  action: {0}" -f $c.action)
    Write-Host ("  amount: {0}" -f $c.est_amount)
    $kind = $null
    if ($c.apply) { $kind = $c.apply.kind }
    if ($kind -eq 'fund_sleeve') {
      Write-Host ("  ACTIONABLE - fund_sleeve, from {0}" -f ($c.meta.from -join ', ')) -ForegroundColor Green
      Write-Host '  NOT ACCEPTED HERE. Tap it on Today when you have read the legs.' -ForegroundColor Green
    } else {
      Write-Host ("  STILL GUIDANCE (apply.kind = {0}): {1}" -f $kind, $c.why) -ForegroundColor Red
    }
  }
}

Write-Host ''
Write-Host 'PR 61.1 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: PR 62 - Israeli companies into the candidate universe.'
