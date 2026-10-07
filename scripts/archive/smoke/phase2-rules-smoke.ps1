# Smoke test - PHASE 2 (triggered rules execute + are recorded).
#
# NON-DESTRUCTIVE by default: it proves the wiring works *and* that nothing was
# auto-sold. Nothing in your book changes unless you pass -Execute.
#
#   .\scripts\smoke\phase2-rules-smoke.ps1 -BaseUrl https://your-app.up.railway.app
#   .\scripts\smoke\phase2-rules-smoke.ps1 -BaseUrl ... -Token "<jwt>"     # if REQUIRE_AUTH=1
#   .\scripts\smoke\phase2-rules-smoke.ps1 -BaseUrl ... -Execute           # ACTUALLY trades

param(
    [Parameter(Mandatory=$true)][string]$BaseUrl,
    [string]$Token = "",
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
$H = @{}
if ($Token) { $H['Authorization'] = "Bearer $Token" }

$pass = 0; $fail = 0
function Ok  ($m) { Write-Host "  PASS  $m" -ForegroundColor Green; $script:pass++ }
function Bad ($m) { Write-Host "  FAIL  $m" -ForegroundColor Red;   $script:fail++ }
function Api ($method, $path) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec 45 }
    catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 401 -or $code -eq 403) {
            Write-Host "`n401/403 - REQUIRE_AUTH is on. Re-run with -Token '<jwt>'." -ForegroundColor Yellow
            exit 1
        }
        throw
    }
}

Write-Host "`n=== PHASE 2 SMOKE: $BaseUrl ===`n" -ForegroundColor Magenta

# --- 1. the new table exists and the audit endpoint is reachable -------------
Write-Host "1. Audit trail endpoint" -ForegroundColor Cyan
$ev = Api GET '/api/v1/rules/events'
if ($null -ne $ev.events) { Ok "GET /rules/events responds ($($ev.events.Count) rows) - rule_events table created" }
else { Bad "no 'events' key - table may not have been created at boot" }

# --- 2. snapshot the book BEFORE evaluating ---------------------------------
Write-Host "`n2. Book snapshot (to prove nothing auto-executes)" -ForegroundColor Cyan
$before = Api GET '/api/v1/portfolio'
$beforeMap = @{}
foreach ($p in $before.positions) { $beforeMap[$p.ticker] = [double]$p.quantity }
Write-Host "   NAV $([math]::Round($before.nav_ils,2)) across $($before.positions.Count) positions" -ForegroundColor DarkGray

# --- 3. force an evaluation --------------------------------------------------
Write-Host "`n3. Forcing rule evaluation" -ForegroundColor Cyan
$evalRes = Api POST '/api/v1/rules/evaluate'
Write-Host "   newly triggered this run: $($evalRes.triggered.Count)" -ForegroundColor DarkGray

# --- 4. firings are recorded, with grounded prices ---------------------------
Write-Host "`n4. Firings recorded" -ForegroundColor Cyan
$ev2 = Api GET '/api/v1/rules/events'
if ($ev2.events.Count -ge 1) {
    Ok "$($ev2.events.Count) event(s) logged"
    $e = $ev2.events[0]
    if ($e.trigger_price) { Ok "newest event carries a real trigger price ($($e.ticker) @ $($e.trigger_price), target $($e.target_price))" }
    else { Bad "event has no trigger_price - not grounded in a real quote" }
    if ($e.triggered_at)  { Ok "timestamped: $($e.triggered_at)" } else { Bad "no triggered_at" }
    Write-Host "   outcome=$($e.outcome)  notified=$($e.notified)" -ForegroundColor DarkGray
} else {
    Write-Host "  SKIP  no events yet - no rule is currently through its level" -ForegroundColor Yellow
}

