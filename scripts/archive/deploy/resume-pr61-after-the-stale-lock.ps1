# resume-pr61-after-the-stale-lock.ps1
# PR 61, second half only. Run INSTEAD of the ship script, after the commit
# failed on a stale .git/index.lock.
#
# WHAT HAPPENED. The lock was mine, not yours, and not a running git process.
# It is a ZERO-BYTE file stamped 14:37, owned by the Linux sandbox user that
# backs my shell into your folder - your run started around 15:00. At 14:37 my
# shell ran "git status --short" while inserting the STATUS.md entry. git writes
# .git/index.lock to refresh the index, then unlinks it; that shell is not
# allowed to delete files inside your folder, so the unlink failed and the lock
# survived. It printed as a warning on a command that still exited 0, and I read
# that line as being about the two temp files I was removing on the same line.
# Every later git WRITE - your three git add calls and the commit - then refused.
#
# I have switched to "git --no-optional-locks" for read-only git in that shell,
# which does not take the lock at all, so this cannot recur.
#
# WHY THIS SKIPS THE GATES. It does not skip them blind: it re-hashes all four
# files and refuses to continue unless they are byte-identical to what ruff and
# the 1737-test run passed on twenty minutes ago. Nothing has changed since, so
# running them again would prove the same thing about the same bytes.
#
# If you would rather just re-run everything, delete the lock and run the ship
# script again instead - it is idempotent:
#   Remove-Item C:\dev\Investwise-pro\.git\index.lock -Force
#   & C:\dev\Investwise-pro\scripts\deploy\ship-pr61-the-card-that-finishes-the-move.ps1
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

# ------------------------------------------------------ 0. clear the stale lock
$lock = Join-Path $repo '.git\index.lock'
if (Test-Path $lock) {
  $li = Get-Item $lock
  $ageMin = ((Get-Date) - $li.LastWriteTime).TotalMinutes
  Write-Host ("=== 0. stale lock: {0} bytes, {1:N0} min old ===" -f $li.Length, $ageMin) -ForegroundColor Cyan
  if ($li.Length -ne 0) {
    throw 'index.lock is NOT empty - a git process may really be writing. Do not delete it; check Task Manager for git.exe first.'
  }
  if ($ageMin -lt 2) {
    throw 'index.lock is less than 2 minutes old - it may belong to a live git. Wait, then re-run.'
  }
  Remove-Item $lock -Force
  Write-Host '  removed' -ForegroundColor Green
} else {
  Write-Host '=== 0. no stale lock ===' -ForegroundColor Cyan
}

# ------------------------------------- 1. the bytes must be the ones we gated
Write-Host '=== 1. are these the bytes the gates passed on ===' -ForegroundColor Cyan
$expected = @{
  'app/services/recommendations.py'                          = '92e00032845c5c9f2c63023ce85050047e70e7cb1729944813c3f24de6a11bfb'
  'app/services/strategy_catalog.py'                         = '898aa74ad17f312a0792fbabec70c5635e0300790d5d2fa210b07964ce62d25e'
  'tests/test_the_card_finishes_the_move_it_recommended.py'  = 'cc0ad6479155e14dce3a3b33e03bd4aa4d464b073ca4c5cd0e5c1e17572d8950'
  'scripts/deploy/ship-pr61-the-card-that-finishes-the-move.ps1' = 'cb84b5d134eb71caaf1b4cfcb39ef2aa4f65758aa984df6b7e310e1d0d81ea1e'
}
foreach ($k in $expected.Keys) {
  $p = Join-Path $repo $k
  if (-not (Test-Path $p)) { throw ("missing file: {0}" -f $k) }
  $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash
  if ($h.ToLower() -ne $expected[$k]) {
    throw ("{0} has changed since the gates ran. Run the full ship script instead." -f $k)
  }
  Write-Host ("  ok  {0}" -f $k) -ForegroundColor Green
}
Write-Host '  ruff and 1737 tests passed on exactly these bytes at 15:00.' -ForegroundColor Green

# ------------------------------------------------------------------- tidy-up
foreach ($tmp in @('PR61_STATUS.md', 'pr61.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
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
Accept sold 12,139. Measured on a reproduction of the live book (NAV 21,874,
btm_trend_soxl 33.91% held, btm_qqq_hold 33.09% holding nothing, TQQQ 7,476
unclaimed): the card reads "Finish the move: sell TQQQ, hold QQQ", one leg,
7,238, and nothing else is touched.

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

Gates: 1737 passed on Eran's machine, 1717 on SQLite and 1721 on PostgreSQL in
the sandbox against a dropped and recreated database, ruff check app tests
alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py app/services/strategy_catalog.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_the_card_finishes_the_move_it_recommended.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr61-the-card-that-finishes-the-move.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- scripts/deploy/resume-pr61-after-the-stale-lock.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 3. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }

  Write-Host '=== 4. what the cold-start card says NOW ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $c2 = @($r2.recommendations | Where-Object { $_.meta.cold_start -eq $true })
  if ($c2.Count -eq 0) {
    Write-Host '  no cold-start card. Check /api/v1/plan/sleeves below.' -ForegroundColor Yellow
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
  Write-Host '=== 5. the sleeves, for context ===' -ForegroundColor Cyan
  $sl = Invoke-RestMethod -Uri "$base/api/v1/plan/sleeves" -Headers $apiHeaders -TimeoutSec 60
  foreach ($s in $sl.sleeves) {
    Write-Host ("  {0,-16} target {1,6:N2}%  held {2,6:N2}%  gap {3,7:N2}" -f $s.strategy_id, $s.sleeve_pct, ($s.held_pct * 100), $s.gap_pct)
  }
}

Write-Host ''
Write-Host 'PR 61 SHIPPED.' -ForegroundColor Green
