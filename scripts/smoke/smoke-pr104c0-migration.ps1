# SMOKE - PR 104c.0: migration 0027 alone (plans.halve_limit_pct, plans.on_track_pct).
#
#   .\scripts\smoke\smoke-pr104c0-migration.ps1 -Sha <sha serving now>
#
#   1  the pre-deploy migration ran: /health says schema 0027_plan_goal_limits
#   2  nothing reads the columns yet: /plan, /plan/frontier and /recommendations
#      answer exactly as before (the goal headline is unchanged)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104c.0"
Assert-Build -Sha $Sha -Schema '0027_plan_goal_limits' -Why "(the new migration)"

Sec "2  nothing reads them yet"
$p = Api GET "/api/v1/plan"
$f = Api GET "/api/v1/plan/frontier"
$r = Api GET "/api/v1/recommendations"
if ($p -and $p.target_amount) { Ok ("/plan: goal {0} by {1}" -f $p.target_amount, $p.target_date) } else { Bad "/plan did not answer" }
if ($f) { Ok ("/plan/frontier: headline {0}%" -f $f.headline.p_goal_pct) } else { Bad "/plan/frontier did not answer" }
if ($r) { Ok ("/recommendations: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "/recommendations did not answer" }
Stop-Smoke
