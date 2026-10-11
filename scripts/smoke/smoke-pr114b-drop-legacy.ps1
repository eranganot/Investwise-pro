# SMOKE - PR 114b: migration 0029 drops the legacy tables (D76).
#
#   .\scripts\smoke\smoke-pr114b-drop-legacy.ps1 -Sha <short sha of the merge>
#
#   1  the schema revision is 0029_drop_legacy_tables (the pre-deploy
#      `alembic upgrade head` ran)
#   2  the live read path is intact: /portfolio (NAV = sum of rows), Today,
#      the health check, the rules list
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 114b"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(the legacy tables and positions.bucket_id are gone)"

Sec "2  the read path"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
$h = Api GET "/api/v1/health-check"
if ($h -and $null -ne $h.wealth_health_score) { Ok ("health check: {0}/100" -f $h.wealth_health_score) } else { Bad "health check failed" }
$ru = Api GET "/api/v1/rules"
if ($ru -and @($ru.rules).Count -gt 0) { Ok ("{0} rules listed" -f @($ru.rules).Count) } else { Bad "rules list failed" }
Stop-Smoke
