# ship-pr53-the-risk-report-is-in-shekels.ps1
# PR 53 - the risk report was denominated in dollars. Run after PR 51.
#
# Found while pulling live numbers to answer the open volatility decision -
# which is the decision it turns out to have been corrupting.
#
# POST /api/v1/portfolio/risk returned "nav": 7437.82 on a book /portfolio
# values at 21,207.78 ILS, at the same moment, on the same commit.
#
# ROOT CAUSE, proven by arithmetic rather than inferred. portfolio_risk sized
# every holding as quantity x current_price and stopped. current_price is in
# the position's TRADING currency. Drop the rate and add the five rows up:
#
#   TQQQ 2,474.15 + SOXL 2,464.69 + VTI 1,131.87 + BND 748.36 + CASH 618.65
#   = 7,437.72   against a reported 7,437.82
#
# The ten agorot are the displayed prices rounding. That is an identity.
#
# THE PART THAT MATTERS MOST is not the label on the NAV. Dropping the rate is
# a common factor across the four USD names, so their weights relative to one
# another survived it - but CASH is already in shekels, so it was scaled up by
# a rate it never needed: 8.3% of the risk book against 2.9% of the real one.
# Cash has no volatility, so an over-weighted cash line drags portfolio
# volatility and beta DOWN. The endpoint was UNDERSTATING the risk it exists to
# measure, which means the 63.85% volatility reading - already 2.5x the book's
# own 25% cap - was the optimistic version.
#
# The fix is a call, not a formula: fx.position_value_ils already existed, and
# its docstring already said why. This was its sixth call site and the one that
# disagreed.
#
# Also in here: ship-pr50 called /portfolio/risk as a GET. It is a POST route.
# The first run threw 405 and aborted the smoke before the concurrency check.
#
# Gates already run on these exact bytes: 1641 on SQLite, 1641 on PostgreSQL
# (database dropped and recreated first), ruff clean.
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

