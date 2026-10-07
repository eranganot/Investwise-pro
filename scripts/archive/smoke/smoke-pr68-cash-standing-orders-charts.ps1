# SMOKE - PR 68: cash is not a bank stock, deposits that record themselves,
# a chart per holding, and surplus cash that follows the plan.
#
#   .\scripts\smoke\smoke-pr68-cash-standing-orders-charts.ps1
#   .\scripts\smoke\smoke-pr68-cash-standing-orders-charts.ps1 -Sha abc1234
#
# READ-ONLY. Nothing here creates a standing order or accepts a card: set your
# order up in the app (Holdings -> Cash -> pencil), then re-run to see check 3
# report it.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$pass = 0; $fail = 0; $skip = 0
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
function Api($method, $path, $tmo = 180) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch {
        $code = $null; try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> HTTP $code $($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}
function ApiCode($path) {
    try { Invoke-RestMethod -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec 60 | Out-Null; return 200 }
    catch { try { return $_.Exception.Response.StatusCode.value__ } catch { return 0 } }
}

Write-Host "Smoke: PR 68  ($BaseUrl)" -ForegroundColor White

Sec "0  the build and the schema"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
Note "commit $($h.commit)   schema $($h.schema_revision)"
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha - DEPLOY NOT LANDED" } }
else { Skip "no -Sha given" }
if ($h.schema_revision -eq '0023_standing_orders') { Ok "migration 0023_standing_orders ran" }
else { Bad "schema is $($h.schema_revision), expected 0023_standing_orders" }

$pf = Api GET "/api/v1/portfolio"
$nav = [double]$pf.nav_ils; $cash = [double]$pf.cash_ils
Note ("NAV {0:N2}   cash {1:N2}" -f $nav, $cash)

Sec "1  performance values cash at face value, not as Pathward Financial"
$perf = Api POST "/api/v1/portfolio/performance"
if ($null -eq $perf -or -not $perf.ok) { Bad "performance did not answer" }
else {
    if ($perf.holdings_analyzed -contains 'CASH') { Bad "CASH is still sent for price history" } else { Ok "CASH not in holdings_analyzed" }
    if ([math]::Abs([double]$perf.cash_flat_value - $cash) -lt 1) { Ok ("cash_flat_value {0:N2} = cash {1:N2}" -f $perf.cash_flat_value, $cash) }
    else { Bad ("cash_flat_value {0} vs cash {1}" -f $perf.cash_flat_value, $cash) }
    $ratio = [double]$perf.end_value / $nav
    Note ("end_value {0:N2}  (was 147,014.61 on 2026-09-20 with NAV 22,148)" -f $perf.end_value)
    if ($ratio -gt 0.85 -and $ratio -lt 1.15) { Ok ("end_value within 15% of NAV (ratio {0:N3})" -f $ratio) }
    else { Bad ("end_value/NAV = {0:N3} - the series is not this book" -f $ratio) }
    Note ("1Y return {0}%  max drawdown {1}%  excess vs SPY {2}%  -- these are the REAL numbers now" -f $perf.total_return_pct, $perf.max_drawdown_pct, $perf.excess_return_pct)
}

Sec "2  no card treats cash as a security"
$recs = Api GET "/api/v1/recommendations" 300
$cards = @($recs.recommendations)
$bad = @($cards | Where-Object { $_.title -match '^CASH ' })
if ($bad.Count -eq 0) { Ok "no 'CASH is ...' card among $($cards.Count)" } else { Bad "card(s): $($bad.title -join '; ')" }

Sec "3  standing orders"
$so = Api GET "/api/v1/portfolio/standing-orders"
if ($null -eq $so) { Bad "GET /portfolio/standing-orders failed" }
else {
    Ok ("endpoint answers; {0} order(s), {1:N0}/month; today (Israel) {2}" -f @($so.orders).Count, $so.monthly_total_ils, $so.today)
    foreach ($o in @($so.orders)) { Note ("{0:N0} on day {1}  active={2}  next {3}  runs {4}" -f $o.amount_ils, $o.day_of_month, $o.active, $o.next_run_on, $o.runs) }
    $proj = Api GET "/api/v1/plan/projection"
    if ($so.monthly_total_ils -gt 0) {
        if ($proj.with_contributions.source -eq 'standing_order') { Ok "projection counts the standing order" } else { Bad "projection source is '$($proj.with_contributions.source)'" }
    } else { Skip "no order yet - projection check needs one" }
}

Sec "4  per-holding chart"
foreach ($p in @($pf.positions)) {
    if ($p.ticker -eq 'CASH') { continue }
    $hh = Api GET "/api/v1/portfolio/position-history?ticker=$($p.ticker)&range=1M"
    if ($hh -and @($hh.price.points).Count -ge 2) {
        $v = $hh.value
        $vs = "value line: none"
        if ($v.available) { $vs = ("value line {0} pts from {1}" -f @($v.points).Count, $v.starts_at) }
        Ok ("{0}: {1} price pts, {2}" -f $p.ticker, @($hh.price.points).Count, $vs)
    } else { Bad "$($p.ticker): no chart" }
}
$c = ApiCode "/api/v1/portfolio/position-history?ticker=CASH&range=1M"
if ($c -eq 422) { Ok "CASH chart refused (422) before any market call" } else { Bad "CASH chart returned $c" }
$d1 = Api GET "/api/v1/portfolio/position-history?ticker=SOXL&range=1D"
if ($d1) { Note "1D granularity: $($d1.granularity) ($(@($d1.price.points).Count) pts)" }

Sec "5  the cash cards"
$sl = Api GET "/api/v1/plan/sleeves"
$over = @{}; foreach ($s in @($sl.sleeves)) { foreach ($t in @($s.tickers)) { if ([double]$s.gap_pct -lt 0) { $over[$t] = $s.gap_pct } } }
$rd = @($cards | Where-Object { $_.apply.kind -eq 'redeploy_cash' })
if ($rd.Count -eq 0) { Skip ("no redeploy card: cash is {0:P1} of the book (fires above the 7.5% soft target)" -f ($cash / $nav)) }
else {
    $r = $rd[0]; Note $r.title; Note $r.action
    $hits = @($r.apply.legs | Where-Object { $over.ContainsKey($_.ticker) })
    if ($hits.Count -eq 0) { Ok "no leg into a sleeve already over its target" } else { Bad "leg into over-target sleeve: $($hits.ticker -join ',')" }
    if ($r.why -match 'sale proceeds') { Bad "still claims sale proceeds" } else { Ok "no invented history in the why" }
}
$bh = @($cards | Where-Object { $_.apply.kind -eq 'set_standing_order' })
if ($bh.Count -gt 0) { Ok ("behind-goal card offers a standing order: {0:N0}/month total" -f $bh[0].apply.monthly_total_ils) }
else { Skip "no behind-goal contribution card (hidden, or on track)" }

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }
