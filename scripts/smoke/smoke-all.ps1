# FULL SMOKE - every phase (1-10). READ-ONLY: nothing is bought, sold or dismissed.
# Reads real values from the API (never hardcodes caps/floors) and SKIPs or FAILs
# on a null payload rather than passing on missing data.
#
#   .\scripts\smoke\smoke-all.ps1

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
$H = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0; $retried = 0
# A run where calls 1-5 succeeded and everything after returned no HTTP status
# at all turned out to be a Railway REDEPLOY draining the old container, not a
# client or server fault: probing /health, /health/ready and /api/v1/plan
# straight after two back-to-back /recommendations calls returned 0.1s each once
# the deploy had settled. -DisableKeepAlive was tried and made no difference, so
# it is not used -- it only adds TLS handshake churn.
# Hence the preflight below: never interpret a mid-deploy run as a test result.
[Net.ServicePointManager]::DefaultConnectionLimit = 100
[Net.ServicePointManager]::Expect100Continue = $false
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $tmo = 60) {
    # One retry on a no-response failure. If the retry succeeds, the endpoint was
    # never broken and the first attempt died on a stale connection -- reported
    # explicitly so a transport problem is never mistaken for an app bug.
    for ($i = 1; $i -le 2; $i++) {
        try {
            $r = Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec $tmo
            if ($i -eq 2) { Write-Host "  (recovered on retry - transport, not the API)" -ForegroundColor DarkYellow; $script:retried++ }
            return $r
        } catch {
            $c = $_.Exception.Response.StatusCode.value__
            if ($c) { Write-Host "  HTTP $c on $method $path" -ForegroundColor DarkRed; return $null }
            if ($i -eq 1) { Start-Sleep -Milliseconds 800; continue }
            Write-Host "  NO RESPONSE on $method $path (both attempts)" -ForegroundColor DarkRed
            return $null
        }
    }
}

Write-Host "`n===== INVESTWISE FULL SMOKE =====" -ForegroundColor Magenta

# ---------- PREFLIGHT: is the deploy settled? ----------
# /health is async and touches nothing; /health/ready opens a DB connection. If
# either is slow or flaky the container is still booting or draining, and every
# result below would be noise. Refuse to run rather than report a false failure.
Sec "0. Preflight - is the app settled?"
$stable = 0
for ($i = 1; $i -le 5; $i++) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 10 | Out-Null
          Invoke-RestMethod "$BaseUrl/health/ready" -Headers $H -TimeoutSec 10 | Out-Null
          $sw.Stop(); $stable++
          Write-Host "   probe $i OK ($([math]::Round($sw.Elapsed.TotalSeconds,2))s)" -ForegroundColor DarkGray }
    catch { $sw.Stop(); Write-Host "   probe $i FAILED" -ForegroundColor DarkRed }
    Start-Sleep -Milliseconds 600
}
if ($stable -lt 5) {
    Write-Host "`n  ABORTING: only $stable/5 health probes succeeded." -ForegroundColor Red
    Write-Host "  The container is booting, draining or restarting - most likely a deploy" -ForegroundColor Yellow
    Write-Host "  is still in flight. Wait for Railway to go Active, then re-run." -ForegroundColor Yellow
    Write-Host "  (A previous run read a mid-deploy drain as 9 API failures.)`n" -ForegroundColor Yellow
    return
}
Ok "5/5 health probes - deploy is settled, results below are meaningful"

# ---------- PHASE 10: latency ----------
Sec "1. Latency (phase 10 - provider cache TTLs)"
$t1 = [Diagnostics.Stopwatch]::StartNew(); $recs = Api GET '/api/v1/recommendations'; $t1.Stop()
$t2 = [Diagnostics.Stopwatch]::StartNew(); $recs2 = Api GET '/api/v1/recommendations'; $t2.Stop()
Write-Host "   cold $([math]::Round($t1.Elapsed.TotalSeconds,1))s   warm $([math]::Round($t2.Elapsed.TotalSeconds,1))s" -ForegroundColor DarkGray
if ($null -eq $recs) { Bad "recommendations unreachable - most checks below cannot run" } else { Ok "recommendations responded" }
$warm = [math]::Round($t2.Elapsed.TotalSeconds, 1)
# Honest bar: the measured baseline before phase 10 was 24.2s EVERY call. Under
# 8s means the provider cache is working. But 4-5s is still slow for a cached
# endpoint, so anything over 2s is called out rather than quietly passed -- the
# remaining cost is elsewhere in the fan-out (screener / war-room), tracked
# separately. Moving the goalposts to make this green would hide that.
if ($null -eq $recs2) { Skip "second call failed" } elseif ($warm -lt 8) { Ok "warm call ${warm}s (baseline was 24.2s every time)"; if ($warm -gt 2) { Write-Host "   NOTE ${warm}s is still slow for a warm call - provider cache is working, so the remaining cost is elsewhere in the fan-out (open item)" -ForegroundColor DarkYellow } } else { Bad "warm call ${warm}s - provider cache is not surviving between requests" }

# ---------- PHASE 1: cash ----------
Sec "2. Cash is not priced as a stock (phase 1)"
$port = Api GET '/api/v1/portfolio'
if ($null -eq $port) { Skip "portfolio unreachable (NOT a pass)" } else {
  $cashApi = (Api GET '/api/v1/portfolio/cash').cash_ils
  $nav = [double]$port.nav_ils
  Write-Host "   NAV $([math]::Round($nav,2))  invested $([math]::Round([double]$port.invested_ils,2))  gain $($port.gain_pct)%" -ForegroundColor DarkGray
  Write-Host "   card cash $($port.cash_ils)  |  modal cash $cashApi" -ForegroundColor DarkGray
  if ($null -eq $cashApi) { Skip "cash endpoint empty" } elseif ([math]::Abs([double]$port.cash_ils - [double]$cashApi) -lt 1.0) { Ok "both cash figures agree (was 521,904 vs 1,934.52)" } else { Bad "still disagree" }
  if ($null -eq $port.gain_pct) { Skip "no gain_pct" } elseif ([math]::Abs([double]$port.gain_pct) -lt 500) { Ok "gain $($port.gain_pct)% is plausible (was +2153%)" } else { Bad "gain $($port.gain_pct)% - NAV still inflated" } }

# ---------- PHASE 3 + 9: health score ----------
Sec "3. Health score is measured, not placeholder (phases 3 + 9)"
$h = Api GET '/api/v1/health-check'
if ($null -eq $h) { Skip "health-check unreachable (NOT a pass)" } else {
  Write-Host "   score $($h.wealth_health_score)  risk $($h.risk_score)  tax $($h.tax_efficiency_score)  spread $($h.diversification_score)  cash $($h.liquidity_score)" -ForegroundColor DarkGray
  Write-Host "   avg vol $($h.avg_volatility_pct)%  vs budget $($h.volatility_cap_pct)%" -ForegroundColor DarkGray
  if ($h.max_achievable -eq 100) { Ok "no hidden ceiling (thematic=60 constant removed)" } else { Bad "max_achievable = $($h.max_achievable)" }
  if ($h.avg_volatility_pct -eq 15) { Bad "volatility is exactly 15% - placeholder still in use" } elseif ($h.avg_volatility_pct -gt 0) { Ok "volatility $($h.avg_volatility_pct)% derived from real instruments" } else { Skip "no volatility reported" }
  if ($h.tax_efficiency_score -gt 85) { Ok "tax $($h.tax_efficiency_score) exceeds the old hard cap of 85" } else { Skip "tax $($h.tax_efficiency_score) - only meaningful if you have no unharvested losses" } }

# ---------- PHASE 9: expected return ----------
Sec "4. Expected return is grounded (phase 9)"
$plan = Api GET '/api/v1/plan'
if ($null -eq $plan) { Skip "plan unreachable (NOT a pass)" } else {
  $roi = $plan.portfolio_expected_roi_pct
  $cap = [double]$plan.caps.concentration_cap
  Write-Host "   expected ROI $roi%/yr  target $($plan.roi_annual_target_pct)%  on_track $($plan.roi_on_track)" -ForegroundColor DarkGray
  Write-Host "   your concentration cap: $([math]::Round($cap*100,1))%  (risk tolerance $($plan.risk_tolerance))" -ForegroundColor DarkGray
  if ($null -eq $roi) { Bad "expected ROI is null" } elseif ($roi -gt 0) { Ok "expected ROI $roi%/yr (was ~0 from the `or 0.0` fallback)" } else { Bad "still $roi%/yr" } }

