# SMOKE - W0-5: remove what nothing uses (L2).
#
#   .\scripts\smoke\smoke-w0-5-remove-unused.ps1 -Sha <short sha of the merge>
#
#   1  the build serves; /app and Today work without the removed files
#   2  CORS: a preflight from http://localhost:5173 is allowed only while
#      Railway still sets FRONTEND_ORIGIN to it (reported, not failed: removing
#      the variable is an owner step)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "W0-5"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the app"
try {
    $a = Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 30
    if ($a.StatusCode -eq 200) { Ok "/app 200" } else { Bad ("/app {0}" -f $a.StatusCode) }
} catch { Bad ("/app failed: {0}" -f $_.Exception.Message) }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }

Sec "2  CORS"
try {
    $o = Invoke-WebRequest -Uri "$BaseUrl/api/v1/plan" -Method Options -UseBasicParsing -TimeoutSec 30 `
        -Headers @{ "Origin" = "http://localhost:5173"; "Access-Control-Request-Method" = "GET" }
    $allow = $o.Headers["Access-Control-Allow-Origin"]
} catch { $allow = $null }
if ($allow) { Skip "localhost:5173 still allowed: FRONTEND_ORIGIN is still set in Railway (owner step)" } else { Ok "no CORS for a ghost origin" }
Stop-Smoke
