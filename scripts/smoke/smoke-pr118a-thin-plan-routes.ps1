# SMOKE - PR 118a: thin plan routes (L7). Pure moves: nothing may change.
#
#   .\scripts\smoke\smoke-pr118a-thin-plan-routes.ps1 -Sha <short sha of the merge>
#
#   1  every moved endpoint returns exactly the keys it returned before the
#      move (recorded from production, build 64128a8, 2026-10-12)
#   2  the numbers still agree: /plan current_value = /portfolio nav_ils;
#      plan health and Today build
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 118a"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$expect = [ordered]@{
  "/api/v1/plan" = "assumption_basis,assumptions,caps,cash_redeploy_at_ils,cash_target_ils,cash_target_pct,configured,currency,current_value,expectation,frontier,goal,goal_progress,horizon_years,kelly_fraction,measured_pct_of_book,objective,portfolio_expected_roi_pct,portfolio_expected_roi_pct_measured,portfolio_expected_roi_pct_static,portfolio_volatility_pct,portfolio_volatility_pct_static,preferred_depth,return_confidence,risk_tolerance,roi_annual_target_pct,roi_on_track,target_amount,target_date,target_roi_pct,target_roi_period,target_yield_pct,target_yield_period,thresholds,thresholds_defaults,thresholds_effective_card_floor_ils,thresholds_overrides,thresholds_spec"
  "/api/v1/plan/sleeves" = "allocated_held_ils,allocated_held_pct,allocated_pct,cash_reserve_pct,commodities_pct,core,core_held_pct,core_is_implicit,core_pct,nav_ils,sleeves"
  "/api/v1/plan/core/options" = "core_is_unset,current,objective,options,risk_tolerance,store_error"
  "/api/v1/plan/projection" = "assumption_basis,assumptions,deadline,excess_comparison,expected_return_pct_static,goal_headline,measured_pct_of_book,on_track,probability_meets_target,probability_of_gain_real,probability_of_loss_real,projected_high,projected_low,projected_median,return_convention,return_convention_note,runs,starting_value,target_amount,volatility_pct_static,with_contributions,years"
  "/api/v1/mix" = "cash_ils,core_pct_of_book,current_allocation,drift_percentage,nav,note,objective,rebalance_actions,rebalance_required,target_allocation,target_from_core,target_from_sleeves,target_source"
  "/api/v1/war-room" = "agents,count,generated_at,grounded,sessions,signal_basis,solver_constraint"
}
Sec "1  the moved endpoints keep their shape"
$got = @{}
foreach ($p in $expect.Keys) {
    $r = Api GET $p
    $got[$p] = $r
    $k = (@($r.PSObject.Properties.Name) | Sort-Object) -join ","
    if ($k -eq $expect[$p]) { Ok "$p same keys" } else { Bad "$p keys changed: $k" }
}

Sec "2  the numbers agree"
$pf = Api GET "/api/v1/portfolio"
$cv = [double]$got["/api/v1/plan"].current_value
if ($pf -and [math]::Abs($cv - [double]$pf.nav_ils) -le 1) { Ok ("/plan current_value {0:N2} = /portfolio nav" -f $cv) } else { Bad ("/plan current_value {0} vs nav {1}" -f $cv, $pf.nav_ils) }
$h = Api GET "/api/v1/health-check"
if ($h -and $null -ne $h.wealth_health_score -and $null -ne $h.on_track) { Ok ("health check {0}/100, on track {1}" -f $h.wealth_health_score, $h.on_track) } else { Bad "health check (it calls projection + mix) failed" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
