# SMOKE - PR 119: the deploy is infrastructure as code (.railway/railway.ts).
#
#   .\scripts\smoke\smoke-pr119-railway-iac.ps1 -Sha <short sha of the merge>
#
#   1  the pushed SHA is serving and the schema is 0029 (the pre-deploy
#      `alembic upgrade head` still runs without railway.json)
#   2  /health/ready answers (the healthcheck path the deploy gates on)
#   3  nothing else changed: /portfolio, Today, the health check, push status
#
#   Manual, alongside: `railway deployment list --json` -> the newest
#   deployment's meta.serviceManifest reads builder DOCKERFILE, healthcheckPath
#   /health/ready, restartPolicyMaxRetries 3; `railway config plan` reports
#   "already up to date".
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 119"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(migrations ran pre-deploy from railway.ts)"

Sec "2  the healthcheck path"
try {
    $hr = Invoke-WebRequest -Uri "$BaseUrl/health/ready" -UseBasicParsing -TimeoutSec 30
    if ($hr.StatusCode -eq 200) { Ok "/health/ready 200" } else { Bad ("/health/ready {0}" -f $hr.StatusCode) }
} catch { Bad ("/health/ready failed: {0}" -f $_.Exception.Message) }

Sec "3  nothing else changed"
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
$ps = Api GET "/api/v1/push/status"
if ($ps) { Ok "push status answers (VAPID variables preserved)" } else { Bad "push status failed" }
Stop-Smoke