# --- 5. THE KEY CHECK: nothing moved on its own ------------------------------
Write-Host "`n5. Nothing auto-executed (one-tap, not automatic)" -ForegroundColor Cyan
$after = Api GET '/api/v1/portfolio'
$drift = @()
foreach ($p in $after.positions) {
    $was = $beforeMap[$p.ticker]
    if ($null -ne $was -and [math]::Abs($was - [double]$p.quantity) -gt 1e-6) { $drift += $p.ticker }
}
if ($drift.Count -eq 0) { Ok "no holding changed quantity across an evaluation" }
else { Bad "quantities moved without consent: $($drift -join ', ')" }

# --- 6. the card is now executable rather than advice-only -------------------
Write-Host "`n6. Rule cards are actionable" -ForegroundColor Cyan
$recs = Api GET '/api/v1/recommendations'
$ruleCards = @($recs.recommendations | Where-Object { $_.dimension -eq 'rule' })
if ($ruleCards.Count -eq 0) {
    Write-Host "  SKIP  no rule cards on Today right now" -ForegroundColor Yellow
} else {
    foreach ($c in $ruleCards) {
        $kind = $c.apply.kind
        if ($kind -in @('sell_position','trim')) { Ok "'$($c.title)' -> executable ($kind, $($c.apply.shares) sh)" }
        elseif ($kind -eq 'none') { Write-Host "  INFO  '$($c.title)' stays advisory (price alert / buy-dip) - by design" -ForegroundColor DarkGray }
        else { Bad "'$($c.title)' has unexpected apply.kind '$kind'" }
    }
    $exec = @($ruleCards | Where-Object { $_.apply.kind -in @('sell_position','trim') })
    if ($exec.Count -gt 0) { Ok "$($exec.Count) rule card(s) no longer render as 'Guidance - you act on this yourself'" }
}

# --- 7. optional: actually execute one (DESTRUCTIVE) -------------------------
if ($Execute) {
    Write-Host "`n7. EXECUTING one rule card (this really changes your book)" -ForegroundColor Yellow
    $target = @($ruleCards | Where-Object { $_.apply.kind -in @('sell_position','trim') })[0]
    if (-not $target) { Write-Host "  nothing executable to run" -ForegroundColor Yellow }
    else {
        Write-Host "   $($target.title) -> $($target.apply.kind) $($target.apply.shares) $($target.apply.ticker)"
        if ((Read-Host "   Type EXECUTE to confirm") -ne 'EXECUTE') { Write-Host "   aborted" -ForegroundColor Yellow }
        else {
            $body = @{ id = $target.id } | ConvertTo-Json
            $res = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/recommendations/apply" `
                     -Headers ($H + @{'Content-Type'='application/json'}) -Body $body -TimeoutSec 60
            Write-Host "   result: $($res | ConvertTo-Json -Compress)" -ForegroundColor DarkGray
            if ($res.applied -in @('sell_position','trim')) { Ok "Accept executed (applied=$($res.applied))" } else { Bad "applied=$($res.applied)" }
            if ($res.cash_added_ils -gt 0) { Ok "proceeds credited to cash: $($res.cash_added_ils)" } else { Bad "no cash credited" }

            $post = Api GET '/api/v1/portfolio'
            $stillThere = @($post.positions | Where-Object { $_.ticker -eq $target.apply.ticker })
            if ($target.apply.kind -eq 'sell_position' -and $stillThere.Count -eq 0) { Ok "position removed from the book" }
            elseif ($target.apply.kind -eq 'trim' -and $stillThere.Count -eq 1) { Ok "position trimmed, still held" }

            $ev3 = Api GET '/api/v1/rules/events'
            $done = @($ev3.events | Where-Object { $_.outcome -eq 'executed' })
            if ($done.Count -gt 0) { Ok "audit trail stamped outcome=executed with the action detail" }
            else { Bad "event not stamped - the loop didn't close" }
        }
    }
} else {
    Write-Host "`n7. Execution test skipped (re-run with -Execute to trade for real)" -ForegroundColor DarkGray
}

Write-Host "`n=== $pass passed, $fail failed ===" -ForegroundColor $(if ($fail) {'Red'} else {'Green'})
if ($fail) { exit 1 }
