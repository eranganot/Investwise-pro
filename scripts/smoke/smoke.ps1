# InvestWise Pro - deploy smoke test (phases 1 + 2). NON-DESTRUCTIVE.
# Nothing in your book changes. Safe to paste into the console OR run as a file:
#   .\scripts\smoke\smoke.ps1
# Every conditional is kept on a single line so console-paste works too.

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
# Auth: REQUIRE_AUTH is on in production. The key is read from the environment,
# never hardcoded here -- this file is committed to the repo.
#   $env:IW_AGENT_KEY = "<your AGENT_API_KEY>"   (set it in the shell, not here)
$H = @{}
if ($env:IW_AGENT_KEY) { $H['x-agent-key'] = $env:IW_AGENT_KEY }
elseif ($env:IW_TOKEN)  { $H['Authorization'] = "Bearer $($env:IW_TOKEN)" }
else { Write-Host "No credential found. Set `$env:IW_AGENT_KEY first - expect 401 otherwise." -ForegroundColor Yellow }
$pass = 0; $fail = 0; $skip = 0
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Api($method, $path) { try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec 45 } catch { Write-Host "  HTTP $($_.Exception.Response.StatusCode.value__) on $method $path" -ForegroundColor DarkRed; return $null } }

Write-Host "`n=== SMOKE: $BaseUrl ===" -ForegroundColor Magenta

# --- 0. is the API reachable at all -----------------------------------------
Write-Host "`n0. Reachability" -ForegroundColor Cyan
$port = Api GET '/api/v1/portfolio'
if ($null -eq $port) { Write-Host "`nAPI unreachable - stop here, nothing below will be meaningful." -ForegroundColor Red; return }
Ok "GET /api/v1/portfolio responded ($($port.positions.Count) positions)"

# --- 1. PHASE 1: cash is no longer priced as a US bank stock ----------------
Write-Host "`n1. PHASE 1 - cash pricing guard" -ForegroundColor Cyan
$cashApi = (Api GET '/api/v1/portfolio/cash').cash_ils
$cashCard = $port.cash_ils
Write-Host "   dashboard cash_ils = $cashCard   |   edit-modal cash = $cashApi" -ForegroundColor DarkGray
if ($null -eq $cashApi) { Skip "cash endpoint returned nothing" } elseif ([math]::Abs([double]$cashCard - [double]$cashApi) -lt 1.0) { Ok "the two cash figures agree (this was the 521,904 vs 1,934.52 bug)" } else { Bad "still disagree: card=$cashCard modal=$cashApi - the 30-min repair may not have run yet" }
$cashRow = @($port.positions | Where-Object { $_.ticker -eq 'CASH' })
if ($cashRow.Count -eq 0) { Skip "no CASH row held" } elseif ([double]$cashRow[0].cost_basis -eq 1 -and [double]$cashRow[0].quantity -gt 0) { Ok "CASH row is ILS-native (1 unit = 1 shekel, basis 1.0)" } else { Bad "CASH row basis=$($cashRow[0].cost_basis) - not repaired" }
Write-Host "   NAV $([math]::Round([double]$port.nav_ils,2)) vs invested $([math]::Round([double]$port.invested_ils,2)) = $($port.gain_pct)%" -ForegroundColor DarkGray
if ($null -eq $port.gain_pct) { Skip "no gain_pct" } elseif ([math]::Abs([double]$port.gain_pct) -lt 500) { Ok "gain % is plausible again (was +2153% on inflated cash)" } else { Bad "gain still $($port.gain_pct)% - NAV likely still inflated" }

# --- 2. PHASE 2: is it actually deployed ------------------------------------
Write-Host "`n2. PHASE 2 - deployed?" -ForegroundColor Cyan
$spec = Api GET '/openapi.json'
if ($null -eq $spec) { Skip "could not read /openapi.json" } else { $paths = $spec.paths.PSObject.Properties.Name; if ($paths -contains '/api/v1/rules/events') { Ok "/api/v1/rules/events is registered - phase 2 is live" } else { Bad "/api/v1/rules/events MISSING - phase 2 not deployed; stop and check the push/CI" } }

# --- 3. audit trail endpoint -------------------------------------------------
Write-Host "`n3. Audit trail" -ForegroundColor Cyan
$ev = Api GET '/api/v1/rules/events'
if ($null -eq $ev) { Skip "events endpoint unavailable (expected if phase 2 isn't live)" } elseif ($null -ne $ev.events) { Ok "rule_events table exists - $($ev.events.Count) row(s) logged" } else { Bad "no 'events' key in the response" }

# --- 4. snapshot, evaluate, prove nothing auto-traded ------------------------
Write-Host "`n4. Rules evaluate WITHOUT auto-trading" -ForegroundColor Cyan
$beforeMap = @{}
foreach ($p in $port.positions) { $beforeMap[$p.ticker] = [double]$p.quantity }
$evalRes = Api POST '/api/v1/rules/evaluate'
if ($null -eq $evalRes) { Skip "evaluate endpoint unavailable" } else { Write-Host "   newly triggered this run: $($evalRes.triggered.Count)" -ForegroundColor DarkGray }
$after = Api GET '/api/v1/portfolio'
$drift = @()
foreach ($p in $after.positions) { $was = $beforeMap[$p.ticker]; if ($null -ne $was -and [math]::Abs($was - [double]$p.quantity) -gt 1e-6) { $drift += $p.ticker } }
if ($drift.Count -eq 0) { Ok "no holding moved on its own - execution is one-tap, not automatic" } else { Bad "quantities changed without consent: $($drift -join ', ')" }

# --- 5. firings are recorded with grounded prices ----------------------------
Write-Host "`n5. Firings recorded" -ForegroundColor Cyan
$ev2 = Api GET '/api/v1/rules/events'
if ($null -eq $ev2 -or $ev2.events.Count -eq 0) { Skip "no events yet - no rule is currently through its level" } else { $e = $ev2.events[0]; Write-Host "   newest: $($e.ticker) $($e.rule_type) outcome=$($e.outcome) notified=$($e.notified)" -ForegroundColor DarkGray; if ($e.trigger_price) { Ok "grounded in a real quote ($($e.ticker) @ $($e.trigger_price), target $($e.target_price))" } else { Bad "no trigger_price - not grounded" }; if ($e.triggered_at) { Ok "timestamped $($e.triggered_at)" } else { Bad "no triggered_at" } }

# --- 6. rule cards are executable, not advice-only ---------------------------
Write-Host "`n6. Rule cards actionable" -ForegroundColor Cyan
$recs = Api GET '/api/v1/recommendations'
if ($null -eq $recs) { Skip "recommendations unavailable" } else { $ruleCards = @($recs.recommendations | Where-Object { $_.dimension -eq 'rule' }); if ($ruleCards.Count -eq 0) { Skip "no rule cards on Today right now" } else { foreach ($c in $ruleCards) { $k = $c.apply.kind; if ($k -eq 'sell_position' -or $k -eq 'trim') { Ok "'$($c.title)' -> executable ($k, $($c.apply.shares) sh)" } elseif ($k -eq 'none') { Write-Host "  INFO  '$($c.title)' stays advisory by design (price alert / buy-dip)" -ForegroundColor DarkGray } else { Bad "'$($c.title)' unexpected apply.kind '$k'" } } } }

Write-Host "`n=== $pass passed, $fail failed, $skip skipped ===`n" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
