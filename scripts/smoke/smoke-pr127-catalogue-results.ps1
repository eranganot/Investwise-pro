# SMOKE - PR 127: the eight rotation candidates measured; Trend-Filtered 2x Nasdaq joins the catalogue.
#
#   .\scripts\smoke\smoke-pr127-catalogue-results.ps1 -Sha <short sha of the merge>
#
#   1  btm_qld_trend is in the catalogue, offered, designed Medium-high, holds QLD over QQQ
#   2  once measured (03:30 or a manual refresh): it beats its base and shows its measured tier (SKIP until then)
#   3  nothing else changed: the 8 earlier entries, Dual Momentum and Factor Stack still offered (their replacements failed)
#
# Public endpoints only. READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 127"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$bt = Api GET "/api/v1/strategies/backtests"
if (-not $bt) { Bad "/strategies/backtests did not answer"; Stop-Smoke }
$q = @($bt.strategies | Where-Object { $_.id -eq "btm_qld_trend" })[0]

Sec "1  the new entry"
if (-not $q) { Bad "btm_qld_trend is not in the catalogue"; Stop-Smoke }
if (-not $q.retired) { Ok "btm_qld_trend offered" } else { Bad "btm_qld_trend marked retired" }
if ($q.risk -eq "Medium-high") { Ok "designed tier Medium-high" } else { Bad "designed tier $($q.risk)" }
$basket = (@($q.basket) | ForEach-Object { $_[0] }) -join ","
if ($basket -eq "QLD" -and (@($q.base_when_flat) -join ",") -eq "QQQ") { Ok "QLD, otherwise QQQ" } else { Bad "basket $basket, base $(@($q.base_when_flat) -join ',')" }

Sec "2  measured"
$m = $null
if ($q.backtest) { $m = $q.backtest.metrics }
if ($null -eq $m -or $null -eq $m.cagr_pct) { Skip "not measured yet: the 03:30 refresh measures new entries" }
else {
    if ([double]$m.excess_over_base_cagr_pct -gt 0) { Ok ("{0}%/yr, {1} points over QQQ" -f $m.cagr_pct, $m.excess_over_base_cagr_pct) } else { Bad ("does not beat QQQ in production: {0}" -f $m.excess_over_base_cagr_pct) }
    Note ("worst drop -{0}%, measured tier {1}" -f $m.max_drawdown_pct, $q.measured_risk)
    $lh = $q.backtest.robustness.long_history
    if ($lh -and $lh.ok) { Note ("2000-2016: {0}%/yr vs {1}, worst drop -{2}%" -f $lh.cagr_pct, $lh.base_cagr_pct, $lh.max_drawdown_pct) }
}

Sec "3  nothing else changed"
$ids = @($bt.strategies | ForEach-Object { $_.id })
if ($ids.Count -eq 9) { Ok "9 entries" } else { Bad "$($ids.Count) entries, expected 9" }
foreach ($sid in @("btm_dual_momentum", "btm_factor_stack")) {
    $s = @($bt.strategies | Where-Object { $_.id -eq $sid })[0]
    if ($s -and -not $s.retired) { Ok "$sid still offered (its replacement did not pass)" } else { Bad "$sid missing or retired" }
}

Stop-Smoke
