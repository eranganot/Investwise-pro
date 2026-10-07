# ship-pr55-1-volatility-of-todays-book.ps1
# PR 55.1 - the book's volatility is about the book, not the strategies.
# Run after PR 55.
#
# PR 55 found /plan saying 44.62% while the realised measurement said 67.48%,
# and showed the disagreement was a CONTRADICTION: the plan's number is a
# weighted average of component volatilities, which is an upper bound, and the
# realised figure was above it.
#
# THIS IS WHY, and it is measured rather than inferred. The backtests already
# store time_in_market_pct:
#
#   sleeve                    w%     vol%   in-mkt%   implied in-market vol%
#   btm_trend_soxl (SOXL)   35.09   83.95     73.4            97.99
#   btm_swing_dip  (TQQQ)   35.22   34.24     15.6            86.69
#   bal_6040 core           26.77   11.59    100.0            11.59
#   cash                     2.92    0.00      ---             0.00
#
#   blend of LONG-RUN  vols = 44.62%   <- what /plan reported
#   blend of IN-MARKET vols = 68.02%   <- realised measured 67.48%
#
# Half a point. btm_swing_dip holds TQQQ and sits in cash 84.4% of the time, so
# its 34.24% is a ten-year average made mostly of flat days. The book holds
# TQQQ right now, so that sleeve has been contributing near 87% volatility on a
# third of the book all along and no screen said so.
#
# THE SCALING: in the market a fraction f of the time and flat otherwise, the
# flat days contribute no variance, so sigma_measured^2 = f * sigma_in^2 and
# sigma_in = sigma_measured / sqrt(f). An estimate, not an identity - the
# reconciliation above is the evidence it holds here, and the test fails if it
# drifts more than two points from the realised figure.
#
# YOUR DECISION: the headline is today's exposure, because that is what every
# other risk figure on the screen is about. The long-run number stays beside it
# as portfolio_volatility_pct_strategy_longrun.
#
# THE OBSERVABLE PROOF: volatility_exceeds_assumption should go FALSE after this
# deploy, because the comparison is finally like-for-like. Step 5 checks it.
#
# Gates already run on these exact bytes: 1667 SQLite, 1667 PostgreSQL, ruff
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
  Write-Host '=== 0b. the two numbers before ===' -ForegroundColor Cyan
  $pl = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  $r  = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 150
  Write-Host ("  plan volatility     {0,8}%" -f $pl.portfolio_volatility_pct)
  Write-Host ("  realised volatility {0,8}%" -f $r.annualized_volatility_pct)
  if ($r.volatility_exceeds_assumption) {
    Write-Host '  flagged as a contradiction (this is what should go quiet)' -ForegroundColor Yellow
  }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_book_volatility_matches_todays_exposure.py tests/test_the_forward_numbers_say_what_they_are.py
