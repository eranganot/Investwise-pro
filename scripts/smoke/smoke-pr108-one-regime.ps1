# SMOKE - PR 108: one regime acts; the futures cross-check is measured right.
#
#   .\scripts\smoke\smoke-pr108-one-regime.ps1 -Sha <short sha of the merge>
#
#   1  /markets/futures: each tracked future's change_pct has the sign of
#      (price - previous daily bar), checked against Yahoo's own bars (C11).
#      Before PR 108, ES=F read -0.18% on a +0.56% day.
#   2  the strategies' regime (regime_proxy) is served; futures are labelled
#      cross_check
#   3  Today: a "Markets look risk-off" card or heads-up appears only when the
#      proxy says risk_off, and it names SPY's trend (never "()")
#   4  the page: the Markets card leads with "what your strategies read";
#      sw.js iw-v53
#
# READ-ONLY (Yahoo is read directly for the reference bars). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 108"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 108 has no migration)"

$f = Api GET "/api/v1/markets/futures"

Sec "1  day change vs the previous daily bar"
foreach ($sym in @('ES=F', 'NQ=F', 'CL=F')) {
    $row = @($f.futures) | Where-Object { $_.symbol -eq $sym } | Select-Object -First 1
    if (-not $row -or $null -eq $row.change_pct) { Skip "$sym not served"; continue }
    $y = $null
    try { $y = Invoke-RestMethod -Uri ("https://query1.finance.yahoo.com/v8/finance/chart/{0}?interval=1d&range=5d" -f [uri]::EscapeDataString($sym)) -Headers @{ 'User-Agent' = 'InvestWise/1.0' } -TimeoutSec 30 } catch {}
    if (-not $y) { Skip "$sym : Yahoo unreachable for the reference"; continue }
    $closes = @($y.chart.result[0].indicators.quote[0].close)
    $earlier = @($closes[0..($closes.Count - 2)] | Where-Object { $null -ne $_ })
    $prev = [double]$earlier[-1]; $px = [double]$y.chart.result[0].meta.regularMarketPrice
    $want = [math]::Round(($px - $prev) / $prev * 100, 2)
    if ([math]::Abs([double]$row.change_pct - $want) -le 0.15) { Ok ("{0}: {1:+0.00;-0.00}% (reference {2:+0.00;-0.00}%)" -f $sym, [double]$row.change_pct, $want) }
    else { Bad ("{0}: app {1}% vs previous-bar {2}%" -f $sym, $row.change_pct, $want) }
}

Sec "2  the strategies' regime"
if ($f.regime_proxy.ok) { Ok ("regime_proxy {0} (score {1})" -f $f.regime_proxy.state, $f.regime_proxy.score) } else { Bad "regime_proxy not ok: $($f.regime_proxy.reason)" }
if ($f.market.role -eq 'cross_check') { Ok "futures labelled cross_check" } else { Bad "futures not labelled cross_check" }

Sec "3  Today's risk-off message follows the proxy"
$r = Api GET "/api/v1/recommendations"
$all = @($r.recommendations) + @($r.more_recommendations) + @(@($r.headsups) | Where-Object { $_.card } | ForEach-Object { $_.card })
$ro = @($all | Where-Object { "$($_.title)" -like 'Markets look risk-off*' })
$state = $f.regime_proxy.state
if ($state -ne 'risk_off') {
    if ($ro.Count -eq 0) { Ok "proxy $state, no risk-off message" } else { Bad "proxy $state but Today says: $($ro[0].title)" }
} else {
    if ($ro.Count -ge 1 -and "$($ro[0].why) $($ro[0].action)" -match 'SPY' -and "$($ro[0].action)" -notmatch '\(\)') { Ok "proxy risk_off and Today says why: $($ro[0].why)" }
    else { Bad "proxy risk_off but no (or an unexplained) risk-off message" }
}

Sec "4  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($html -and $html -match "what your strategies read") { Ok "Markets card leads with the strategies' regime" } else { Bad "Markets card unchanged" }
$sw = $null; try { $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($sw -and $sw -match "iw-v53") { Ok "sw.js iw-v53" } else { Bad "sw.js not bumped" }
Stop-Smoke