# ---------- PHASE 6: cash not tradeable ----------
Sec "5. Cash is not a tradeable holding (phase 6)"
$ev = Api POST '/api/v1/rules/evaluate'
if ($null -eq $ev) { Skip "evaluate unreachable" } else { Ok "evaluation ran (newly triggered: $($ev.triggered.Count))" }
$rules = (Api GET '/api/v1/rules').rules
if ($null -eq $rules) { Skip "rules unreachable (NOT a pass)" } else {
  if (@($rules | Where-Object { $_.ticker -eq 'CASH' -and $_.active }).Count -eq 0) { Ok "no active rule on CASH" } else { Bad "CASH rule still armed" }
  $sug = (Api GET '/api/v1/rules/suggestions').suggestions
  if ($null -eq $sug) { Skip "suggestions unreachable" } elseif (@($sug | Where-Object { $_.ticker -eq 'CASH' }).Count -eq 0) { Ok "suggester never offers rules on CASH" } else { Bad "CASH still suggested" } }

# ---------- PHASE 6 + 8: banner matches reality ----------
Sec "6. Banner matches the cards (phases 6 + 8)"
if ($null -eq $rules -or $null -eq $recs) { Skip "need both rules and recommendations (NOT a pass)" } else {
  $trig = @($rules | Where-Object { $_.triggered -and $_.active })
  $ruleCards = @($recs.recommendations | Where-Object { $_.dimension -eq 'rule' })
  Write-Host "   triggered: $(if ($trig.Count) { ($trig | ForEach-Object { $_.ticker }) -join ', ' } else { 'none' })" -ForegroundColor DarkGray
  if ($trig.Count -eq $ruleCards.Count) { Ok "banner ($($trig.Count)) matches rule cards ($($ruleCards.Count)) - no deadlock" } else { Bad "banner $($trig.Count) vs $($ruleCards.Count) cards" } }

# ---------- PHASE 2 + 5: audit trail ----------
Sec "7. Rule audit trail (phases 2 + 5)"
$events = (Api GET '/api/v1/rules/events?limit=50').events
if ($null -eq $events) { Skip "events unreachable (NOT a pass)" } elseif ($events.Count -eq 0) { Skip "no firings logged yet" } else {
  Ok "$($events.Count) firing(s) logged"
  $res = @($events | Where-Object { $_.outcome -ne 'triggered' })
  if ($res.Count -gt 0) { Ok "outcomes stamped: $(($res | ForEach-Object { $_.outcome } | Select-Object -Unique) -join ', ')" } else { Skip "nothing acted on yet" }
  if ($events[0].trigger_price) { Ok "grounded in a real quote ($($events[0].ticker) @ $($events[0].trigger_price))" } else { Bad "no trigger_price" } }

