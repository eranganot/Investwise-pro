# smoke-52-to-55.ps1 - one pass over everything shipped from PR 52 onwards.
#
# Each PR gets its own named check, and each check asserts the INVARIANT that PR
# established rather than that the endpoint returned 200. A smoke that only
# proves the app answers is the thing that let Railway report "Deployment
# successful" through a two-hour outage.
#
# Safe to run before a PR has landed: an unmet invariant is reported as NOT YET
# rather than as a crash, so this doubles as a "where am I" check. The exit code
# is non-zero if anything FAILED (shipped and wrong) but zero if things are
# merely NOT YET (not shipped).
#
#   PR 52  the pre-deploy step runs alembic against production
#   PR 53  the risk report is denominated in shekels
#   PR 54  the book targets 7.5% cash and the floor honours it
#   PR 54.1 no card offers to buy cash
#   PR 55  the goal projection comes from a stated assumption, not last year
#
# Usage:
#   .\scripts\smoke\smoke-52-to-55.ps1
#   .\scripts\smoke\smoke-52-to-55.ps1 -BaseUrl https://...   (another env)
#
# Pure ASCII, no backticks.

param(
  [string]$BaseUrl = 'https://investwise-pro-production.up.railway.app',
  [string]$Repo    = 'C:\dev\Investwise-pro'
)

$ErrorActionPreference = 'Stop'

$keyPath = Join-Path $Repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "need $keyPath to call the API" }
$H = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }

$script:Results = @()
function Check {
  param([string]$Pr, [string]$What, [scriptblock]$Test)
  try {
    $r = & $Test
    $status = if ($r.ok) { 'PASS' } elseif ($r.notyet) { 'NOT YET' } else { 'FAIL' }
  } catch {
    $status = 'FAIL'; $r = @{ detail = $_.Exception.Message }
  }
  $colour = switch ($status) { 'PASS' { 'Green' } 'NOT YET' { 'Yellow' } default { 'Red' } }
  Write-Host ("  [{0,-7}] {1}" -f $status, $What) -ForegroundColor $colour
  if ($r.detail) { Write-Host ("            {0}" -f $r.detail) -ForegroundColor DarkGray }
  $script:Results += [pscustomobject]@{ PR = $Pr; What = $What; Status = $status }
}

Write-Host ''
Write-Host "=== smoke: PR 52 -> 55 against $BaseUrl ===" -ForegroundColor Cyan

# ------------------------------------------------------------------ fetch once
$health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30
$ready  = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -TimeoutSec 30
$pf     = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio" -Headers $H -TimeoutSec 60
$plan   = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan" -Headers $H -TimeoutSec 60
$mix    = Invoke-RestMethod -Uri "$BaseUrl/api/v1/mix" -Headers $H -TimeoutSec 60
$risk   = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio/risk" -Method Post -Headers $H -Body '{}' -ContentType 'application/json' -TimeoutSec 150
$recs   = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $H -TimeoutSec 150

Write-Host ''
Write-Host ("serving {0} | env {1} | ready: {2} | NAV {3:N2} ILS across {4} positions" -f $health.commit, $health.environment, $ready.status, $pf.nav_ils, $pf.positions.Count)
Write-Host ''

# --------------------------------------------------------------------- PR 52
Write-Host 'PR 52 - the migrations run before the container starts' -ForegroundColor Cyan
Check '52' 'the database is stamped at an alembic revision' {
  $rev = $health.schema_revision
  if ($null -eq $rev) {
    return @{ ok = $false; detail = 'schema_revision is null - alembic has never run against this database' }
  }
  @{ ok = $true; detail = "schema_revision $rev" }
}
Check '52' 'the stamp is at or beyond 0021 (the pre-deploy step ran a NEW migration)' {
  $rev = [string]$health.schema_revision
  if (-not $rev) { return @{ ok = $false; detail = 'no revision' } }
  $n = 0
  if ($rev -match '^(\d+)') { $n = [int]$Matches[1] }
  if ($n -lt 21) {
    return @{ ok = $false; detail = "stamped at $rev. 0021 shipped with PR 54; if the column exists but the stamp does not, create_all did it and preDeployCommand is being ignored." }
  }
  @{ ok = $true; detail = "$rev - alembic itself moved the stamp" }
}

