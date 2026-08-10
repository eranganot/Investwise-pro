# Smoke test - PHASE 9. NON-DESTRUCTIVE (read-only; nothing is bought or sold).
#
#   .\scripts\smoke\smoke-phase9.ps1

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
$H = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Api($method, $path) { try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec 90 } catch { Write-Host "  HTTP $($_.Exception.Response.StatusCode.value__) on $method $path" -ForegroundColor DarkRed; return $null } }

Write-Host "`n=== PHASE 9 SMOKE ===" -ForegroundColor Magenta

# --- 1. expected return is no longer zero -----------------------------------
Write-Host "`n1. Expected return is grounded, not zero" -ForegroundColor Cyan
$plan = Api GET '/api/v1/plan'
if ($null -eq $plan) { Write-Host "API unreachable - stop." -ForegroundColor Red; return }
$roi = $plan.portfolio_expected_roi_pct
Write-Host "   portfolio_expected_roi_pct = $roi   target = $($plan.roi_annual_target_pct)   on_track = $($plan.roi_on_track)" -ForegroundColor DarkGray
if ($null -eq $roi) { Bad "expected ROI is null" } elseif ($roi -gt 0) { Ok "expected ROI is $roi%/yr (was ~0 from the 'or 0.0' fallback)" } else { Bad "still $roi%/yr - the character fallback is not being applied" }
# ~30% cash at 3% plus equities at 7-9.5% should land roughly 5-9%/yr.
if ($roi -ge 3 -and $roi -le 12) { Ok "plausible for a 30%-cash equity book" } else { Skip "review: $roi%/yr is outside the expected 3-12 band" }

# --- 2. risk score now reflects the actual holdings -------------------------
Write-Host "`n2. Risk score reflects real instruments" -ForegroundColor Cyan
$h = Api GET '/api/v1/health-check'
Write-Host "   score $($h.wealth_health_score)  risk $($h.risk_score)  tax $($h.tax_efficiency_score)  spread $($h.diversification_score)  cash $($h.liquidity_score)" -ForegroundColor DarkGray
Write-Host "   avg volatility $($h.avg_volatility_pct)%  vs your cap $($h.volatility_cap_pct)%" -ForegroundColor DarkGray
if ($h.avg_volatility_pct -gt 15.5) { Ok "volatility $($h.avg_volatility_pct)% is no longer the flat 15% placeholder" } elseif ($h.avg_volatility_pct -eq 15) { Bad "still exactly 15% - placeholder still in use" } else { Skip "volatility $($h.avg_volatility_pct)% - check it matches your mix" }
if ($null -ne $h.max_achievable) { Ok "max_achievable = $($h.max_achievable) (no hidden ceiling)" } else { Bad "phase 3 fields missing" }

# --- 3. the concentration cap is applied ------------------------------------
Write-Host "`n3. Concentration cap holds" -ForegroundColor Cyan
$port = Api GET '/api/v1/portfolio'
# Read the ACTUAL cap from the plan. Hardcoding 0.25 made a High-risk-tolerance
# book (cap 0.40) look like a cap breach, and the "bug" that followed was mine.
$cap = [double]((Api GET '/api/v1/plan').caps.concentration_cap)
if (-not $cap) { $cap = 0.25 }
Write-Host "   your concentration cap is $([math]::Round($cap*100,1))% (from risk tolerance)" -ForegroundColor DarkGray
if ($null -eq $port) { Skip "portfolio call failed - cannot judge the cap (NOT a pass)" } else {
$nav = [double]$port.nav_ils
$w = @{}; foreach ($p in $port.positions) { $w[$p.ticker] = [double]$p.value_ils / $nav }
$over = @($w.Keys | Where-Object { $w[$_] -ge $cap -and $_ -ne 'CASH' })
Write-Host "   at/over the cap: $(if ($over) { ($over | ForEach-Object { "$_ $([math]::Round($w[$_]*100,1))%" }) -join ', ' } else { 'none' })" -ForegroundColor DarkGray }
$recs = Api GET '/api/v1/recommendations'
if ($null -eq $recs) { Bad "recommendations call failed - checks below are meaningless, not passing" }
$card = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' })[0]
if ($null -eq $card) { Skip "no redeploy card (cash may be at the floor)" } else {
  $legs = $card.apply.legs
  $legs | Format-Table ticker, amount_ils, reason -AutoSize | Out-String | Write-Host
  $bad = @($legs | Where-Object { $over -contains $_.ticker })
  if ($bad.Count -eq 0) { Ok "no leg tops up a holding already at the cap" } else { Bad "$(($bad | ForEach-Object { $_.ticker }) -join ', ') is over the cap and still being bought" }
  $amts = @($legs | ForEach-Object { [math]::Round($_.amount_ils, 2) } | Select-Object -Unique)
  if ($legs.Count -le 1 -or $amts.Count -gt 1) { Ok "legs are individually sized, not one flat split" } else { Bad "all legs identical ($($amts[0])) - weights are still reading as zero" } }

# --- 4. one card per pot of cash --------------------------------------------
Write-Host "`n4. One card per pot of cash" -ForegroundColor Cyan
$cashCards = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' -or $_.apply.kind -eq 'rebalance_to_objective' -or $_.title -like '*idle cash*' })
$legTickers = @(); if ($card) { $legTickers = @($card.apply.legs | ForEach-Object { $_.ticker }) }
$dupBuys = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'buy_funded' -and $legTickers -contains $_.apply.ticker })
Write-Host "   cash-related cards: $($cashCards.Count); competing sized buys: $($dupBuys.Count)" -ForegroundColor DarkGray
# A check that can pass on missing data is worse than no check: an earlier run
# reported "no competing cash cards" when the API had simply timed out.
if ($null -eq $recs -or $null -eq $recs.recommendations) { Skip "no recommendations payload - cannot judge (NOT a pass)" } else {
$recs.recommendations | Select-Object id, dimension, title | Format-Table -AutoSize | Out-String | Write-Host
if ($cashCards.Count -le 1) { Ok "no competing cash cards" } else { Bad "$($cashCards.Count) cards spend the same cash: $(($cashCards | ForEach-Object { $_.title }) -join ' | ')" }
if ($dupBuys.Count -eq 0) { Ok "no duplicate sized buy for a ticker the redeploy card funds" } else { Bad "duplicate buy card(s): $(($dupBuys | ForEach-Object { $_.title }) -join ' | ')" } }

# --- 5. Gemini (billing, not code) ------------------------------------------
Write-Host "`n5. AI features" -ForegroundColor Cyan
$diag = Api GET '/api/v1/adversary/diagnostics'
if ($null -eq $diag) { Skip "diagnostics unavailable" } elseif ($diag.ok) { Ok "Gemini reachable - AI summary and Ask InvestWise should work" } else { Skip "Gemini still failing: $($diag.error -replace '\s+',' ' | ForEach-Object { $_.Substring(0, [Math]::Min(120, $_.Length)) })" ; Write-Host "   (429 = top up prepayment credits at https://ai.studio/projects - not a code fix)" -ForegroundColor DarkGray }

Write-Host "`n=== $pass passed, $fail failed, $skip skipped ===`n" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