# ---------- PHASE 7 + 9: redeploy card ----------
Sec "8. Idle cash redeployment (phases 7 + 9)"
if ($null -eq $recs -or $null -eq $port) { Skip "need recommendations + portfolio (NOT a pass)" } else {
  $cap = if ($plan) { [double]$plan.caps.concentration_cap } else { 0.0 }
  $w = @{}; foreach ($p in $port.positions) { $w[$p.ticker] = [double]$p.value_ils / [double]$port.nav_ils }
  $over = @($w.Keys | Where-Object { $w[$_] -ge $cap -and $_ -ne 'CASH' -and $cap -gt 0 })
  Write-Host "   at/over your $([math]::Round($cap*100,1))% cap: $(if ($over) { ($over | ForEach-Object { "$_ $([math]::Round($w[$_]*100,1))%" }) -join ', ' } else { 'none' })" -ForegroundColor DarkGray
  $card = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' })[0]
  if ($null -eq $card) { Skip "no redeploy card (cash may be at the floor)" } else {
    Ok "card: $($card.title)"
    $legs = $card.apply.legs
    $legs | Format-Table ticker, amount_ils, reason -AutoSize | Out-String | Write-Host
    if (@($legs | Where-Object { $_.ticker -eq 'CASH' }).Count -eq 0) { Ok "never buys CASH with cash" } else { Bad "a leg buys CASH" }
    if (@($legs | Where-Object { $_.amount_ils -lt 250 }).Count -eq 0) { Ok "no sub-minimum dust trades" } else { Bad "a leg is under the 250 minimum" }
    if (@($legs | Where-Object { $over -contains $_.ticker }).Count -eq 0) { Ok "no leg tops up a holding at/over YOUR cap" } else { Bad "tops up an over-cap holding" }
    $sum = ($legs | Measure-Object amount_ils -Sum).Sum
    if ($sum -le [double]$port.cash_ils) { Ok "total $([math]::Round($sum,2)) within available cash" } else { Bad "overspends" }
    $after = [double]$card.meta.cash_after_ils; $floor = [double]$card.meta.floor_pct * [double]$port.nav_ils
    if ($after -ge $floor - 1) { Ok "keeps $([math]::Round($after,2)) buffer (floor $([math]::Round($floor,2)))" } else { Bad "spends below the floor" } }
  $cashCards = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' -or $_.apply.kind -eq 'rebalance_to_objective' -or $_.title -like '*idle cash*' })
  if ($cashCards.Count -le 1) { Ok "one card per pot of cash" } else { Bad "$($cashCards.Count) cards spend the same cash: $(($cashCards | ForEach-Object { $_.title }) -join ' | ')" } }

# ---------- PHASE 4: notifications ----------
Sec "9. Notifications (phase 4)"
$st = Api GET '/api/v1/push/status'
if ($null -eq $st) { Skip "push/status unreachable (NOT a pass)" } else {
  foreach ($b in $st.blockers) { Write-Host "   -> $b" -ForegroundColor Yellow }
  if ($st.subscriptions -gt 0) { Ok "$($st.subscriptions) live subscription(s)" } else { Bad "ZERO subscriptions - enable notifications in the app on your Pixel" }
  if ($st.push_library_ok) { Ok "push library OK" } else { Bad "push library: $($st.push_library_error)" }
  if ($st.scheduler.scheduler_running) { Ok "scheduler running" } else { Bad "scheduler not running" }
  $jids = @($st.scheduler.jobs | ForEach-Object { $_.id })
  if ($jids -contains 'push_evaluate' -and $jids -contains 'push_digest') { Ok "push jobs registered" } else { Bad "push jobs missing" } }

# ---------- PHASE 10: AI ----------
Sec "10. AI features (phase 10)"
$diag = Api GET '/api/v1/adversary/diagnostics'
if ($null -eq $diag) { Skip "diagnostics unreachable (NOT a pass)" } elseif ($diag.ok) { Ok "Gemini reachable (model $($diag.adversary_llm_model))" } else { Bad "Gemini failing: $($diag.error)" }
$ai = Api GET '/api/v1/ai/portfolio-summary'
if ($null -eq $ai) { Skip "ai summary unreachable" } elseif ($ai.llm) { Ok "portfolio summary generated" } else { Bad "summary failed - reason shown to user: '$($ai.error)'" }
$ask = try { Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/ask" -Headers ($H + @{'Content-Type'='application/json'}) -Body (@{question="What is my biggest holding?"} | ConvertTo-Json) -TimeoutSec 60 -DisableKeepAlive } catch { $null }
if ($null -eq $ask) { Skip "ask unreachable" } elseif ($ask.llm) { Ok "Ask InvestWise answered" } else { Bad "ask failed - reason shown to user: '$($ask.error)'" }

Write-Host "`n===== $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($retried -gt 0) { Write-Host "$retried call(s) only succeeded on retry - the first attempt died on a stale connection, not an API fault.`n" -ForegroundColor DarkYellow } else { Write-Host "" }
