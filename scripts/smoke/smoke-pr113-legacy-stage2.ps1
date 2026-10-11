# SMOKE - PR 113: legacy removal, stage 2 -- the decision-feed cluster (D76).
#
#   .\scripts\smoke\smoke-pr113-legacy-stage2.ps1 -Sha <short sha of the merge>
#
#   1  the decision-feed, learning and /tax/review routes are gone (404/405)
#   2  the live neighbours still answer: /health-check, /risk/alerts,
#      /scenario/supported (same router file as two deleted functions), the war
#      room and What-if (both run on the state machine that stays), Today
#
# READ-ONLY (the deleted POST routes are probed and must not exist). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 113"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: stage 2 has no migration)"

function Code($method, $path) {
    try { $r = Invoke-WebRequest -Method $method -Uri "$BaseUrl$path" -Headers $script:ApiHeaders -UseBasicParsing -TimeoutSec 120 -ContentType 'application/json' -Body $(if ($method -ne 'GET') { '{}' } else { $null }); return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}

Sec "1  deleted"
foreach ($g in @(@('GET', '/api/v1/decision-feed/demo'), @('POST', '/api/v1/decision-feed/generate'),
                 @('GET', '/api/v1/decision-feed/latest'), @('GET', '/api/v1/decision-feed/weekly'),
                 @('POST', '/api/v1/actions'), @('GET', '/api/v1/learning/profile'), @('GET', '/api/v1/tax/review'))) {
    $c = Code $g[0] $g[1]
    if ($c -in 404, 405) { Ok ("{0} {1} -> {2}" -f $g[0], $g[1], $c) } else { Bad ("{0} {1} -> {2} (still served)" -f $g[0], $g[1], $c) }
}

Sec "2  live neighbours"
foreach ($p in @('/api/v1/health-check', '/api/v1/risk/alerts', '/api/v1/scenario/supported', '/api/v1/war-room')) {
    $c = Code GET $p
    if ($c -eq 200) { Ok "$p -> 200" } else { Bad "$p -> $c" }
}
$w = $null
try { $w = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/whatif" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 120 } catch {}
if ($w -and $w.grounded -eq $true) { Ok "POST /whatif -> grounded" } else { Bad "POST /whatif failed" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