# --------------------------------------------------------------------- PR 53
Write-Host ''
Write-Host 'PR 53 - the risk report is in shekels' -ForegroundColor Cyan
Check '53' 'the risk NAV equals the portfolio NAV' {
  $gap = [math]::Abs($risk.nav - $pf.nav_ils)
  if ($gap -gt ($pf.nav_ils * 0.01)) {
    return @{ ok = $false; detail = ("risk {0:N2} vs portfolio {1:N2} - a {2:N2} gap, which is the FX rate" -f $risk.nav, $pf.nav_ils, $gap) }
  }
  @{ ok = $true; detail = ("both {0:N2} ILS" -f $risk.nav) }
}
Check '53' 'the shekel VaR is sized off the real NAV' {
  $want = $risk.var_95_1d_pct / 100 * $pf.nav_ils
  $gap = [math]::Abs($risk.var_95_1d_ils - $want)
  if ($gap -gt ($want * 0.02)) {
    return @{ ok = $false; detail = ("var_95_1d_ils {0:N2} but {1}% of NAV is {2:N2}" -f $risk.var_95_1d_ils, $risk.var_95_1d_pct, $want) }
  }
  @{ ok = $true; detail = ("{0}% = {1:N2} ILS" -f $risk.var_95_1d_pct, $risk.var_95_1d_ils) }
}

# --------------------------------------------------------------------- PR 54
Write-Host ''
Write-Host 'PR 54 - the book can hold cash on purpose' -ForegroundColor Cyan
Check '54' 'the plan carries a cash reserve' {
  if ($null -eq $plan.cash_target_pct) {
    return @{ notyet = $true; detail = 'cash_target_pct is null - PR 54 not shipped, or the reserve was never set' }
  }
  @{ ok = $true; detail = ("cash_target_pct {0}" -f $plan.cash_target_pct) }
}
Check '54' 'the book target names Cash' {
  $t = $mix.target_allocation
  if ($null -eq $t.Cash) {
    return @{ notyet = $true; detail = ("target has no Cash key: {0}" -f ($t | ConvertTo-Json -Compress)) }
  }
  @{ ok = $true; detail = ("target {0}" -f ($t | ConvertTo-Json -Compress)) }
}
Check '54' 'the sleeves were not shrunk to make room' {
  $t = $mix.target_allocation
  if ($null -eq $t.Cash) { return @{ notyet = $true; detail = 'no cash target yet' } }
  # Sleeves claim 73% of the book in Equities and must be untouched: the reserve
  # comes out of the core's remainder. Equities target below 73 means the
  # sleeves were scaled, which is the defect book_target.py exists to prevent.
  if ($t.Equities -lt 0.73) {
    return @{ ok = $false; detail = ("Equities target {0} is below the 0.73 the sleeves claim - the reserve was taken from the sleeves" -f $t.Equities) }
  }
  @{ ok = $true; detail = ("Equities {0} >= the sleeves' 0.73" -f $t.Equities) }
}
Check '54' 'the target still sums to 1' {
  $sum = 0.0
  foreach ($p in $mix.target_allocation.PSObject.Properties) { $sum += [double]$p.Value }
  if ([math]::Abs($sum - 1.0) -gt 0.01) {
    return @{ ok = $false; detail = ("sums to {0:N4}" -f $sum) }
  }
  @{ ok = $true; detail = ("{0:N4}" -f $sum) }
}

