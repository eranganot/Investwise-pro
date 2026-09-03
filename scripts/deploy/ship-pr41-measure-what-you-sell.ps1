# ship-pr41-measure-what-you-sell.ps1
# PR 41 - the tax card counted losses it was not going to sell.
#
# Commits four code/test files plus STATUS.md, pushes to main, waits for Railway
# to serve the new SHA, then runs a READ-ONLY smoke.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1615 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1615 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# All three new tests were confirmed RED against 8541583 (PR 40) first.

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
  'app/services/portfolio_analytics.py',
  'tests/test_the_harvest_card_measures_what_it_sells.py',
  'tests/test_live_contradictions.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$lineCount = @(Get-Content 'app/services/recommendations.py').Count
if ($lineCount -lt 4100) { throw "recommendations.py is $lineCount lines - expected ~4207. Truncated write; do not commit." }
Write-Host "recommendations.py: $lineCount lines" -ForegroundColor Green

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the affected tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_harvest_card_measures_what_it_sells.py tests/test_live_contradictions.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 41: the tax card counted losses it was not going to sell

Measured on production on 2026-09-03, the morning after the core realign:

  apply.tickers          VTI, BND
  est_amount             146.46 ILS   "save about 146 in tax this year"
  losses on VTI + BND     11.17 ILS ->   2.79 ILS at the 25% CGT rate
  losses on SOXL + TQQQ  574.58 ILS -> 143.65 ILS, excluded from the sale
                                        three lines earlier as strategy
                                        holdings, and counted anyway

98% of the headline came from two positions the same code block had just decided
not to touch - a 52x overstatement of the card's own number. save was read
straight off tax_opportunities, which scans the whole book, while losers had
already been filtered. _unrealized_ils(rows, only=losers) now measures the loss
over the tickers the card actually sells, FX-converted per holding.

Three more, on the same card:

* It offered to sell the core. The exclusion knew about sleeves only, so on a
  book that had realigned into bal_6040 the day before, the card proposed selling
  VTI and BND - the basket itself. PR 37 settled that a core is a strategy too.
* It promised this year. Every position was below cost, so there were no gains to
  offset; tax_opportunities says "future gains (carry-forward)" in that case and
  the card said "Lowers this year's tax bill" regardless. It now says what the
  loss is worth once there is a gain to offset, and names the carry-forward.
* gains were unrealized and called realized. Winners owe no tax until sold, so
  the loss offsets nothing until they are.

Plus the disclosure the engine already carried and the card never repeated:
_TAX_ASSUMPTIONS states "no inflation-linked cost basis adjustment", so the
estimate is on the nominal gain and is an upper bound. The card says so now.

Effect on Today: on the live book every holding is either a sleeve (SOXL, TQQQ)
or the core basket (VTI, BND), so there is now NO harvest card at all. That is
the correct answer - nothing harvestable that the plan does not hold on purpose.
Today goes from three cards to two.

Tests: tests/test_the_harvest_card_measures_what_it_sells.py, three cases, all
three confirmed FAILING against 8541583 first. One existing test changed with it:
test_tax_harvest_never_targets_the_strategy_sleeve pinned that the exclusion ran
before the saving was quoted - which it did, and the saving was wrong anyway. It
now pins that the whole-book read is ABSENT and the measurement scoped to what
survives the exclusion. Strengthened, not relaxed.

Gate: SQLite 1615 passed / 0 failed / 0 skipped; Postgres 1615 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py app/services/portfolio_analytics.py tests/test_the_harvest_card_measures_what_it_sells.py tests/test_live_contradictions.py STATUS.md scripts/deploy/ship-pr41-measure-what-you-sell.ps1
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
# This PR has a visible, checkable effect on production, so the smoke asserts it.
Write-Host '=== 5. smoke (read-only) ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }

$p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
Write-Host ("  NAV {0:N2} ILS across {1} positions" -f $p.nav_ils, $p.positions.Count)

$r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
Write-Host ("  {0} cards on Today:" -f $r.count)
foreach ($card in $r.recommendations) {
  $kind = if ($card.apply) { $card.apply.kind } else { '-' }
  Write-Host ("    [{0}] {1}" -f $kind, $card.title)
}

$tax = @($r.recommendations | Where-Object { $_.dimension -eq 'tax' })
if ($tax.Count -eq 0) {
  Write-Host '  PASS: no harvest card - every holding is a sleeve or the core basket.' -ForegroundColor Green
} else {
  Write-Host '  A harvest card is still being built. Check it names only what it sells:' -ForegroundColor Yellow
  foreach ($t in $tax) {
    Write-Host ("    tickers: {0}" -f ($t.apply.tickers -join ', '))
    Write-Host ("    claims : {0:N2} ILS" -f $t.est_amount)
    Write-Host ("    action : {0}" -f $t.action)
    Write-Host ("    excluded as strategy: {0}" -f ($t.audit_trail.raw_data.excluded_strategy_sleeve -join ', '))
    Write-Host ("    realizable loss: {0}  offsets a gain now: {1}" -f $t.audit_trail.raw_data.realizable_loss_ils, $t.audit_trail.raw_data.offsets_a_gain_now)
  }
}

Write-Host ''
Write-Host 'PASS CRITERIA' -ForegroundColor Yellow
Write-Host '  1. /health serves the SHA above (already asserted).'
Write-Host '  2. The harvest card is GONE from Today - VTI and BND are the core basket,'
Write-Host '     SOXL and TQQQ are sleeves, so nothing is harvestable. Two cards, not three.'
Write-Host '  3. If a harvest card does appear, est_amount must equal 25% of realizable_loss_ils'
Write-Host '     for the tickers listed - not a number drawn from the whole book.'
Write-Host '  4. No card text says "in tax this year" or "lowers this year is tax bill".'
Write-Host '  5. NAV unchanged (~20,950 ILS) - this PR moves no money.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
