# SMOKE - PR 160 (C34a): migration 0030 alone (card_sightings).
#
#   .\scripts\smoke\smoke-pr160-card-sightings-migration.ps1 -Sha <sha serving now>
#
#   1  the pre-deploy migration ran: /health says schema 0030_card_sightings
#   2  nothing reads or writes the table yet: /plan and /recommendations answer as before
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 160"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(the new migration)"

Sec "2  nothing reads it yet"
$p = Api GET "/api/v1/plan"
$r = Api GET "/api/v1/recommendations"
if ($p -and $p.objective) { Ok ("/plan answers (objective {0})" -f $p.objective) } else { Bad "/plan did not answer" }
if ($r) { Ok ("/recommendations: {0} card(s)" -f @($r.recommendations).Count) } else { Bad "/recommendations did not answer" }
Stop-Smoke
