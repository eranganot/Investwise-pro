# ship-pr56-ops-and-the-55-1-withdrawal.ps1
# PR 56 - the ops track, and the withdrawal of PR 55.1. Run after PR 55.1.
#
# TWO THINGS IN ONE PR because they share nothing except the date. The honest
# label for the second half is a RETRACTION.
#
# PR 55.1 said the 23-point gap between /plan (44.62) and the realised
# /portfolio/risk (67.48) was exposure: a sleeve in the market a fraction f of
# the time and FLAT otherwise has sigma_in = sigma_measured / sqrt(f). It
# reconciled - 68.02 against 67.48, half a point - and I took that as proof.
#
# It is wrong. The out-of-market days are not flat. _weights_or_off hands the
# sleeve its BASE when the rule is off, never cash, and says so in its own
# docstring. Measured on the shipped engine, 2016-09-06 to 2026-09-04:
#
#   sleeve            in mkt   measured   TRUE in-mkt   out-of-mkt   formula
#   btm_swing_dip      15.6%     33.91%      67.97%       22.46%      85.96%
#   btm_trend_soxl     74.0%     83.50%      93.95%       40.97%      97.05%
#
# The variance decomposition closes (f*sig_in^2 + (1-f)*sig_out^2 = 33.84 vs
# 33.91 measured). The half-point agreement was two errors of opposite sign
# cancelling. That is the specific danger: it manufactured its own evidence.
#
# NOT REPLACED WITH A CORRECTED VERSION, because a corrected one does not
# reconcile either - blending the TRUE in-market vols gives 60.01 against a
# realised 67.48, below the portfolio it is meant to bound. A second difference
# is doing work: the components are ten-year backtests, the realised figure is
# 251 days. So the app now asserts NEITHER cause and labels both windows.
#
# WHAT TO EXPECT AFTER THIS DEPLOY, and it is the opposite of last time:
#   plan volatility goes 68.02 -> about 44.62 (the blend, back in the headline)
#   portfolio_volatility_pct_strategy_longrun DISAPPEARS
#   volatility_exceeds_assumption goes back to TRUE, with a note that names
#   two candidates instead of calling it a contradiction
# A flag that goes loud again is the correct outcome here. Step 5 checks it.
#
# The ops half ships scripts/ops/ops-check.ps1 (14 read-only checks) and
# scripts/ops/OPS_RUNBOOK.md. Neither is code the app runs.
#
# Gates already run on these exact bytes: 1670 SQLite, 1670 PostgreSQL on a
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
  Write-Host '=== 0b. what PR 55.1 is saying right now ===' -ForegroundColor Cyan
  $pl = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  plan volatility (the 55.1 headline) {0,8}%" -f $pl.portfolio_volatility_pct)
  Write-Host ("  plan volatility (strategy long-run) {0,8}%" -f $pl.portfolio_volatility_pct_strategy_longrun)
  Write-Host '  both of those change below; the second one goes away entirely'
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the withdrawal guard, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_book_volatility_is_not_rescaled_by_exposure.py tests/test_the_forward_numbers_say_what_they_are.py
if ($LASTEXITCODE -ne 0) { throw 'the withdrawal guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 56: the ops track, and PR 55.1 withdrawn one deploy after it shipped

PR 55.1 is retracted. It claimed the 23-point gap between /plan (44.62) and the
realised /portfolio/risk (67.48) was explained by exposure: a sleeve in the
market a fraction f of the time and FLAT otherwise satisfies

    sigma_in = sigma_measured / sqrt(f)

so the plan should publish the rescaled figure. It reconciled to half a point -
68.02 against 67.48 - and that reconciliation was treated as proof.

WHY IT IS WRONG. The scaling requires the out-of-market days to be flat and
they are not. strategy_backtest._weights_or_off hands the sleeve its BASE when
the rule is off, never cash, and says so in its own docstring: "a swing rule is
an OVERLAY -- it decides between the aggressive instrument and the core
holding, not between the aggressive instrument and being out of the market."
Neither live sleeve declares risk_off. Measured on the shipped engine over
2016-09-06 to 2026-09-04, 2514 sessions, adjusted closes:

  sleeve            in mkt   measured   TRUE in-mkt   out-of-mkt   formula
  btm_swing_dip      15.6%     33.91%      67.97%       22.46%      85.96%
  btm_trend_soxl     74.0%     83.50%      93.95%       40.97%      97.05%

The variance decomposition closes: f*sigma_in^2 + (1-f)*sigma_out^2 gives
33.84% against 33.91% measured. So this is not a matter of interpretation, and
the half-point agreement came from two errors of opposite sign cancelling -
which is the specific reason this defect was worse than an ordinary one. It
manufactured its own confirmation.

NOT REPLACED WITH A CORRECTED VERSION. Blending the TRUE in-market figures
gives 60.01% against the realised 67.48% - below the portfolio it is supposed
to bound, which a weighted average of the same components over the same period
cannot be. A second difference is doing work and PR 55 named only one: the
components are ten-year backtests and the realised figure is 251 days. Two
numbers over different windows need not obey a bound derived for one.

WHAT SHIPS INSTEAD.

  * volatility_conditional_pct / _method / _floored, EXPOSURE_FLOOR_PCT and
    _in_market_volatility are removed.
  * /plan publishes one volatility again, the blend, and
    portfolio_volatility_pct_strategy_longrun goes with the number that
    displaced it.
  * The blend carries volatility_window: "strategy backtests, full measured
    window". That label's absence is how a ten-year average came to be compared
    against a 251-day measurement as though they were rivals.
  * exposure_pct STAYS on each component, re-commented as reported and not used
    to scale anything. It is a true and useful fact about a sleeve, and the
    analysis that retired the formula is worth being able to redo.
  * portfolio_risk.analyze no longer calls the gap a contradiction. It states
    the bound, names both candidate causes, and says which figure describes the
    book held today.

BLAST RADIUS WHILE IT WAS LIVE: one deploy, and cosmetic. _portfolio_stats
feeds eight screens, so the plan headline read 68.02 where it now reads 44.62.
Nothing sized a trade from it - book_target.compose() and the funding layer use
weights and NAV, not this figure - and /portfolio/risk computed its own
covariance throughout, so VaR, beta and the goal projection were never touched.

PINNED by tests/test_the_book_volatility_is_not_rescaled_by_exposure.py, nine
tests. Five fail against the reconstructed PR 55.1 code: the withdrawn keys,
the floor constant, the /plan headline, the window label, the note's wording.
Four are structural pins that pass on both sides and exist to make the NEXT
attempt notice - each live sleeve declares a base and no risk_off,
_weights_or_off returns that base rather than an empty book, and exposure_pct
scales nothing. A future strategy that genuinely sits in cash will declare
risk_off, and those tests will tell whoever adds it that the arithmetic differs
for that one and not for these.

THE OPS HALF. scripts/ops/ops-check.ps1 runs 14 read-only checks reporting
DONE / TODO / MANUAL / UNKNOWN across branch protection, the agent key, the
VAPID pin, DATABASE_PUBLIC_URL, the three rotations and the deploy invariants
from PR 49/52; it changes nothing and the checks it cannot answer without a
secret say UNKNOWN rather than guessing. scripts/ops/OPS_RUNBOOK.md puts the
same items in the order to do them, branch protection first because it is two
minutes and the only one that prevents a class of incident rather than reducing
exposure to one - and it records why: git push origin main deploys to
production, with no step in between. The Postgres password rotation is
explicitly recommended NOT now; it is the one item whose failure mode is an
outage, and it should not share a week with a deploy-gate change.

Gates: 1670 passed on SQLite, 1670 on PostgreSQL against a dropped and
recreated database, ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/measured_assumptions.py app/services/portfolio_risk_service.py
git add -- app/api/routes/plan.py app/engines/portfolio_risk.py
git add -- tests/test_the_book_volatility_is_not_rescaled_by_exposure.py
git add -- tests/test_the_book_volatility_matches_todays_exposure.py
git add -- scripts/ops/ops-check.ps1 scripts/ops/OPS_RUNBOOK.md
git add -- STATUS.md scripts/deploy/ship-pr56-ops-and-the-55-1-withdrawal.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------------- did the retraction land?
if ($haveKey) {
  Write-Host '=== 5. the numbers after the withdrawal ===' -ForegroundColor Cyan
  $pl2 = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  $r2  = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 150

  Write-Host ("  plan volatility (the blend)  {0,8}%" -f $pl2.portfolio_volatility_pct)
  Write-Host ("  realised volatility          {0,8}%" -f $r2.annualized_volatility_pct)
  Write-Host ("  assumed (now the blend)      {0,8}%" -f $r2.assumed_volatility_pct)

  $longrun = $pl2.PSObject.Properties['portfolio_volatility_pct_strategy_longrun']
  if ($null -eq $longrun) {
    Write-Host '  strategy_longrun key is GONE, as intended' -ForegroundColor Green
  } else {
    Write-Host '  STILL PRESENT: portfolio_volatility_pct_strategy_longrun' -ForegroundColor Red
    Write-Host '  the withdrawal did not reach production - check the deploy SHA' -ForegroundColor Red
  }

  $win = $pl2.assumptions.volatility_window
  if ($win) {
    Write-Host ("  volatility_window: {0}" -f $win) -ForegroundColor Green
  } else {
    Write-Host '  volatility_window MISSING - the blend is unlabelled again' -ForegroundColor Red
  }

  if ($r2.volatility_exceeds_assumption) {
    Write-Host ''
    Write-Host '  FLAGGED AGAIN, and that is CORRECT.' -ForegroundColor Green
    Write-Host '  The gap is real and unexplained; the app no longer pretends' -ForegroundColor Green
    Write-Host '  to know which of the two causes it is. The note:' -ForegroundColor Green
    Write-Host ("  {0}" -f $r2.volatility_note)
  } else {
    Write-Host ''
    Write-Host '  NOT FLAGGED. Unexpected after the withdrawal - the blend' -ForegroundColor Yellow
    Write-Host '  should sit well below the realised figure on this book.' -ForegroundColor Yellow
    Write-Host '  Check that the plan volatility above really moved to ~44.6.' -ForegroundColor Yellow
  }

  $gap = [math]::Abs($pl2.portfolio_volatility_pct - $r2.annualized_volatility_pct)
  Write-Host ''
  Write-Host ("  plan vs actual: {0:N2} points apart, and named rather than explained" -f $gap)
}

Write-Host ''
Write-Host 'PR 56 SHIPPED.' -ForegroundColor Green
Write-Host '  Then run: .\scripts\ops\ops-check.ps1'
Write-Host '  Then read: scripts\ops\OPS_RUNBOOK.md - branch protection first, 2 minutes.'
Write-Host '  Safe to delete when you like: _to_delete\ (stale index.lock files plus'
Write-Host '  the retired test_the_book_volatility_matches_todays_exposure.py).'