if ($LASTEXITCODE -ne 0) { throw 'the volatility guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 55.1: the book's volatility is about the book, not the strategies

PR 55 found /plan.portfolio_volatility_pct at 44.62 and the realised
/portfolio/risk.annualized_volatility_pct at 67.48 on the same book at the same
moment, and showed the disagreement was a contradiction rather than a
difference of method: the plan's figure is a NAV-weighted average of component
volatilities, which is an upper bound on the volatility of any portfolio built
from them, and the realised figure was above it.

This is why, and it is measured rather than inferred. The backtests already
store time_in_market_pct, and it accounts for the whole gap:

  sleeve                    w%     vol%   in-mkt%   implied in-market vol%
  btm_trend_soxl (SOXL)   35.09   83.95     73.4            97.99
  btm_swing_dip  (TQQQ)   35.22   34.24     15.6            86.69
  bal_6040 core           26.77   11.59    100.0            11.59
  cash                     2.92    0.00      ---             0.00

  blend of LONG-RUN  vols = 44.62%   <- what /plan reported
  blend of IN-MARKET vols = 68.02%   <- realised measured 67.48%

Half a point. btm_swing_dip holds TQQQ and sits in cash 84.4% of the time, so
its 34.24% is a ten-year average made mostly of flat days. The book is holding
TQQQ right now, so that sleeve has been contributing something near 87%
volatility on a third of the book all along, and no screen said so.

THE SCALING, stated so it can be argued with. If a strategy is in the market a
fraction f of the time and flat otherwise, the flat days contribute no
variance, so the variance measured over the whole window is f * sigma_in^2 and

    sigma_in = sigma_measured / sqrt(f)

It assumes out-of-market days are genuinely flat -- true when the sleeve sits in
cash -- and that in-market days are homogeneous. The reconciliation above is the
evidence it holds here. It is an estimate, not an identity, and the test fails
if it drifts more than two points from the realised figure.

Both numbers are true and they answer different questions. ~68% is what the
book is doing today with its sleeves in the market; ~44.6% is what it will
average over a year with btm_swing_dip flat most of it. Eran's decision: the
headline is today's exposure, because that is what every other risk figure on
the screen is about - VaR is a statement about the book you hold this morning.
/plan.portfolio_volatility_pct now carries the conditional figure and
portfolio_volatility_pct_strategy_longrun carries the other, labelled.

Guards on the edges, because sigma/sqrt(f) is sharp:

  * An ABSENT exposure is not an exposure of zero. A component with no
    exposure_pct - the cash line, or a backtest predating the field - keeps its
    measured number rather than being scaled by a guess.
  * A component in the market 100% of the time is untouched; scaling the core's
    11.59% would invent risk that is not there.
  * Below EXPOSURE_FLOOR_PCT (10%) the scaling is floored and
    volatility_conditional_floored says it bound. At 1% exposure the formula
    implies ten times the measured volatility on a measurement resting on a
    handful of days - arithmetic, not evidence - and a silent clamp is a lie the
    size of the difference.
  * Only a MEASURED volatility is conditioned. A static instrument figure
    already describes buy-and-hold, so scaling it would invent exposure the
    number never averaged over.

The observable proof: portfolio_risk_service now passes the conditional blend
as assumed_volatility_pct, so PR 55's contradiction check compares like with
like, and volatility_exceeds_assumption should go FALSE. That flag going quiet
is the confirmation the conditioning was the missing piece; if it stays true,
the scaling has stopped describing this book and the headline is wrong again.

Gates: 1667 passed on SQLite, 1667 on PostgreSQL, ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/measured_assumptions.py app/services/portfolio_risk_service.py app/api/routes/plan.py
git add -- tests/test_the_book_volatility_matches_todays_exposure.py
git add -- STATUS.md scripts/deploy/ship-pr55-1-volatility-of-todays-book.ps1
git add -- scripts/smoke/smoke-52-to-55.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ---------------------------------------------------- did the flag go quiet?
if ($haveKey) {
  Write-Host '=== 5. the two numbers after ===' -ForegroundColor Cyan
  $pl2 = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  $r2  = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 150

  Write-Host ("  plan volatility (today's exposure)  {0,8}%" -f $pl2.portfolio_volatility_pct)
  Write-Host ("  plan volatility (strategy long-run) {0,8}%" -f $pl2.portfolio_volatility_pct_strategy_longrun)
  Write-Host ("  realised volatility                 {0,8}%" -f $r2.annualized_volatility_pct)
  Write-Host ("  assumed (now the conditional one)   {0,8}%" -f $r2.assumed_volatility_pct)

  if ($r2.volatility_exceeds_assumption) {
    Write-Host '' -ForegroundColor Yellow
    Write-Host '  STILL FLAGGED. The conditional blend is below what the book' -ForegroundColor Yellow
    Write-Host '  actually did, so the sigma/sqrt(f) scaling has stopped' -ForegroundColor Yellow
    Write-Host '  describing this book. Read the note and look before trusting' -ForegroundColor Yellow
    Write-Host '  the headline:' -ForegroundColor Yellow
    Write-Host ("  {0}" -f $r2.volatility_note) -ForegroundColor Yellow
  } else {
    Write-Host ''
    Write-Host '  THE FLAG WENT QUIET. The plan and the measurement now describe' -ForegroundColor Green
    Write-Host '  the same book, and the 23-point gap was btm_swing_dip being in' -ForegroundColor Green
    Write-Host '  the market 15.6% of the time.' -ForegroundColor Green
  }

  $gap = [math]::Abs($pl2.portfolio_volatility_pct - $r2.annualized_volatility_pct)
  Write-Host ''
  Write-Host ("  plan vs actual: {0:N2} points apart (was about 23)" -f $gap)
}

Write-Host ''
Write-Host 'PR 55.1 SHIPPED.' -ForegroundColor Green
Write-Host '  Then run: .\scripts\smoke\smoke-52-to-55.ps1'