# ------------------------------------------------- measure the bug, live, first
$navBefore = $null
if ($haveKey) {
  Write-Host '=== 0b. the bug, on production, before the fix ===' -ForegroundColor Cyan
  $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  $r = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 90
  $navBefore = $r.nav
  Write-Host ("  /portfolio      NAV  {0,12:N2} ILS" -f $p.nav_ils)
  Write-Host ("  /portfolio/risk NAV  {0,12:N2}     <- same book, same moment" -f $r.nav) -ForegroundColor Yellow
  Write-Host ("  ratio                {0,12:N4}" -f ($r.nav / $p.nav_ils)) -ForegroundColor Yellow
  Write-Host ("  var_95_1d            {0}%  =  {1:N2} ILS as reported" -f $r.var_95_1d_pct, $r.var_95_1d_ils)
  Write-Host ("                                {0:N2} ILS against the real NAV" -f ($r.var_95_1d_pct / 100 * $p.nav_ils)) -ForegroundColor Yellow
  Write-Host ("  annualized_volatility {0}%   (book cap: 25%)" -f $r.annualized_volatility_pct)
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard, then the full suite ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_risk_report_is_in_shekels.py
if ($LASTEXITCODE -ne 0) { throw 'the shekel guard failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 53: the risk report was denominated in dollars

POST /api/v1/portfolio/risk returned "nav": 7437.82 on a book /portfolio values
at 21,207.78 ILS, at the same moment, on commit 6f3cc4f.

Root cause, proven by arithmetic reconciliation rather than inferred:
portfolio_risk sized every holding as quantity x current_price and stopped.
current_price is in the position's TRADING currency. Drop the rate and add the
same five rows up -

  TQQQ 2,474.15 + SOXL 2,464.69 + VTI 1,131.87 + BND 748.36 + CASH 618.65
  = 7,437.72

- against a reported 7,437.82. The ten agorot are the displayed prices
rounding. That is an identity, not a resemblance.

Four consequences, in ascending order of how much they matter:

  1. var_95_1d_ils is var95 * nav, so the shekel loss on screen read 480 ILS
     against a real 6.45% of 21,207.78, which is 1,368 ILS.
  2. The Monte Carlo starts at nav, so median_terminal, p10 and p90 were all
     scaled by 0.35.
  3. prob_reach compares that scaled distribution against the plan's REAL
     shekel target. It is the one number the error made pessimistic, and it is
     the one the UI renders as a percentage.
  4. The weights were wrong for cash, and only for cash. Dropping the rate is a
     common factor across the four USD names, so their weights relative to one
     another survived it. CASH is already in shekels, so it was scaled up by a
     rate it never needed: 8.3% of the risk book against 2.9% of the real one.
     Cash has no volatility, so an over-weighted cash line drags portfolio
     volatility and beta DOWN. The endpoint was understating the risk it exists
     to measure - which means the 63.85% volatility reading, already 2.5x the
     book's own 25% cap, was the optimistic version.

Why it was silent: every figure it produces is internally consistent, and
report["nav"] is not displayed beside the portfolio NAV on any screen, so the
one cross-check that would have caught it in a glance is not there.

The fix is a call, not a formula. fx.position_value_ils already exists and its
docstring already anticipated this: "Five call sites computed
quantity * current_price * fx_rate(...) inline. The formula is not the hard
part; agreeing on it is. A money invariant that measures the book with a
different expression from the one the user is shown is not an invariant." This
was the sixth call site and the one that disagreed. It is offloaded as well,
per PR 50 - fx_rate reaches guarded_fx underneath.

Found and NOT fixed here, because it is a blind spot this exposed in PR 50's
guard: fourteen async def sites call fx_rate / fx_rate_with_status directly,
which reaches the same blocking provider one level down. The AST guard matches
guarded_* by name and does not follow the wrapper. Lower stakes than PR 50 -
the USD/ILS rate is usually served from the breaker's cache - but it is the
same fault in money-moving code and deserves its own gated PR rather than a
rider on this one.

Also here: ship-pr50 called /portfolio/risk as a GET. It is a POST route. The
first run threw 405 there and aborted the smoke before the concurrency check
ran. The deploy was already verified by then, so nothing was at risk, but a
smoke that stops early reports less than it claims.

Gates: 1641 passed on SQLite, 1641 on PostgreSQL, ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/portfolio_risk_service.py tests/test_the_risk_report_is_in_shekels.py STATUS.md
git add -- scripts/deploy/ship-pr50-the-loop-stays-free.ps1 scripts/deploy/ship-pr53-the-risk-report-is-in-shekels.ps1
# The ops script goes in here rather than sitting untracked, which is the
# thing PR 51 was about. It handles no secrets - it reads the compare the
# app already publishes at /api/v1/push/status.
git add -- scripts/ops/verify-vapid-pin.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# --------------------------------------------- the same measurement, after
if ($haveKey) {
  Write-Host '=== 5. the same two numbers, after ===' -ForegroundColor Cyan
  $p2 = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  $r2 = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Method Post -Headers $apiHeaders -Body '{}' -ContentType 'application/json' -TimeoutSec 90
  Write-Host ("  /portfolio      NAV  {0,12:N2} ILS" -f $p2.nav_ils)
  Write-Host ("  /portfolio/risk NAV  {0,12:N2} ILS" -f $r2.nav)
  $gap = [math]::Abs($r2.nav - $p2.nav_ils)
  if ($gap -gt ($p2.nav_ils * 0.01)) {
    Write-Host ("  STILL {0:N2} ILS APART - the fix did not take. Stop and look." -f $gap) -ForegroundColor Red
  } else {
    Write-Host '  One book, one NAV.' -ForegroundColor Green
    if ($null -ne $navBefore) {
      Write-Host ("  was {0:N2}, now {1:N2}" -f $navBefore, $r2.nav) -ForegroundColor Green
    }
  }
  Write-Host ''
  Write-Host ("  var_95_1d             {0}%  =  {1:N2} ILS" -f $r2.var_95_1d_pct, $r2.var_95_1d_ils)
  Write-Host ("  annualized_volatility {0}%   (book cap: 25%)" -f $r2.annualized_volatility_pct) -ForegroundColor Yellow
  Write-Host ("  beta                  {0}   structural {1}" -f $r2.beta, $r2.beta_validation.structural_beta) -ForegroundColor Yellow
  Write-Host ''
  Write-Host '  These are the corrected numbers, and cash is no longer inflated, so' -ForegroundColor Yellow
  Write-Host '  volatility and beta should both be HIGHER than before rather than' -ForegroundColor Yellow
  Write-Host '  lower. That is the open volatility decision, now on a measurement' -ForegroundColor Yellow
  Write-Host '  that is worth deciding from.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'PR 53 SHIPPED.' -ForegroundColor Green
