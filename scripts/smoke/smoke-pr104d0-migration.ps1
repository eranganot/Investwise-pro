# SMOKE - PR 104d.0: migration 0028 alone (plans.thresholds).
#
#   .\scripts\smoke\smoke-pr104d0-migration.ps1 -Sha <sha serving now>
#
#   1  the pre-deploy migration ran: /health says schema 0028_plan_thresholds
#   2  nothing reads the column yet: /plan and /recommendations answer as before
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104d.0"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(the new migration)"

Sec "2  nothing reads it yet"
$p = Api GET "/api/v1/plan"
$r = Api GET "/api/v1/recommendations"
if ($p -and $p.target_amount) { Ok ("/plan: goal {0} by {1}" -f $p.target_amount, $p.target_date) } else { Bad "/plan did not answer" }
if ($r) { Ok ("/recommendations: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "/recommendations did not answer" }
Stop-Smoke
