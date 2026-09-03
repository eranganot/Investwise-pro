# ship-pr42-read-the-books-target.ps1
# PR 42 - the funding layer was measuring against a target the rest of the app
# had left behind.
#
# Commits three code/test files plus STATUS.md, pushes to main, waits for
# Railway to serve the new SHA, then runs a READ-ONLY smoke.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1619 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1619 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# All four new tests were confirmed RED against d4ca292 (PR 41) first.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'app/services/funding_service.py',
  'app/services/recommendations.py',
  'tests/test_the_funding_layer_reads_the_books_target.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$fs = @(Get-Content 'app/services/funding_service.py').Count
$rc = @(Get-Content 'app/services/recommendations.py').Count
if ($fs -lt 690)  { throw "funding_service.py is $fs lines - expected ~714. Truncated write; do not commit." }
if ($rc -lt 4150) { throw "recommendations.py is $rc lines - expected ~4214. Truncated write; do not commit." }
Write-Host "funding_service.py: $fs lines; recommendations.py: $rc lines" -ForegroundColor Green

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the affected tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_funding_layer_reads_the_books_target.py tests/test_funding_service.py tests/test_card_claims.py tests/test_c3_funding.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 42: the funding layer reads the book's target, not the objective table

Measured on production on 2026-09-03 at d4ca292. Both cards on Today were funded
by selling VTI - the Classic 60/40 core basket bought two days earlier - and both
carried the reason:

  "cheapest way to raise it: Equities is the most overweight class
   (86% against a 80% target) and VTI is among its least-taxed positions"

/api/v1/mix, at the same moment, on the same book:

  target_allocation   {"Equities": 0.892, "Fixed Income": 0.108}
  current_allocation  {"Equities": 0.8625, ...}
  drift_percentage    {"Equities": -0.0295}
  rebalance_required  false

Opposite signs, same book, same screen. 80% is OBJ_TARGET["Grow"], which knows
nothing about sleeves or a core. 89.2% is the book's own target - 73% sleeves
plus 16.2% core - and is what /mix, the rebalance card and the contradictions
check all read. Equities at 86.25% is six points over the first and three points
UNDER the second, so the funding layer was selling equities to correct an
overweight that did not exist, and reaching for the core to do it because sleeves
are excluded from trims and the core is not.

Three faults, each surviving the one before it being fixed:

1. rank_trim_candidates, plan_funding and propose_funded_buy read
   OBJ_TARGET[objective] with no way for a caller to supply the book's target.
   funding_service.targets_for() is now the one seam; all three take
   book_target, and the three call sites in recommendations.py pass it.
2. The reason sentence claimed "the most overweight class" UNCONDITIONALLY. It is
   reached whenever a candidate scores at all, and the score carries a
   tax-friendliness term that stays positive when nothing is over anything.
3. And the overweight must be MATERIAL. With the book target threaded in, BND
   came out over by 0.06 points - 15.73 ILS, below MIN_TRADE_ILS - and inherited
   the claim VTI had just been relieved of: "Fixed Income is the most overweight
   class (11% against a 11% target)".

targets_for MERGES rather than replaces: it overrides only the classes the book
target names. Sleeves plus core describe Equities and Fixed Income and say
nothing about Commodities. Replacing the table outright sets the Commodities
target to zero, and propose_funded_buy refuses a buy into a class with no gap -
so both commodity idea cards would have vanished, not because the plan rejected
them but because the plan had never been asked.

Simulated on the live book before shipping: the Commodities buy gap stays open at
2,552 ILS so both cards survive, and VTI and BND now read "cheapest way to raise
it, but it corrects no drift ... Selling it does not move you closer to your
plan".

Deliberately NOT changed: the cards still fund by selling VTI. plan_funding's
book_has_budget fallback is explicit that when nothing is overweight the buy is
funded from wherever it can be, and that was a deliberate decision with a comment
on it. This PR makes the sentence true; whether the app should sell the core to
buy a class the plan does not model is a decision for Eran.

Tests: tests/test_the_funding_layer_reads_the_books_target.py, four cases, all
four confirmed FAILING against d4ca292 first.

Gate: SQLite 1619 passed / 0 failed / 0 skipped; Postgres 1619 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/funding_service.py app/services/recommendations.py tests/test_the_funding_layer_reads_the_books_target.py STATUS.md scripts/deploy/ship-pr42-read-the-books-target.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

# --------------------------------------------------------------------- push
Write-Host '=== 3. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

# ------------------------------------------------------------ wait for deploy
Write-Host '=== 4. waiting for production to serve ' -NoNewline -ForegroundColor Cyan
Write-Host "$sha ===" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(10)
$live = $null
while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
    if ($h.commit -eq $sha) { $live = $h; break }
    Write-Host ("  serving {0}, waiting..." -f $h.commit)
  } catch {
    Write-Host '  /health not answering yet...'
  }
  Start-Sleep -Seconds 20
}
if ($null -eq $live) { throw "production never served $sha within 10 minutes - check the Railway deploy log" }
Write-Host ("LIVE: {0} v{1}" -f $live.commit, $live.version) -ForegroundColor Green

# -------------------------------------------------------------------- smoke
Write-Host '=== 5. smoke (read-only) ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }

$mix = Invoke-RestMethod -Uri "$base/api/v1/mix" -Headers $apiHeaders -TimeoutSec 30
$mixLine = "  /mix target Equities {0}; current {1}; rebalance_required {2}"
Write-Host ($mixLine -f $mix.target_allocation.Equities, $mix.current_allocation.Equities, $mix.rebalance_required)

$r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
Write-Host ("  {0} cards on Today:" -f $r.count)
$bad = 0
foreach ($card in $r.recommendations) {
  $kind = if ($card.apply) { $card.apply.kind } else { '-' }
  Write-Host ("    [{0}] {1}" -f $kind, $card.title)
  foreach ($leg in $card.apply.sells) {
    Write-Host ("        sell {0}: {1}" -f $leg.ticker, $leg.reason)
    if ($leg.reason -like '*most overweight class*') {
      # Only legitimate if that class really is over its BOOK target.
      Write-Host '        ^ check this against /mix above' -ForegroundColor Yellow
      $bad = $bad + 1
    }
  }
}

Write-Host ''
Write-Host 'PASS CRITERIA' -ForegroundColor Yellow
Write-Host '  1. /health serves the SHA above (already asserted).'
Write-Host '  2. No funding leg says "Equities is the most overweight class" while /mix'
Write-Host '     reports rebalance_required False and a NEGATIVE Equities drift. Expect'
Write-Host '     "corrects no drift ... does not move you closer to your plan" instead.'
Write-Host '  3. The two commodity idea cards are STILL THERE. If they vanished, the'
Write-Host '     merge in targets_for is not doing its job - that is the regression to'
Write-Host '     watch for in this PR.'
Write-Host '  4. NAV unchanged (~20,950 ILS) - this PR moves no money.'
if ($bad -gt 0) {
  Write-Host ''
  Write-Host ("  NOTE: {0} leg(s) still claim an overweight - verify against /mix." -f $bad) -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
