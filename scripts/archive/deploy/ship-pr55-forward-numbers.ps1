# ship-pr55-forward-numbers.ps1
# PR 55 - the forward numbers say what they are. Run after PR 54.1.
#
# Two numbers the app presents as forecasts, neither of which stated its basis.
#
# 1. THE GOAL PROJECTION EXTRAPOLATED THE TRAILING YEAR. The drift was
#    port_rets.mean() * 252 - the last 251 trading days annualised, 107.08%/yr
#    on this book - pushed five years through a GBM. That produced
#    prob_reach 0.9604, a median terminal of 1,407,623 ILS and a p90 of
#    9,601,434 ILS, on a book worth 21,208 ILS. The UI renders the first as
#    "Reach 102,973 in 5y - 96%".
#
#    The plan already held a stated forward return with provenance and a caveat
#    that says a measured CAGR is not a forecast. Nothing read it. It does now,
#    and None means NO projection rather than a fallback to the trailing mean.
#
#    THE CONVENTION, which moves the answer more than the fix does: drift_pct
#    is a CAGR (measured_assumptions builds it from metrics["cagr_pct"]), so it
#    is compounded directly and the -0.5*sigma^2 drag is NOT applied twice.
#    Subtracting it anyway would have cancelled the whole 22.18% assumption,
#    because sigma is 67.5% and 0.5*sigma^2 is 22.8%.
#
#    EXPECT THE NUMBER ON THE SCREEN TO FALL HARD, and that is the fix working:
#      prob_reach       0.9604 -> about 0.37
#      median_terminal  1,407,623 -> about 63,000
#
# 2. THE PLAN'S VOLATILITY IS NOT A PORTFOLIO VOLATILITY. blend() computes a
#    NAV-weighted AVERAGE of component volatilities. Expected return is linear
#    so blending returns that way is right; volatility is not. sqrt(w'Sw)
#    equals sum(w.sigma) only at perfect correlation and is strictly smaller
#    otherwise, so 44.62 is an UPPER BOUND, not an estimate. The payload says
#    so now.
#
# 3. WHICH MAKES PLAN VS ACTUAL A CONTRADICTION, NOT A DIFFERENCE. Realised
#    67.48 is ABOVE the 44.62 upper bound. Under those component volatilities
#    the book cannot be that volatile - so at least one component understates
#    what the book is doing. Flagged on the risk endpoint, which is the one
#    place both numbers already exist.
#
# Gates already run on these exact bytes: 1661 SQLite, 1661 PostgreSQL, ruff
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
  Write-Host '=== 0b. the numbers before ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 120
  Write-Host ("  trailing return       {0}%" -f $r.annualized_return_pct)
  Write-Host ("  realised volatility   {0}%" -f $r.annualized_volatility_pct)
  if ($r.goal) {
    Write-Host ("  prob_reach            {0}" -f $r.goal.prob_reach) -ForegroundColor Yellow
    Write-Host ("  median_terminal       {0:N0} ILS" -f $r.goal.median_terminal) -ForegroundColor Yellow
    Write-Host ("  p90_terminal          {0:N0} ILS" -f $r.goal.p90_terminal) -ForegroundColor Yellow
  }
  $pl = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  plan volatility       {0}%   (vs realised above)" -f $pl.portfolio_volatility_pct)
  Write-Host ("  plan expected return  {0}%" -f $pl.portfolio_expected_roi_pct)
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard and the engine suite, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_forward_numbers_say_what_they_are.py tests/test_portfolio_risk.py tests/test_the_risk_report_is_in_shekels.py
if ($LASTEXITCODE -ne 0) { throw 'the forward-number guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 55: the forward numbers say what they are

Two numbers the app presents as forecasts, neither of which stated its basis.
Measured on production 6044124, one book, one moment.

1. THE GOAL PROJECTION EXTRAPOLATED THE TRAILING YEAR.

POST /api/v1/portfolio/risk returned annualized_return_pct 107.08 and a goal
block of prob_reach 0.9604, median_terminal 1,407,623 ILS, p90_terminal
9,601,434 ILS - on a book worth 21,208 ILS. index.html:2890 renders the first
of those as "Reach 102,973 in 5y - 96%". The drift was port_rets.mean() * 252,
the last 251 trading days annualised, pushed five years through a GBM. A 3x
leveraged NASDAQ-and-semiconductor book after a good year is the worst possible
input for that, and the number it produced was the most confident thing on the
screen.

The plan already held a stated forward return with provenance and a caveat:
expected_return_pct 22.18, return_basis measured_full, 97.1% of the book
measured, and a caveat reading "A measured CAGR is what happened over one
window, not what will happen." Nothing read it. analyze() now takes drift_pct
and builds the projection from that and nothing else.

None means no projection, not a fallback. The old fallback WAS the trailing
mean, so keeping it as a fallback would preserve the defect and hide it better.
The payload omits goal and says why in goal_omitted_reason. The trailing figure
is still reported as trailing_return_pct - a name that cannot be read as a
forecast - because hiding it would trade one unstated basis for another.

THE CONVENTION DECISION, which moves the answer more than the fix does:
drift_pct is a CAGR. measured_assumptions builds each component's return from
metrics["cagr_pct"] and its caveat calls it one. A CAGR is already geometric -
the -0.5*sigma^2 drag is inside it - so it is compounded directly and the drag
is NOT applied twice. Subtracting it anyway would have been the
conservative-looking mistake: sigma is 67.5%, 0.5*sigma^2 is 22.8%, and it
would have cancelled the entire 22.18% assumption and put the median five-year
outcome below today's NAV for arithmetic reasons rather than for anything
measured. Asserted in a test, not left in a comment.

What production will report after this:
  prob_reach       0.9604    -> 0.3683
  median_terminal  1,407,623 -> 63,003
  p10_terminal     204,801   -> 9,166
  p90_terminal     9,601,434 -> 429,767

2. THE PLAN'S VOLATILITY IS NOT A PORTFOLIO VOLATILITY.

measured_assumptions.blend computes sum(w*sigma)/sum(w), a NAV-weighted average
of component volatilities. Reconciled exactly against the live components:
35.09% x 83.95 + 35.22% x 34.24 + 26.77% x 11.59 + 2.92% x 0.00 = 44.62.

Expected return is linear, so blending returns that way is correct and 22.18
checks out the same way. Volatility is not. Portfolio volatility is
sqrt(w'Sw), and a weighted average of component volatilities equals it only
when every pair is perfectly correlated - otherwise the average is strictly
larger. It is an UPPER BOUND, not an estimate. The payload now says so:
volatility_method "weighted_average", volatility_is_upper_bound true.

3. WHICH MAKES PLAN VS ACTUAL A CONTRADICTION, NOT A DIFFERENCE.

  /plan.portfolio_volatility_pct         44.62   upper bound
  /portfolio/risk.annualized_volatility  67.48   realised, real covariance

The realised volatility EXCEEDS the upper bound. Under those component
volatilities the book cannot be more volatile than 44.62; it measured 67.48. So
at least one component understates what the book is doing - which is what you
would expect when a sleeve's ten-year backtest includes every period it sat in
cash, and the book is holding the leveraged names right now. Two honest
measurements of two different questions, and nothing said which question was
being answered.

The reconciliation lands on the risk endpoint rather than /plan, because /plan
has no price history and giving it some would put N provider fetches on a hot
endpoint. The risk call already reads the plan and already computes the
covariance, so it is the one place the two can be compared without paying for
anything twice: assumed_volatility_pct, volatility_exceeds_assumption, and a
volatility_note that states the bound argument.

book_assumptions's own docstring already reached for half of this, about the
core: "a per-ticker table has no covariance term - so it overstates the core's
volatility by construction." True of the whole blend.

One existing test asserted the behaviour removed here and is rewritten rather
than deleted: test_goal_probability_present_with_target passed nav, target and
years and expected a probability - and got one, built from the trailing mean of
the history it had just generated. It now asserts both halves, because the
refusal is the part that would regress quietly.

Gates: 1661 passed on SQLite, 1661 on PostgreSQL, ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/engines/portfolio_risk.py app/services/measured_assumptions.py app/services/portfolio_risk_service.py
git add -- tests/test_the_forward_numbers_say_what_they_are.py tests/test_portfolio_risk.py
git add -- STATUS.md scripts/deploy/ship-pr55-forward-numbers.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# --------------------------------------------------------------- the numbers
if ($haveKey) {
  Write-Host '=== 5. the numbers after ===' -ForegroundColor Cyan
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 120

  Write-Host ("  trailing_return_pct   {0}%   (reported, not projected)" -f $r2.trailing_return_pct)
  Write-Host ("  realised volatility   {0}%" -f $r2.annualized_volatility_pct)
  Write-Host ("  assumed volatility    {0}%   (the plan's upper bound)" -f $r2.assumed_volatility_pct)

  if ($r2.volatility_exceeds_assumption) {
    Write-Host '' -ForegroundColor Yellow
    Write-Host '  CONTRADICTION FLAGGED:' -ForegroundColor Yellow
    Write-Host ("  {0}" -f $r2.volatility_note) -ForegroundColor Yellow
    Write-Host ''
  }

  if ($null -eq $r2.goal) {
    Write-Host ("  no goal projection: {0}" -f $r2.goal_omitted_reason) -ForegroundColor Yellow
  } else {
    Write-Host ("  prob_reach            {0}      (was 0.9604)" -f $r2.goal.prob_reach) -ForegroundColor Green
    Write-Host ("  median_terminal       {0:N0} ILS   (was 1,407,623)" -f $r2.goal.median_terminal) -ForegroundColor Green
    Write-Host ("  p10_terminal          {0:N0} ILS" -f $r2.goal.p10_terminal)
    Write-Host ("  p90_terminal          {0:N0} ILS   (was 9,601,434)" -f $r2.goal.p90_terminal)
    Write-Host ("  built from            {0}% ({1}, {2})" -f $r2.goal.drift_pct, $r2.goal.drift_source, $r2.goal.drift_convention)
    if ($r2.goal.prob_reach -gt 0.9) {
      Write-Host '  STILL ABOVE 90% - the projection is not using the stated rate.' -ForegroundColor Red
      Write-Host '  Check that /plan reports an expected return at all.' -ForegroundColor Red
    }
  }
}

Write-Host ''
Write-Host 'PR 55 SHIPPED.' -ForegroundColor Green
Write-Host '  The probability on the Plan screen will be far lower. That is the'
Write-Host '  fix: it is now built from a stated assumption with a caveat attached,'
Write-Host '  rather than from one good year of a leveraged book.'
