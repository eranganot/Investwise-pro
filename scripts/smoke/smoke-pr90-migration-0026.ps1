# SMOKE - PR 90: migration 0026, plans.kelly_fraction + plans.return_confidence.
# Add-only, ships ALONE (PR 47): nothing reads the columns until PR 91.
#
#   .\scripts\smoke\smoke-pr90-migration-0026.ps1 -Sha abc1234
#
#   0  the build serves the SHA and the schema is stamped 0026 -- the gate PR 91
#      must see before it ships
#   1  the plan, the Today cards and the mix still answer (every Plan SELECT now
#      carries the two columns; a missing column would 500 here)
#   2  nothing moved
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 90 (migration 0026)"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(the gate for PR 91)"

Sec "1  every Plan read still answers"
$plan = Api GET "/api/v1/plan"
if ($plan -and $plan.objective) { Ok ("/plan: objective {0}, target ROI {1}%" -f $plan.objective, $plan.target_roi_pct) } else { Bad "/plan did not answer" }
$recs = Api GET "/api/v1/recommendations"
if ($recs) { Ok ("/recommendations: {0} cards" -f @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ }).Count) } else { Bad "/recommendations did not answer" }
$mix = Api GET "/api/v1/mix"
if ($mix -and $mix.target_allocation) { Ok ("/mix target ({0}): {1}" -f $mix.target_source, ($mix.target_allocation | ConvertTo-Json -Compress)) } else { Bad "/mix did not answer" }
$fi = Api GET "/api/v1/plan/frontier/inputs" 600
if ($fi -and $fi.ok) { Ok "/plan/frontier/inputs (PR 89) still answers" } else { Bad "/plan/frontier/inputs broke" }

Sec "2  nothing moved"
$sl = Api GET "/api/v1/plan/sleeves"
Note ("sleeves: {0}" -f ((@($sl.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ', '))
if (@($sl.sleeves).Count -gt 0) { Ok "sleeves read back" } else { Skip "no sleeves on this book" }

Stop-Smoke
