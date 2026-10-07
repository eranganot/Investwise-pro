# ship-pr64-the-cash-target-became-soft.ps1
# PR 64 - the cash target became soft, and the floor became a number.
# Run AFTER PR 63. It refuses to start otherwise and applies its own patch.
#
# Eran, 2026-09-06:
#   "it should be a soft target. meaning if there is a good/valueble/interesting
#    holding that I must buy you can use the cash to do it --> but if you
#    recommend me to use my cash for this investment you should explain why I
#    should go below the 7.5%. the hard minimum cash I should hold is 150"
#
# THIS REVERSES A DECISION THE CODE STATED OUTRIGHT, and the reversed argument is
# kept in the file rather than deleted. cash_floor_pct's docstring said:
#
#   "THE FLOOR IS THE TARGET, and that is the point rather than a shortcut. A
#    reserve the machine is allowed to nibble into is not a reserve: on the live
#    book, Grow floors cash at 3% and the balance sat at 2.92%, so the app had
#    already spent the reserve down to its own limit and nothing on any screen
#    called that a decision."
#
# That was written against a real failure and the failure has not gone away,
# which is why the change is narrow rather than a repeal.
#
# TWO NUMBERS WHERE THERE WAS ONE:
#
#   target      7.5% of NAV (plans.cash_target_pct)   nobody routine crosses it
#   hard floor  150 shekels, absolute                 nothing crosses it, ever
#
# CASH_HARD_FLOOR_ILS is in shekels rather than a share of NAV on purpose: a
# percentage scales with the book, which is right for a target and wrong for a
# floor. cash_soft_floor_ils takes the MAX of the two, because on a small book
# 7.5% can be under 150 and a percentage must not quietly overrule an absolute.
#
# THE REASON IS A PRECONDITION, NOT A CONVENTION. plan_funding refuses
# allow_reserve without a non-empty reserve_reason - the same shape as
# describe_funding's required buying_class, and for the reason recorded there:
# "a missing argument is now a TypeError at the call site instead of a missing
# sentence in production prose". A boolean alone would let a future call site opt
# in and forget to explain, which is the whole of what Eran asked for.
#
# The card then says it in four parts - how much of the reserve, where cash lands
# against the target, that the floor is untouched, and his own why:
#
#   "This spends 850 of your cash reserve, taking cash to 1.3% against your 7.5%
#    target (150 left, hard floor 150 untouched). Worth it because TSEM ranks
#    88/100 on the Grow screen - strong momentum; cheap on earnings, and nothing
#    in the book was overweight enough to trim for it."
#
# THE BAR IS STRUCTURAL, NOT A SCORE THRESHOLD. The obvious design - let an idea
# above score N reach the reserve - needs a number, and any number here is one I
# invented for someone else's money. Instead the buy-idea card plans the ordinary
# way first and retries against the reserve only when trims and spendable cash
# could not pay for the purchase at all. The app never decides a name is worth
# the reserve; it puts the score, the reasons and the resulting cash percentage
# on the card. Bounded by _MAX_BUY_IDEA_CARDS = 2 and the funding ledger's
# cash_used, so two cards cannot each plan against the same reserve.
#
# DEBT, NAMED: cash_floor_pct / cash_floor_ils now return the TARGET and their
# names say floor. They are payload keys the UI reads, so the rename is a
# separate change. Recorded in STATUS.
#
# Gates on these exact bytes: 1754 SQLite, 1754 PostgreSQL on a dropped and
# recreated database, ruff clean. 8 of 12 new tests confirmed RED against
# 6dc3c8d.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

Write-Host '=== 0. PR 63 must be in first ===' -ForegroundColor Cyan
$log = (git log --oneline -3 | Out-String)
if ($log -notmatch 'PR 63') {
  throw 'PR 63 is not in the last three commits. Ship it first - pr64.patch is cut against its tree.'
}
git rev-parse --short HEAD
git status --short

Write-Host '=== 0b. what the book holds in cash right now ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  $pf = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 60
  $pl = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  $navv = [double]$pf.nav_ils
  $cashv = [double]$pf.cash_ils
  $tgt = [double]$pl.cash_target_pct
  Write-Host ("  cash {0:N2} of NAV {1:N2} = {2:P2}; target {3:N1}%; hard floor 150" -f $cashv, $navv, ($cashv / $navv), $tgt)
}

Write-Host '=== 1. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr64.patch'
if (-not (Test-Path $patch)) { throw 'pr64.patch is not in the repo root' }
$already = (Select-String -Path 'app\services\funding_service.py' -Pattern 'CASH_HARD_FLOOR_ILS' -Quiet)
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
frag = io.open("PR64_STATUS.md", encoding="utf-8").read().rstrip("\n")
s = io.open("STATUS.md", encoding="utf-8").read()
if "PR 64 " in s.split("## PR 63")[0]:
    print("already present")