# ------------------------------------------------------------------- PR 54.1
Write-Host ''
Write-Host 'PR 54.1 - no card offers to buy cash' -ForegroundColor Cyan
Check '54.1' 'no rebalance leg names Cash' {
  $bad = @()
  foreach ($c in $recs.recommendations) {
    if ($c.apply.kind -ne 'rebalance_to_objective') { continue }
    if ($c.apply.asset_class -eq 'Cash') { $bad += "$($c.title): apply.asset_class" }
    foreach ($leg in $c.apply.legs) { if ($leg.asset_class -eq 'Cash') { $bad += "$($c.title): legs" } }
    foreach ($leg in $c.meta.merged_legs) { if ("$leg" -match '(?i)cash') { $bad += "$($c.title): merged_legs '$leg'" } }
  }
  if ($bad.Count -gt 0) { return @{ ok = $false; detail = ($bad -join '; ') } }
  @{ ok = $true; detail = 'none' }
}
Check '54.1' 'no card suggests buying a security to raise cash' {
  $bad = @()
  foreach ($c in $recs.recommendations) {
    $text = (@($c.title, $c.action) + @($c.how)) -join ' '
    if ($text -match '(?i)buy' -and $text -match '(?i)cash' -and $text -match '(?i)\bBIL\b') {
      $bad += $c.title
    }
  }
  if ($bad.Count -gt 0) { return @{ ok = $false; detail = ($bad -join '; ') } }
  @{ ok = $true; detail = 'none' }
}
Check '54.1' 'the cash balance is not screened as a holding' {
  $bad = @($recs.recommendations | Where-Object { $_.dimension -eq 'holding' -and $_.title -match '(?i)cash' })
  if ($bad.Count -gt 0) { return @{ ok = $false; detail = ($bad.title -join '; ') } }
  @{ ok = $true; detail = 'none' }
}
Check '54.1' 'raising cash still has a card while the book is under its reserve' {
  if ($null -eq $mix.target_allocation.Cash) { return @{ notyet = $true; detail = 'no cash target' } }
  if ($mix.current_allocation.Cash -ge $mix.target_allocation.Cash) {
    return @{ ok = $true; detail = 'at or above the reserve, so no card is expected' }
  }
  $raise = @($recs.recommendations | Where-Object { $_.dimension -eq 'liquidity' -or $_.title -match '(?i)raise' })
  if ($raise.Count -lt 1) {
    return @{ ok = $false; detail = 'under the reserve and nothing says so - the fix removed both cards, not one' }
  }
  @{ ok = $true; detail = $raise[0].title }
}

