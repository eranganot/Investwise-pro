# ship-pr44-the-core-is-not-spare-change.ps1
# PR 44 - the core basket is off-limits as a funding source, like the sleeves.
#
# Commits two files plus STATUS.md, pushes to main, waits for Railway to serve
# the new SHA, then runs a READ-ONLY smoke that ASSERTS the exclusion.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1623 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1623 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# Both new tests were confirmed RED against the deployed b7d369f first.
#
# EXPECT TODAY TO GO EMPTY OF BUY CARDS. Every holding is a sleeve or the core,
# and spendable cash is zero (618.65 against a 3% floor of 628), so nothing is
# fundable. That is the point, not a fault.

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
  'tests/test_the_core_is_not_a_funding_source.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$rc = @(Get-Content 'app/services/recommendations.py').Count
if ($rc -lt 4160) { throw "recommendations.py is $rc lines - expected ~4227. Truncated write; do not commit." }
Write-Host "recommendations.py: $rc lines" -ForegroundColor Green

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the affected tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_core_is_not_a_funding_source.py tests/test_the_harvest_card_measures_what_it_sells.py tests/test_every_funding_call_passes_the_book_target.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 44: the core is not spare change

Decided by Eran on 2026-09-03, on the card PR 43 had just made honest:

  Consider COMT (Broad commodities) - 1,465 ILS
  How it's funded:  Sell 6.7347 BND (~1,465 ILS)
  reason: "cheapest way to raise it, but it corrects no drift: Fixed Income is
           at 11% against a 11% target ... Selling it does not move you closer
           to your plan"

1,465 of a 2,263 position - 65% of the bond leg of the core - taking Fixed Income
from 10.8% to 3.8% against an 11% target. The sentence was true. Saying it is not
the same as declining to do it.

The book is exactly on target and nothing is overweight, so plan_funding's
documented book_has_budget fallback funded the buy from whatever it could reach.
Sleeves were off-limits (C3). The core was not.

One list, not two. Sleeves were the whole exclusion set; PR 41 bolted the core
onto the TAX CARD's copy of that idea and left funding's copy alone, so for two
deploys the same screen refused to harvest BND and offered to sell 65% of it.
_funding_exclusions(session, user, btgt) is now the single source, used by
_war_room_recs, build_recommendations and the tax block. It degrades to the
sleeves alone on failure rather than to nothing - a missing core basket must not
silently widen what is sellable.

The core is not frozen: core_to_basket (PR 39) trades it, sized by one plan, and
that is still the path allowed to touch it. What is now impossible is the core
being sold as a side effect of buying something else.

Effect on Today: every holding is a sleeve (SOXL, TQQQ) or the core basket (VTI,
BND) and spendable cash is zero, so nothing is fundable and there are no buy
cards. That is the honest state of a fully-invested book on its target. Cards
return when a class drifts, a name breaks its cap, or new money arrives.

Tests: tests/test_the_core_is_not_a_funding_source.py, both confirmed FAILING
against b7d369f first. The behavioural one covers _funding_exclusions; the
structural one walks the AST and asserts every exclude= handed to
propose_funded_buy / plan_funding / rank_trim_candidates is either a forwarded
parameter or a name bound from _funding_exclusions. Its first draft banned
sleeve_tickers outright and was wrong - _core_plan and the core-ceiling card use
it for a different question - so the guard pins the funding path only.

Gate: SQLite 1623 passed / 0 failed / 0 skipped; Postgres 1623 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py tests/test_the_core_is_not_a_funding_source.py STATUS.md scripts/deploy/ship-pr44-the-core-is-not-spare-change.ps1
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
Write-Host '=== 5. smoke (read-only, asserted) ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }

$protectedList = @('VTI', 'BND', 'SOXL', 'TQQQ')   # core basket + sleeves
$p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
$navLine = "  NAV {0:N2} ILS, cash {1:N2}"
Write-Host ($navLine -f $p.nav_ils, $p.cash_ils)

$r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
Write-Host ("  {0} card(s) on Today:" -f $r.count)
$raided = @()
foreach ($card in $r.recommendations) {
  $kind = if ($card.apply) { $card.apply.kind } else { '-' }
  Write-Host ("    [{0}] {1}" -f $kind, $card.title)
  foreach ($leg in $card.apply.sells) {
    Write-Host ("        sell {0} (~{1:N0} ILS)" -f $leg.ticker, $leg.value_ils)
    if ($protectedList -contains $leg.ticker) {
      $raided += ("{0} on '{1}'" -f $leg.ticker, $card.title)
    }
  }
  foreach ($t in $card.apply.tickers) {
    if ($protectedList -contains $t) { $raided += ("{0} on '{1}'" -f $t, $card.title) }
  }
}

Write-Host ''
if ($raided.Count -gt 0) {
  Write-Host 'SMOKE FAILED - a card still sells something the plan holds on purpose:' -ForegroundColor Red
  foreach ($x in $raided) { Write-Host ("  " + $x) -ForegroundColor Red }
  throw 'the core or a sleeve is still a funding source'
}
Write-Host 'SMOKE PASSED' -ForegroundColor Green
Write-Host '  No card sells a sleeve or the core basket.'
if ($r.count -eq 0) {
  Write-Host '  Today has no buy cards, which is expected: every holding is a sleeve or'
  Write-Host '  the core, and spendable cash is zero. Nothing is fundable, so nothing is'
  Write-Host '  proposed. Cards return when a class drifts, a name breaks its cap, or new'
  Write-Host '  money arrives.'
}
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
