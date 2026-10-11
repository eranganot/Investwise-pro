# SMOKE - PR 112: legacy removal, stage 1 (D-j / D76).
#
#   .\scripts\smoke\smoke-pr112-legacy-stage1.ps1 -Sha <short sha of the merge>
#
#   1  every deleted route is gone (404/405 with the agent key)
#   2  the live neighbours in the same areas still answer 200
#   3  the scheduler runs 10 jobs; market_refresh is no longer one of them
#
# READ-ONLY (the deleted POST routes are probed and must not exist). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 112"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: stage 1 has no migration)"

function Code($method, $path) {
    try { $r = Invoke-WebRequest -Method $method -Uri "$BaseUrl$path" -Headers $script:ApiHeaders -UseBasicParsing -TimeoutSec 120 -ContentType 'application/json' -Body $(if ($method -ne 'GET') { '{}' } else { $null }); return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}

Sec "1  deleted"
$gone = @(@('POST', '/api/v1/lag/scan'), @('GET', '/api/v1/whs'), @('GET', '/api/v1/simulation'),
          @('POST', '/api/v1/safety/check'), @('GET', '/api/v1/tax/preview'), @('GET', '/api/v1/risk/preview'),
          @('POST', '/api/v1/allocation/analyze'), @('POST', '/api/v1/backtest'), @('GET', '/api/v1/fees'),
          @('GET', '/api/v1/screener'), @('GET', '/api/v1/jobs'), @('POST', '/api/v1/jobs/monte-carlo'),
          @('POST', '/api/v1/broker/connect'), @('POST', '/api/v1/broker/sync'), @('GET', '/api/v1/entities'),
          @('GET', '/api/v1/market/quote?ticker=VTI'), @('GET', '/api/v1/fx/rate'))
foreach ($g in $gone) {
    $c = Code $g[0] $g[1]
    if ($c -in 404, 405) { Ok ("{0} {1} -> {2}" -f $g[0], $g[1], $c) } else { Bad ("{0} {1} -> {2} (still served)" -f $g[0], $g[1], $c) }
}

Sec "2  live neighbours"
foreach ($p in @('/api/v1/health-check', '/api/v1/risk/alerts', '/api/v1/research/events', '/api/v1/data-status',
                 '/api/v1/war-room', '/api/v1/auth/status', '/api/v1/scenario/supported')) {
    $c = Code GET $p
    if ($c -eq 200) { Ok "$p -> 200" } else { Bad "$p -> $c" }
}
$w = $null
try { $w = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/whatif" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 120 } catch {}
if ($w -and $w.grounded -eq $true) { Ok "POST /whatif -> grounded" } else { Bad "POST /whatif failed" }

Sec "3  scheduler"
$ps = Api GET "/api/v1/push/status"
$ids = @($ps.scheduler.jobs | ForEach-Object { $_.id })
if ($ids.Count -eq 10 -and $ids -notcontains 'market_refresh') { Ok "10 jobs, no market_refresh" } else { Bad ("jobs: {0}" -f ($ids -join ', ')) }
Stop-Smoke