else:
    anchor = "## PR 63 "
    i = s.index(anchor)
    s = s[:i] + frag + "\n\n" + s[i:]
    s = s.replace("**HEAD is PR 63.**", "**HEAD is PR 64.**", 1)
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
  'tests/test_the_cash_target_is_soft_and_the_floor_is_not.py',
  'tests/test_funding_service.py',
  'tests/test_the_book_can_hold_cash_on_purpose.py',
  'tests/test_a_cash_target_does_not_produce_a_buy_cash_card.py',
  'tests/test_card_claims.py'
)
$have = @()
foreach ($g in $guards) { if (Test-Path (Join-Path $repo $g)) { $have += $g } }
if ($have.Count -lt 3) { throw 'the guard list did not resolve - check the test filenames' }
python -m pytest -q @have
if ($LASTEXITCODE -ne 0) { throw 'the cash guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$msg = @'
PR 64: the cash target became soft, and the floor became a number

Eran, 2026-09-06: "it should be a soft target. meaning if there is a
good/valueble/interesting holding that I must buy you can use the cash to do it
--> but if you recommend me to use my cash for this investment you should
explain why I should go below the 7.5%. the hard minimum cash I should hold is
150"

THIS REVERSES A DECISION THE CODE STATED OUTRIGHT, and the reversed argument is
kept in the file rather than deleted. cash_floor_pct's docstring said:

  "THE FLOOR IS THE TARGET, and that is the point rather than a shortcut. A
   reserve the machine is allowed to nibble into is not a reserve: on the live
   book, Grow floors cash at 3% and the balance sat at 2.92%, so the app had
   already spent the reserve down to its own limit and nothing on any screen
   called that a decision."

That was written against a real failure and the failure has not gone away, which
is why the change is narrow rather than a repeal.

TWO NUMBERS WHERE THERE WAS ONE:

  target      7.5% of NAV (plans.cash_target_pct)   nobody routine crosses it
  hard floor  150 shekels, absolute                 nothing crosses it, ever

CASH_HARD_FLOOR_ILS is in shekels rather than a share of NAV on purpose: a
percentage scales with the book, which is right for a target and wrong for a
floor. cash_soft_floor_ils takes the MAX of the two, because on a small book
7.5% can be under 150 and a percentage must not quietly overrule an absolute.

THE REASON IS A PRECONDITION, NOT A CONVENTION. plan_funding refuses
allow_reserve without a non-empty reserve_reason - the same shape as
describe_funding's required buying_class, and for the reason recorded there: "a
missing argument is now a TypeError at the call site instead of a missing
sentence in production prose". A boolean on its own would let a future call site
opt in and forget to explain, which is the whole of what Eran asked for.

The card then says it in four parts - how much of the reserve, where cash lands
against the target, that the floor is untouched, and his own why. reserve_reason
is cleared on the payload unless the reserve was actually spent, so the sentence
cannot be printed with nothing behind it.

THE BAR IS STRUCTURAL, NOT A SCORE THRESHOLD. The obvious design - let a buy
idea above score N reach the reserve - needs a number, and any number here is
one I invented for someone else's money. Instead the buy-idea card plans the
ordinary way first and retries against the reserve only when trims and spendable
cash could not pay for the purchase at all. The app never decides a name is
worth the reserve; it puts the score, the ranking reasons and the resulting cash
percentage on the card and Eran taps or does not. Bounded by
_MAX_BUY_IDEA_CARDS = 2 and by the funding ledger's cash_used, so two cards in
one build cannot each plan against the same reserve.

DEBT, NAMED RATHER THAN LEFT IMPLICIT. cash_floor_pct / cash_floor_ils now
return the TARGET, and their names say floor. They are payload keys the UI
reads, so renaming the concept end to end is a separate change. The docstring
says what the function means today and quotes what it used to mean.

tests/test_the_cash_target_is_soft_and_the_floor_is_not.py - 12 tests, 8
confirmed RED against 6dc3c8d.

Gates: 1754 passed on SQLite, 1754 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/funding_service.py app/services/recommendations.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_the_cash_target_is_soft_and_the_floor_is_not.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md scripts/deploy/ship-pr64-the-cash-target-became-soft.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

foreach ($tmp in @('PR64_STATUS.md', 'pr64.patch')) {
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
  Write-Host '=== 7. does any card reach for the reserve today ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
  $spoke = $false
  foreach ($c in $r.recommendations) {
    $txt = ($c.action + ' ' + ($c.how -join ' '))
    if ($txt -match 'cash reserve') {
      $spoke = $true
      Write-Host ("  {0}" -f $c.title) -ForegroundColor Green
      Write-Host ("    {0}" -f $c.action)
    }
  }
  if (-not $spoke) {
    Write-Host '  none, which is the expected answer on a book at or above target.'
    Write-Host '  The reserve is a last resort: a card reaches it only when trims and'
    Write-Host '  spendable cash could not pay for the buy at all.'
  }
}

Write-Host ''
Write-Host 'PR 64 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: PR 65 - C: the veto and the cap that nothing reads.'
