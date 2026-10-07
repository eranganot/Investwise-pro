# ship-pr61-the-card-that-finishes-the-move.ps1
# PR 61 - the card that finishes the move, and the first rule for it was wrong.
# Run after PR 60.1 (d8f7d09).
#
# Eran, looking at Today after the B2 swap: "the swing should be a card that
# replace the TQQQ with QQQ automatically."
#
# PR 60 stopped the CORE card offering to liquidate the 7,476 of TQQQ that no
# sleeve claims any more. This is the other half - the card that offers to
# complete the move - and it turns on one question the file had already answered
# in the negative:
#
#   "one tap taking a book from nothing to a fifth in a 3x fund is a different
#    decision from topping up. That one routes to 'Fund this sleeve'."
#
# THE FIRST RULE I WROTE WAS WRONG, AND THE SUITE CAUGHT IT. I made the card
# actionable when every funding leg was cash or a holding NO SLEEVE CLAIMS. Full
# SQLite run: test_sleeves_mean_what_they_say.py::
# test_k_an_empty_sleeve_card_cannot_be_buried_by_accept - 1 failed, 1718
# passed. On that fixture the rule would have taken a book from nothing to 10%
# SOXL by selling an ordinary V position: precisely the decision the guidance
# split exists to keep out of one tap. Being UNCLAIMED is not the same as being
# EARMARKED, and I had tested the wrong one.
#
# THE RULE THAT SURVIVES IS THE RELATIONSHIP. Actionable only when every position
# being sold is a leveraged instrument whose BASE is this sleeve's ticker - a new
# strategy_catalog.steps_back_to(aggressive, base), read straight off the specs.
#
#   sold   sleeve   steps_back_to                                 card
#   TQQQ   QQQ      yes - three rules hold TQQQ over a QQQ core    ACTIONABLE
#   SOXL   SMH      yes                                            actionable
#   V      SOXL     no - V is not an overlay instrument            guidance
#   QQQ    TQQQ     NO - the helper is asymmetric on purpose       guidance
#
# The asymmetry is the point. Selling the 3x fund to hold the index it was
# leveraging is a DOWNGRADE of an exposure the book already has, under a rule
# that is no longer running. The reverse is the move the objection was about.
#
# Still guidance when a leg belongs to another sleeve (the piggy-bank exclusion
# _fund_sleeve already enforces) or when there is a shortfall (a position at a
# size nobody chose, which that function abstains over rather than half-install).
#
# PRICED THROUGH THE SAME FUNCTION THE BUTTON RUNS: load_basket(mode=fund,
# dry_run=True) inside _agent_tx, quoting min(amount_ils, funded_ils) - what the
# executor actually spends. Measured on a reproduction of the live book: card
# est_amount 15,671.01, Accept moved 15,670.96, sleeve 0% -> 33.3%, card stopped
# firing.
#
# AND THE GUIDANCE BRANCH STOPPED POINTING AT A TAB. It now carries the funding
# engine's own refusal - "You asked for a 40% sleeve (44,520). The most this book
# can fund right now is 0% ... Lower the sleeve to about 0%, or add cash."
#
# WHAT TO EXPECT AFTER: on the live book the cold-start QQQ card should arrive
# with apply.kind = fund_sleeve and a title reading "Finish the move: sell TQQQ,
# hold QQQ". Step 5 asks production and prints it. IT DOES NOT ACCEPT IT - that
# is your tap, not the script's.
#
# Gates already run on these exact bytes: 1717 SQLite (plus 4 caplog errors that
# are an artefact of running with -p no:logging, green with the plugin on), 1721
# PostgreSQL on a dropped and recreated database, ruff clean.
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
  Write-Host '=== 0b. what the cold-start card says BEFORE ===' -ForegroundColor Cyan
  $r0 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $c0 = @($r0.recommendations | Where-Object { $_.meta.cold_start -eq $true })
  if ($c0.Count -eq 0) {
    Write-Host '  no cold-start card on the live book right now.'
  } else {
    foreach ($c in $c0) {
      Write-Host ("  title: {0}" -f $c.title)
      Write-Host ("  apply: {0}" -f (($c.apply | ConvertTo-Json -Compress)))
    }
  }
}

