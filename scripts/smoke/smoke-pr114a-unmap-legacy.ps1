# SMOKE - PR 114a: the legacy tables are no longer mapped (D76).
#
#   .\scripts\smoke\smoke-pr114a-unmap-legacy.ps1 -Sha <short sha of the merge>
#
#   Code only: the six legacy models and Position.bucket_id leave the ORM; the
#   tables stay until migration 0029 (PR 114b). The risk is the read path, so:
#   1  schema is still 0028 (nothing dropped yet)
#   2  /portfolio reads every position (NAV = sum of rows), Today builds, the
#      health check answers -- positions are selected without bucket_id
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 114a"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: 114a is code only; 0029 is PR 114b)"

Sec "1-2  the read path"
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
Stop-Smoke
