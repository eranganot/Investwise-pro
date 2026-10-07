# ship-pr43-the-call-sites-pr42-missed.ps1
# PR 43 - PR 42 was right and incomplete, which is the same as wrong.
#
# Commits two files plus STATUS.md, pushes to main, waits for Railway to serve
# the new SHA, then runs a READ-ONLY smoke that asserts the sentence changed.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1621 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1621 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# The AST guard was confirmed RED against the deployed 5e3fa1c first, naming
# the two call sites PR 42 missed.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'app/services/recommendations.py',
  'tests/test_every_funding_call_passes_the_book_target.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$rc = @(Get-Content 'app/services/recommendations.py').Count
if ($rc -lt 4150) { throw "recommendations.py is $rc lines - expected ~4216. Truncated write; do not commit." }
Write-Host "recommendations.py: $rc lines" -ForegroundColor Green

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the affected tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_every_funding_call_passes_the_book_target.py tests/test_the_funding_layer_reads_the_books_target.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 43: the call sites PR 42 missed, and a guard that walks the tree

PR 42 shipped as 5e3fa1c and the card did not change. The USO card's funding leg
still read "cheapest way to raise it: Equities is the most overweight class
(86% against a 80% target) and VTI is among its least-taxed positions" on a book
whose own Equities target is 89.2% and whose /mix reports rebalance_required
false.

Root cause: the call-site list was truncated and treated as complete. The grep
that found the callers of propose_funded_buy was piped through head, which
returned ten lines. Two sites sat below the cut - _buy_idea_recs
(recommendations.py:3189) and _commodity_recs (:3303) - and the first is what
builds the screener cards on Today. PR 42 threaded the book target through the
funding layer correctly and then handed it to four of six callers. A fix that
reaches most of the call sites is indistinguishable, from the screen, from no fix
at all. This is safe-windows-edits Rule 1 - never trust a truncated view -
applied to a search rather than a file.

Fix: both sites now pass book_target. _buy_idea_recs takes it as a parameter,
threaded from btgt["target"] at its call site; _commodity_recs forwards the
book_mix it was already handed.

The guard matters more than the fix.
tests/test_every_funding_call_passes_the_book_target.py walks the AST of
recommendations.py and funding_service.py and fails if any call to
propose_funded_buy, plan_funding or rank_trim_candidates omits book_target. It
does not care what is passed, only that the caller says which target it means, so
a new call site cannot silently inherit the objective table. A second test builds
the exact missed call shape and asserts the walker flags it, so a green run is
evidence rather than an absence. Against d4ca292 the guard names all five sites;
against 5e3fa1c it names the two that were missed.

Not this bug: Today shows one card, not two. _ledger nets funding across the card
set and the screener deliberately runs last, so USO claimed the Commodities gap
and COMT - still ranked 84.7 - correctly has no card of its own.

Found, not fixed here: _commodity_recs:3264 and _redeploy_cash_recs:2860 read
"book_mix or OBJ_TARGET[objective]". That or is the trap targets_for exists to
avoid - a book target truthy but silent about a class yields zero for it. On this
book that is why _commodity_recs emits nothing. Changing it changes which cards
exist, so it is on the plan rather than done quietly.

Gate: SQLite 1621 passed / 0 failed / 0 skipped; Postgres 1621 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py tests/test_every_funding_call_passes_the_book_target.py STATUS.md scripts/deploy/ship-pr43-the-call-sites-pr42-missed.ps1
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
# This is the smoke PR 42 should have had: it ASSERTS the sentence, instead of
# printing it and trusting the reader.
Write-Host '=== 5. smoke (read-only, asserted) ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }

$mix = Invoke-RestMethod -Uri "$base/api/v1/mix" -Headers $apiHeaders -TimeoutSec 30
$mixLine = "  /mix Equities target {0} current {1}; rebalance_required {2}"
Write-Host ($mixLine -f $mix.target_allocation.Equities, $mix.current_allocation.Equities, $mix.rebalance_required)
$eqOver = ($mix.current_allocation.Equities -gt $mix.target_allocation.Equities)

$r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
Write-Host ("  {0} card(s) on Today:" -f $r.count)
$lies = @()
foreach ($card in $r.recommendations) {
  $kind = if ($card.apply) { $card.apply.kind } else { '-' }
  Write-Host ("    [{0}] {1}" -f $kind, $card.title)
  foreach ($leg in $card.apply.sells) {
    Write-Host ("        sell {0}: {1}" -f $leg.ticker, $leg.reason)
    if (($leg.reason -like '*most overweight class*') -and (-not $eqOver)) {
      $lies += ("{0} on {1}" -f $leg.ticker, $card.title)
    }
    if ($leg.reason -like '*80% target*') {
      $lies += ("{0} still quotes the objective table" -f $leg.ticker)
    }
  }
}

Write-Host ''
if ($lies.Count -gt 0) {
  Write-Host 'SMOKE FAILED - the sentence did not change:' -ForegroundColor Red
  foreach ($l in $lies) { Write-Host ("  " + $l) -ForegroundColor Red }
  throw 'PR 43 did not reach the path that builds these cards'
}
Write-Host 'SMOKE PASSED' -ForegroundColor Green
Write-Host '  No funding leg claims an overweight while /mix says there is none,'
Write-Host '  and none of them quotes the 80% objective table.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