# ---------------------------------------------------------------- the tidy-up
# These two were the transfer mechanism, not part of the PR. device_bash cannot
# delete inside a connected folder, so they are removed here.
foreach ($tmp in @('PR61_STATUS.md', 'pr61.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the guards this PR could break, then everything ===' -ForegroundColor Cyan
$guards = @(
  'tests/test_the_card_finishes_the_move_it_recommended.py',
  'tests/test_sleeves_mean_what_they_say.py',
  'tests/test_two_cards_do_not_want_the_same_money.py',
  'tests/test_card_claims.py',
  'tests/test_plan_sleeves.py'
)
foreach ($g in $guards) {
  if (-not (Test-Path (Join-Path $repo $g))) { throw ("missing test file: {0}" -f $g) }
}
python -m pytest -q @guards
if ($LASTEXITCODE -ne 0) { throw 'the sleeve guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 61: the card that finishes the move, and the first rule for it was wrong

Eran, looking at Today after the B2 swap: "the swing should be a card that
replace the TQQQ with QQQ automatically."

PR 60 stopped the CORE card offering to liquidate the 7,476 of TQQQ that no
sleeve claims any more. This is the other half - the card that offers to
complete the move - and it turns on one question the file had already answered
in the negative:

  "one tap taking a book from nothing to a fifth in a 3x fund is a different
   decision from topping up. That one routes to 'Fund this sleeve'."

That objection is right. It is also not a description of every cold sleeve, and
after a swap it describes the opposite of what is happening.

THE FIRST RULE I WROTE WAS WRONG, AND THE SUITE CAUGHT IT. I made the card
actionable when every funding leg was cash or a holding NO SLEEVE CLAIMS. Full
SQLite run: test_sleeves_mean_what_they_say.py::
test_k_an_empty_sleeve_card_cannot_be_buried_by_accept - 1 failed, 1718 passed.
On that fixture the rule would have taken a book from nothing to 10% SOXL by
selling an ordinary V position: precisely the decision the guidance split exists
to keep out of one tap. Being UNCLAIMED is not the same as being EARMARKED, and
I had tested the wrong one.

THE RULE THAT SURVIVES IS THE RELATIONSHIP, not the owner and not the size.
Actionable only when every position being sold is a leveraged instrument whose
BASE is this sleeve's ticker - a new strategy_catalog.steps_back_to(aggressive,
base), read straight off the specs:

  sold   sleeve   steps_back_to                                 card
  TQQQ   QQQ      yes - three rules hold TQQQ over a QQQ core    ACTIONABLE
  SOXL   SMH      yes                                            actionable
  V      SOXL     no - V is not an overlay instrument            guidance
  QQQ    TQQQ     NO - the helper is asymmetric on purpose       guidance

The asymmetry is the whole point. Selling the 3x fund to hold the index it was
leveraging is a DOWNGRADE of an exposure the book already has, under a rule that
is no longer running. The reverse is the move the objection was written about.

Still guidance when a leg belongs to another sleeve (the piggy-bank exclusion
_fund_sleeve already enforces) or when there is a shortfall (a position at a
size nobody chose, which that function abstains over rather than half-install).

PRICED THROUGH THE SAME FUNCTION THE BUTTON RUNS. The card computes
load_basket(mode=fund, dry_run=True) inside _agent_tx and quotes
min(amount_ils, funded_ils) - what the executor actually spends. That is the
_core_plan discipline, which exists because a card once said "sell 4,324" and
Accept sold 12,139. Measured on a reproduction of the live book: card
est_amount 15,671.01, Accept moved 15,670.96, the sleeve went 0% -> 33.3%, and
the card stopped firing because the sleeve is no longer cold.

AND THE GUIDANCE BRANCH STOPPED POINTING AT A TAB. It now carries the funding
engine's own refusal, already written to be printable: "You asked for a 40%
sleeve (44,520). The most this book can fund right now is 0% - 44,520 short...
Lower the sleeve to about 0%, or add cash." A card that says which number to
change beats one that says which tab to open.

tests/test_the_card_finishes_the_move_it_recommended.py - 9 tests, 6 confirmed
RED against the pre-change tree; the other 3 assert invariants that already
held.

THE LESSON WORTH KEEPING. Two PRs in a row, the thing that caught my mistake was
not the test I wrote for the feature - it was an existing test asserting an
invariant I had stopped thinking about. PR 58 was the war-room speaker sequence;
this was the guidance split. A rule that is "obviously right" about the case in
front of you is worth running against the case that made the rule exist.

Gates: 1717 passed on SQLite, 1721 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py app/services/strategy_catalog.py
git add -- tests/test_the_card_finishes_the_move_it_recommended.py
git add -- STATUS.md scripts/deploy/ship-pr61-the-card-that-finishes-the-move.ps1
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
  Write-Host '=== 5. what the cold-start card says NOW ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $c2 = @($r2.recommendations | Where-Object { $_.meta.cold_start -eq $true })
  if ($c2.Count -eq 0) {
    Write-Host '  no cold-start card. Either every sleeve holds something, or the' -ForegroundColor Yellow
    Write-Host '  drift band did not fire. Check /api/v1/plan/sleeves below.' -ForegroundColor Yellow
  }
  foreach ($c in $c2) {
    Write-Host ("  title:  {0}" -f $c.title)
    Write-Host ("  action: {0}" -f $c.action)
    Write-Host ("  amount: {0}" -f $c.est_amount)
    $kind = $null
    if ($c.apply) { $kind = $c.apply.kind }
    if ($kind -eq 'fund_sleeve') {
      Write-Host ("  ACTIONABLE - apply.kind = fund_sleeve, strategy {0}" -f $c.apply.strategy_id) -ForegroundColor Green
      Write-Host ("  from: {0}" -f ($c.meta.from -join ', ')) -ForegroundColor Green
      Write-Host '  NOT ACCEPTED HERE. Tap it on Today when you have read the legs.' -ForegroundColor Green
    } else {
      Write-Host ("  guidance (apply.kind = {0}). The why now carries the reason:" -f $kind) -ForegroundColor Yellow
      Write-Host ("    {0}" -f $c.why)
    }
  }

  Write-Host ''
  Write-Host '=== 6. the sleeves, for context ===' -ForegroundColor Cyan
  $sl = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl.sleeves) {
    Write-Host ("  {0,-16} target {1,6:N2}%  held {2,6:N2}%  gap {3,7:N2}" -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), $s.gap_pct)
  }
}

Write-Host ''
Write-Host 'PR 61 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: the sleeve target following its signal (item 4 option 2, remainder),'
Write-Host '  then the two validations - TASE instruments, and the V5 + V6 reconciliation.'
