# SMOKE - D-1 (D131): a read-only key for the scheduled production smoke.
#
#   $env:IW_SMOKE_KEY = '<the smoke key>'      # never the agent key
#   .\scripts\smoke\smoke-d1-read-only-smoke-key.ps1 -Sha <short sha of the merge>
#
#   1  the build is serving
#   2  with IW_SMOKE_KEY set: it is not the agent key, and it reads Today, the optimiser and the rules
#      (the refusal of a write is proven by tests/test_d1_the_smoke_key_only_reads.py; a smoke never writes)
#   3  a wrong key reads nothing
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "D-1"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "2  the smoke key reads"
$sk = $env:IW_SMOKE_KEY
if (-not $sk) { Skip "IW_SMOKE_KEY not set" }
elseif ($env:IW_AGENT_KEY -and $sk -eq $env:IW_AGENT_KEY) { Bad "the smoke key is the agent key" }
else {
    foreach ($p in @("/api/v1/recommendations", "/api/v1/plan/optimizer", "/api/v1/rules")) {
        try { $null = Invoke-RestMethod -Method GET -Uri "$BaseUrl$p" -Headers @{ 'x-agent-key' = $sk } -TimeoutSec 60; Ok ("GET {0}" -f $p) }
        catch { Bad ("GET {0}: {1}" -f $p, $_.Exception.Response.StatusCode) }
    }
}

Sec "3  a wrong key reads nothing"
try { $null = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/v1/portfolio" -Headers @{ 'x-agent-key' = 'not-a-key' } -TimeoutSec 30; Bad "a wrong key read the portfolio" }
catch { $code = [int]$_.Exception.Response.StatusCode; if ($code -eq 401 -or $code -eq 403) { Ok ("refused {0}" -f $code) } else { Bad ("unexpected {0}" -f $code) } }
Stop-Smoke
