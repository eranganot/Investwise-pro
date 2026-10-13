# SMOKE - PR 118b: thin intake routes (L7). Pure moves: nothing may change.
#
#   .\scripts\smoke\smoke-pr118b-thin-intake-routes.ps1 -Sha <short sha of the merge>
#
#   1  /portfolio, a position row and /portfolio/standing-orders return exactly
#      the keys recorded from production before the move (build 8e19cdd)
#   2  NAV = sum of rows; Today builds
#
# READ-ONLY. ASCII ONLY. (POST /portfolio/add is not exercised: it writes.)

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 118b"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

function Keys($o) { (@($o.PSObject.Properties.Name) | Sort-Object) -join "," }
Sec "1  the moved endpoints keep their shape"
$p = Api GET "/api/v1/portfolio"
$want = "base_currency,basis_baseline_as_of,basis_baseline_ils,cash_ils,contributed_ils,cost_basis_ils,count,fx_stale_positions,fx_unresolved_positions,fx_unresolved_value_ils,gain_ils,gain_pct,gain_vs_basis_ils,gain_vs_basis_pct,gain_vs_contributions_ils,gain_vs_contributions_pct,invested_ils,invested_source,nav_ils,nav_native,positions,stale_positions,stale_value_ils,unexplained_basis_ils,unpriced_cost_ils,unpriced_positions"
if ((Keys $p) -eq $want) { Ok "/portfolio same keys" } else { Bad ("/portfolio keys changed: {0}" -f (Keys $p)) }
$wantPos = "asset_class,cost_basis,currency,current_price,current_price_ils,depth,fx_status,gain_ils,id,invested_ils,last_buy,market,price_as_of,price_currency_conflict,price_currency_quoted,price_currency_repaired,price_freshness,price_stale,price_stale_days,price_unverified,quantity,ticker,value_ils,volatility_pct"
if ((Keys $p.positions[0]) -eq $wantPos) { Ok "position rows same keys" } else { Bad ("position keys changed: {0}" -f (Keys $p.positions[0])) }
$s = Api GET "/api/v1/portfolio/standing-orders"
if ((Keys $s) -eq "monthly_total_ils,note,orders,today") { Ok "/portfolio/standing-orders same keys" } else { Bad ("standing-orders keys changed: {0}" -f (Keys $s)) }

Sec "2  the numbers agree"
$sum = (@($p.positions) | Measure-Object -Property value_ils -Sum).Sum
if ([math]::Abs($sum - [double]$p.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($p.positions).Count, [double]$p.nav_ils) } else { Bad "nav_ils != sum of rows" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