# --------------------------------------------------------------------- PR 55
Write-Host ''
Write-Host 'PR 55 - the forward numbers say what they are' -ForegroundColor Cyan
Check '55' 'the trailing return is reported under a name that is not a forecast' {
  if ($null -eq $risk.trailing_return_pct) {
    return @{ notyet = $true; detail = 'no trailing_return_pct - PR 55 not shipped' }
  }
  @{ ok = $true; detail = ("trailing_return_pct {0}%" -f $risk.trailing_return_pct) }
}
Check '55' 'the goal projection is built from a stated assumption' {
  if ($null -eq $risk.trailing_return_pct) { return @{ notyet = $true; detail = 'PR 55 not shipped' } }
  if ($null -eq $risk.goal) {
    return @{ ok = $true; detail = ("no projection, and it says why: {0}" -f $risk.goal_omitted_reason) }
  }
  if ($null -eq $risk.goal.drift_pct) {
    return @{ ok = $false; detail = 'a goal was produced with no stated drift' }
  }
  if ([math]::Abs($risk.goal.drift_pct - $risk.trailing_return_pct) -lt 0.01) {
    return @{ ok = $false; detail = ("drift {0}% equals the trailing return - still extrapolating last year" -f $risk.goal.drift_pct) }
  }
  $d = "prob_reach {0} from {1}% ({2}), vs a trailing {3}%" -f $risk.goal.prob_reach, $risk.goal.drift_pct, $risk.goal.drift_convention, $risk.trailing_return_pct
  @{ ok = $true; detail = $d }
}
Check '55' 'the stated rate is compounded, not double-discounted' {
  if ($null -eq $risk.goal) { return @{ notyet = $true; detail = 'no goal to check' } }
  $g = $risk.goal.drift_pct / 100.0
  $want = $risk.nav * [math]::Exp($g * $risk.goal.years)
  $ratio = $risk.goal.median_terminal / $want
  if ($ratio -lt 0.90 -or $ratio -gt 1.10) {
    $d = "median {0:N0} vs compounding {1}% for {2}y = {3:N0}" -f $risk.goal.median_terminal, $risk.goal.drift_pct, $risk.goal.years, $want
    return @{ ok = $false; detail = $d }
  }
  $d = "median {0:N0} ILS, which is {1}% compounded for {2}y" -f $risk.goal.median_terminal, $risk.goal.drift_pct, $risk.goal.years
  @{ ok = $true; detail = $d }
}
Check '55' 'the plan assumption is carried into the risk report' {
  if ($null -eq $risk.assumed_volatility_pct) {
    return @{ notyet = $true; detail = 'no assumed_volatility_pct - PR 55 not shipped' }
  }
  @{ ok = $true; detail = ("assumed {0}% vs realised {1}%" -f $risk.assumed_volatility_pct, $risk.annualized_volatility_pct) }
}
Check '55' 'a realised volatility above the assumption is flagged' {
  if ($null -eq $risk.assumed_volatility_pct) { return @{ notyet = $true; detail = 'PR 55 not shipped' } }
  $exceeds = $risk.annualized_volatility_pct -gt $risk.assumed_volatility_pct
  if ($exceeds -and -not $risk.volatility_exceeds_assumption) {
    return @{ ok = $false; detail = 'realised is above the upper bound and nothing says so' }
  }
  if ($exceeds) { return @{ ok = $true; detail = 'flagged, with a note' } }
  @{ ok = $true; detail = 'realised is within the assumption' }
}

# ------------------------------------------------------------------ the book
Write-Host ''
Write-Host 'the book, for the record' -ForegroundColor Cyan
Write-Host ("  NAV                  {0,12:N2} ILS" -f $pf.nav_ils)
Write-Host ("  target               {0}" -f ($mix.target_allocation | ConvertTo-Json -Compress))
Write-Host ("  current              {0}" -f ($mix.current_allocation | ConvertTo-Json -Compress))
Write-Host ("  realised volatility  {0,12}%" -f $risk.annualized_volatility_pct)
Write-Host ("  plan volatility      {0,12}%   (a weighted average, an upper bound)" -f $plan.portfolio_volatility_pct)
Write-Host ("  plan expected return {0,12}%" -f $plan.portfolio_expected_roi_pct)
Write-Host ("  cards on Today       {0,12}" -f $recs.count)
foreach ($c in $recs.recommendations) { Write-Host ("     [{0}] {1}" -f $c.dimension, $c.title) }

# ------------------------------------------------------------------- summary
$failed = @($script:Results | Where-Object { $_.Status -eq 'FAIL' })
$notyet = @($script:Results | Where-Object { $_.Status -eq 'NOT YET' })
$passed = @($script:Results | Where-Object { $_.Status -eq 'PASS' })

Write-Host ''
Write-Host ("=== {0} passed, {1} not yet shipped, {2} FAILED ===" -f $passed.Count, $notyet.Count, $failed.Count) -ForegroundColor Cyan
foreach ($f in $failed) { Write-Host ("  FAILED  PR {0}: {1}" -f $f.PR, $f.What) -ForegroundColor Red }
foreach ($n in $notyet) { Write-Host ("  NOT YET PR {0}: {1}" -f $n.PR, $n.What) -ForegroundColor Yellow }

if ($failed.Count -gt 0) { exit 1 }
Write-Host ''
Write-Host 'Nothing shipped is behaving wrongly.' -ForegroundColor Green
