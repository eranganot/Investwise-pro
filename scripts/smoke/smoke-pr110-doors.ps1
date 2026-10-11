# SMOKE - PR 110: authorisation and honesty at the edges.
#
#   .\scripts\smoke\smoke-pr110-doors.ps1 -Sha <short sha of the merge>
#
#   1  anonymous: /providers/health, /adversary/diagnostics, /markets/summary -> 401;
#      /docs and /openapi.json -> 404 (production)
#   2  the owner (agent key = SUPERADMIN, D74): /providers/health and
#      /adversary/diagnostics -> 200; /markets/summary -> 200
#   3  broker mock refused in production (ONLY with -Sha proven: on the old build
#      this POST WRITES mock holdings into the real book)
#   4  non-change: /ask still answers a grounded question and ends with the
#      disclaimer; /whatif on the real book is grounded
#
# READ-ONLY (the broker POST is refused before it writes). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 110"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 110 has no migration)"

function Status($path) {
    try { $r = Invoke-WebRequest -Uri "$BaseUrl$path" -UseBasicParsing -TimeoutSec 60 -MaximumRedirection 0; return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}

Sec "1  anonymous"
foreach ($p in @('/api/v1/providers/health', '/api/v1/adversary/diagnostics', '/api/v1/markets/summary')) {
    $s = Status $p
    if ($s -eq 401) { Ok "$p -> 401" } else { Bad "$p -> $s (want 401)" }
}
foreach ($p in @('/docs', '/openapi.json')) {
    $s = Status $p
    if ($s -eq 404) { Ok "$p -> 404" } else { Bad "$p -> $s (want 404)" }
}

Sec "2  the owner"
foreach ($p in @('/api/v1/providers/health', '/api/v1/adversary/diagnostics', '/api/v1/markets/summary')) {
    $r = Api GET $p
    if ($null -ne $r) { Ok "$p -> 200 with the key" } else { Bad "$p failed with the key" }
}

Sec "3  broker mock refused in production"
if (-not $Sha) { Skip "needs -Sha: on an old build this POST would WRITE mock holdings" }
else {
    $pf0 = Api GET "/api/v1/portfolio"
    $b = $null
    # /connect, not /sync: sync without a connection stops earlier ("No brokerage
    # connection") and would pass without proving the production refusal.
    try { $b = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/broker/connect" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 60 } catch {}
    $pf1 = Api GET "/api/v1/portfolio"
    if ($b -and $b.ok -eq $false -and "$($b.error)" -match 'disabled in production' -and @($pf1.positions).Count -eq @($pf0.positions).Count) { Ok ("refused: {0}" -f $b.error) }
    else { Bad ("broker sync not refused: {0}" -f ($b | ConvertTo-Json -Compress)) }
}

Sec "4  non-change"
$a = $null
try { $a = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/ask" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{"question":"What is my biggest holding?"}' -TimeoutSec 120 } catch {}
if ($a -and "$($a.answer)".Trim().ToLower().EndsWith("not financial advice.")) { Ok ("/ask answered (llm={0}, verified={1})" -f $a.llm, $a.verified) }
elseif ($a -and $a.llm -eq $false -and $a.error) { Skip "/ask: model unavailable ($($a.error))" }
else { Bad ("/ask: {0}" -f ($a | ConvertTo-Json -Compress)) }
$w = $null
try { $w = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/whatif" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 120 } catch {}
if ($w -and $w.grounded -eq $true) { Ok ("/whatif grounded, {0} evaluated" -f $w.risk_profile.evaluated) } else { Bad ("/whatif: grounded={0}" -f $w.grounded) }
Stop-Smoke
