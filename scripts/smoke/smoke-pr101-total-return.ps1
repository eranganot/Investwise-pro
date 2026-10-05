# SMOKE - PR 101: backtest history includes dividends (total return).
#
#   .\scripts\smoke\smoke-pr101-total-return.ps1 -Sha abc1234
#
#   1  the holdings review re-measures on demand (POST /holding-review/refresh - a
#      MEASUREMENT, nothing in the book changes): every row is engine a6 and says
#      which return basis it used
#   2  BND: holding it earns money on total return (+1.25%/yr expected, was
#      -1.76% on plain closes) and neither sell signal beats holding
#   3  no "Sell ... BND" card on Today
#   4  a strategy backtest re-measured now is stored as <provider>:total_return
#   5  nothing was executed
#
# DOES NOT ACCEPT ANY CARD. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 101"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

$pfBefore = Api GET "/api/v1/portfolio"

Sec "1  the holdings review, re-measured"
$t0 = Get-Date
$m = Api POST "/api/v1/holding-review/refresh" 900
Note ("measured in {0}s: {1}" -f [int]((Get-Date) - $t0).TotalSeconds, ((@($m.measured) | ForEach-Object { "$($_.ticker)=$($_.ok)" }) -join ', '))
$recs = Api GET "/api/v1/recommendations"
$rows = @($recs.holding_review | Where-Object { $_.measurement })
if ($rows.Count -eq 0) { Bad "no measured holdings in the review" }
foreach ($r in $rows) {
    $x = $r.measurement
    Note ("{0,-6} engine {1}  basis {2}  hold {3}%/yr  20/50 {4}% edge {5}  50/200 {6}% edge {7}" -f $r.ticker, $x.engine_version, $x.return_basis, $x.long.hold_cagr_pct, $x.short.rule_cagr_pct, $x.short.edge, $x.long.rule_cagr_pct, $x.long.edge)
    if ($x.engine_version -ne 'a6') { Bad "$($r.ticker): measured by engine $($x.engine_version), expected a6" }
    elseif (@('total_return', 'price_only') -notcontains $x.return_basis) { Bad "$($r.ticker): no return basis on the measurement" }
    elseif ($x.return_basis -eq 'price_only') { Skip "$($r.ticker): measured on plain closes (no adjusted series) - labelled price_only" }
    else { Ok "$($r.ticker): engine a6, total return" }
}

Sec "2  BND on total return"
$bnd = $rows | Where-Object { $_.ticker -eq 'BND' } | Select-Object -First 1
if ($null -eq $bnd) { Skip "BND is not held" }
else {
    $x = $bnd.measurement
    if ([double]$x.long.hold_cagr_pct -gt 0) { Ok ("holding BND: {0}%/yr with dividends (was -1.76% on plain closes)" -f $x.long.hold_cagr_pct) }
    else { Bad ("holding BND still measures {0}%/yr - dividends missing" -f $x.long.hold_cagr_pct) }
    if (-not $x.short.edge -and -not $x.long.edge) { Ok ("neither signal beats holding: 20/50 {0}%, 50/200 {1}% vs hold {2}%" -f $x.short.rule_cagr_pct, $x.long.rule_cagr_pct, $x.long.hold_cagr_pct) }
    else { Bad "a sell signal on BND still beats holding" }
}

Sec "3  no sell-BND card"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$sell = @($all | Where-Object { $_.dimension -eq 'holding_review' -and "$($_.title)" -match '^Sell .* BND' })
if ($sell.Count -eq 0) { Ok "no 'Sell ... BND' card" } else { Bad ("still showing: {0}" -f (($sell | ForEach-Object { $_.title }) -join '; ')) }

Sec "4  a strategy backtest on total return"
$bt = Api GET "/api/v1/strategies/backtests"
if ($bt.engine_version -ne 'a6') { Bad "live engine is $($bt.engine_version), expected a6" }
$sid = @($bt.strategies)[0].id
$t0 = Get-Date
$null = Api POST "/api/v1/strategies/backtests/refresh?only=$sid" 900
$bt2 = Api GET "/api/v1/strategies/backtests"
$row = (@($bt2.strategies) | Where-Object { $_.id -eq $sid } | Select-Object -First 1).backtest
Note ("{0} re-measured in {1}s: source {2}, engine {3}, cagr {4}%" -f $sid, [int]((Get-Date) - $t0).TotalSeconds, $row.data_source, $row.engine_version, $row.metrics.cagr_pct)
if ($row.engine_version -eq 'a6' -and "$($row.data_source)" -match ':total_return$' -and $row.metrics.return_basis -eq 'total_return') { Ok "$sid stored as total return" }
elseif ("$($row.data_source)" -match ':price_only$') { Skip ("{0} fell back to plain closes for {1}" -f $sid, (@($row.metrics.price_only_tickers) -join ', ')) }
else { Bad ("{0}: engine {1}, source {2}" -f $sid, $row.engine_version, $row.data_source) }
Note ("still stale until tonight's 03:30 run: {0}" -f (@($bt2.stale) -join ', '))

Sec "5  nothing was executed"
$pfAfter = Api GET "/api/v1/portfolio"
$q1 = (@($pfBefore.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
$q2 = (@($pfAfter.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
if ($q1 -eq $q2) { Ok "holdings unchanged" } else { Bad "holdings moved" }

Stop-Smoke
