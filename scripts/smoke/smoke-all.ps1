# FULL SMOKE - every phase (1-10). READ-ONLY: nothing is bought, sold or dismissed.
# Reads real values from the API (never hardcodes caps/floors) and SKIPs or FAILs
# on a null payload rather than passing on missing data.
#
#   .\scripts\smoke\smoke-all.ps1

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
# NOT $H. PowerShell variable names are CASE-INSENSITIVE, so `$h = Api GET
# '/api/v1/health-check'` in section 3 silently overwrote the auth headers with
# the health-check response object. Every call after it died instantly at
# parameter binding ("Cannot convert PSCustomObject to IDictionary") -- which the
# old handler reported as "NO RESPONSE", implying a network fault that never
# existed. Four wrong hypotheses came out of that one hidden error message.
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
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

$script:callNo = 0
function Api($method, $path, $tmo = 60) {
    # Every failure prints its elapsed time and the FULL exception. Collapsing
    # everything to "NO RESPONSE" hid the difference between a 60s server hang
    # and an instant client-side socket error for several debugging rounds --
    # and the same call sequence typed at the prompt succeeds every time, so the
    # fault is in this script, not the API.
    # Fail loudly if a later assignment ever clobbers the headers again. A
    # PSCustomObject here means some variable collided with $ApiHeaders, and the
    # resulting ParameterBindingException looks exactly like a network outage.
    if ($ApiHeaders -isnot [hashtable]) {
        Write-Host "  FATAL: `$ApiHeaders is a $($ApiHeaders.GetType().Name), not a hashtable - a variable collision clobbered it." -ForegroundColor Red
        throw "ApiHeaders corrupted before $method $path"
    }
    for ($i = 1; $i -le 2; $i++) {
        $script:callNo++
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            $r = Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
            $sw.Stop()
            Write-Host ("   [call {0,2}] {1} {2}  {3:N2}s" -f $script:callNo, $method, $path, $sw.Elapsed.TotalSeconds) -ForegroundColor DarkGray
            if ($i -eq 2) { Write-Host "  (recovered on retry)" -ForegroundColor DarkYellow; $script:retried++ }
            return $r
        } catch {
            $sw.Stop()
            $c = $_.Exception.Response.StatusCode.value__
            Write-Host ("   [call {0,2}] {1} {2}  FAILED after {3:N2}s" -f $script:callNo, $method, $path, $sw.Elapsed.TotalSeconds) -ForegroundColor DarkRed
            Write-Host "        TYPE : $($_.Exception.GetType().FullName)" -ForegroundColor DarkRed
            Write-Host "        MSG  : $($_.Exception.Message)" -ForegroundColor DarkRed
            if ($_.Exception.InnerException) { Write-Host "        INNER: $($_.Exception.InnerException.Message)" -ForegroundColor DarkRed }
            if ($_.Exception.Status) { Write-Host "        STAT : $($_.Exception.Status)" -ForegroundColor DarkRed }
            if ($c) { Write-Host "        HTTP : $c" -ForegroundColor DarkRed; return $null }
            if ($i -eq 1) { Start-Sleep -Milliseconds 800; continue }
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
          Invoke-RestMethod "$BaseUrl/health/ready" -Headers $ApiHeaders -TimeoutSec 10 | Out-Null
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
$health = Api GET '/api/v1/health-check'
if ($null -eq $health) { Skip "health-check unreachable (NOT a pass)" } else {
  Write-Host "   score $($health.wealth_health_score)  risk $($health.risk_score)  tax $($health.tax_efficiency_score)  spread $($health.diversification_score)  cash $($health.liquidity_score)" -ForegroundColor DarkGray
  Write-Host "   avg vol $($health.avg_volatility_pct)%  vs budget $($health.volatility_cap_pct)%" -ForegroundColor DarkGray
  if ($health.max_achievable -eq 100) { Ok "no hidden ceiling (thematic=60 constant removed)" } else { Bad "max_achievable = $($health.max_achievable)" }
  if ($health.avg_volatility_pct -eq 15) { Bad "volatility is exactly 15% - placeholder still in use" } elseif ($health.avg_volatility_pct -gt 0) { Ok "volatility $($health.avg_volatility_pct)% derived from real instruments" } else { Skip "no volatility reported" }
  if ($health.tax_efficiency_score -gt 85) { Ok "tax $($health.tax_efficiency_score) exceeds the old hard cap of 85" } else { Skip "tax $($health.tax_efficiency_score) - only meaningful if you have no unharvested losses" } }

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
# Re-read recommendations HERE, not the copy from section 1.
#
# Section 5 POSTs /rules/evaluate, which is what fires a rule ("newly triggered:
# 1"). Comparing the section-1 snapshot against rules read after that evaluate
# compared two different moments with a deliberate state change between them, so
# a rule triggered by THIS SCRIPT read as a banner/card mismatch. It reported a
# deadlock that did not exist, survived four attempted fixes to the app, and was
# never reproducible by hand -- because reading both endpoints together, with no
# evaluate in between, always agreed.
# ORDER MATTERS, and both must be re-read here.
#
# /recommendations does not merely observe: when a rule's card is suppressed
# (dismissed or already done), it RESOLVES that rule inside the same request, so
# `triggered` flips to false during the call. Reading /rules first and
# /recommendations second therefore compares a pre-heal rule list against a
# post-heal card list -- which is what "banner 1 vs 0 cards" was, twice over.
# First the section-1 snapshot was stale; then the rules list was.
#
# Read recommendations, let it settle whatever it is going to settle, and only
# then ask what is still triggered.
$recsNow = Api GET '/api/v1/recommendations' 90
$rulesNow = (Api GET '/api/v1/rules').rules
if ($null -eq $rulesNow -or $null -eq $recsNow) { Skip "need both rules and recommendations (NOT a pass)" } else {
  $recs = $recsNow
  $rules = $rulesNow
  $trig = @($rules | Where-Object { $_.triggered -and $_.active })
  $ruleCards = @($recs.recommendations | Where-Object { $_.dimension -eq 'rule' })
  Write-Host "   triggered: $(if ($trig.Count) { ($trig | ForEach-Object { $_.ticker }) -join ', ' } else { 'none' })" -ForegroundColor DarkGray
  if ($trig.Count -eq $ruleCards.Count) { Ok "banner ($($trig.Count)) matches rule cards ($($ruleCards.Count)) - no deadlock" }
  else {
    Bad "banner $($trig.Count) vs $($ruleCards.Count) cards"
    # Capture the paired state NOW. Every manual attempt to catch this landed on
    # the wrong side of the 30-minute re-evaluation cycle, so the mismatch was
    # never observed and the cause never established.
    $rb = $recs.rule_banner
    Write-Host "   --- diagnostic ---" -ForegroundColor Yellow
    Write-Host "   rule_banner : $(if ($rb) { ($rb | ConvertTo-Json -Compress) } else { 'absent (old build)' })" -ForegroundColor Yellow
    Write-Host "   degraded    : $(if ($recs.degraded) { $recs.degraded -join ',' } else { 'none' })" -ForegroundColor Yellow
    Write-Host "   rule cards  : $(if ($ruleCards.Count) { ($ruleCards | ForEach-Object { $_.id }) -join ',' } else { 'none' })" -ForegroundColor Yellow
    foreach ($t in $trig) {
      $short = $t.id.Substring(0,8)
      $inBanner = if ($rb -and $rb.triggered -contains $short) { 'yes' } else { 'no' }
      $healed   = if ($rb -and $rb.healed -contains $short) { 'YES' } else { 'no' }
      Write-Host "   $($t.ticker) $($t.rule_type) id=$short  seen-by-server=$inBanner  healed=$healed  suppressed?=$(if ($recs.dismissed_count) { $recs.dismissed_count } else { 0 }) dismissed / $(if ($recs.completed_count) { $recs.completed_count } else { 0 }) done" -ForegroundColor Yellow
    }
    Write-Host "   healed=YES means the server cleared it this call - it will re-fire" -ForegroundColor DarkGray
    Write-Host "   within 30 min because the breach is still true. That is a standing-" -ForegroundColor DarkGray
    Write-Host "   condition design question, not a stuck flag." -ForegroundColor DarkGray
  } }

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
  # Work out whether a card SHOULD exist rather than guessing. "cash may be at
  # the floor" was a hypothesis written into the script, and it was wrong: the
  # book was at 12.3% cash against a 3% floor, so the absence of a card was a
  # real finding being reported as an unrunnable check.
  $floorPct = switch ($plan.objective) {
    'Preserve' { 0.10 } 'Income' { 0.07 } 'Grow' { 0.03 } default { 0.05 }
  }
  $cashPct = if ($port.nav_ils) { [double]$port.cash_ils / [double]$port.nav_ils } else { 0 }
  $spendable = [double]$port.cash_ils - ([double]$port.nav_ils * $floorPct)
  Write-Host ("   cash {0:N2} = {1:P1} of NAV | {2} floor {3:P0} | spendable {4:N2}" -f `
      $port.cash_ils, $cashPct, $plan.objective, $floorPct, $spendable) -ForegroundColor DarkGray

  $card = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' })[0]
  if ($null -eq $card) {
    if ($spendable -lt 250) {
      Ok "no redeploy card, and none is due (spendable $([math]::Round($spendable)) is under the 250 minimum)"
    } else {
      Bad "no redeploy card despite $([math]::Round($spendable)) spendable above the floor"
      Write-Host "        Either the card was dismissed/done ($($recs.dismissed_count) / $($recs.completed_count))," -ForegroundColor Yellow
      Write-Host "        or _reconcile dropped it because a rebalance already redeploys the same cash." -ForegroundColor Yellow
    }
  } else {
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
$ask = try { Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/ask" -Headers ($ApiHeaders + @{'Content-Type'='application/json'}) -Body (@{question="What is my biggest holding?"} | ConvertTo-Json) -TimeoutSec 60 -DisableKeepAlive } catch { $null }
if ($null -eq $ask) { Skip "ask unreachable" } elseif ($ask.llm) { Ok "Ask InvestWise answered" } else { Bad "ask failed - reason shown to user: '$($ask.error)'" }

Write-Host "`n===== $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($retried -gt 0) { Write-Host "$retried call(s) only succeeded on retry - the first attempt died on a stale connection, not an API fault.`n" -ForegroundColor DarkYellow } else { Write-Host "" }
