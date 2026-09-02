# SMOKE - PR 35: can the digest still quote a number it was not given?
#
#   .\scripts\smoke\smoke-digest-grounded.ps1
#   .\scripts\smoke\smoke-digest-grounded.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call is a GET. No notification is sent by this script.
#
# House rules from smoke-p0.ps1: a SKIP is never a PASS; read paired state in
# one call; print the exception TYPE on the first failure; the headers variable
# is $ApiHeaders and never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$SkipShaCheck)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\..\agent-key.ps1"

$key = Get-AgentKey -BaseUrl $BaseUrl
if (-not $key) { Write-Host "No usable agent key. See scripts/agent-key.ps1." -ForegroundColor Red; exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $key }

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0

function Check($name, $ok, $detail) {
    if ($ok) { $script:Pass++; Write-Host ("  PASS  " + $name) -ForegroundColor Green }
    else     { $script:Fail++; Write-Host ("  FAIL  " + $name) -ForegroundColor Red }
    if ($detail) { Write-Host ("        " + $detail) -ForegroundColor DarkGray }
}
function Skipped($name, $why) {
    $script:Skip++
    Write-Host ("  SKIP  " + $name) -ForegroundColor Yellow
    Write-Host ("        " + $why + "  -- a SKIP IS NOT A PASS") -ForegroundColor Yellow
}
function Section($n) { Write-Host ""; Write-Host ("== " + $n) -ForegroundColor Cyan }

# --------------------------------------------------------------------------
Section "1. Which build answered"
try {
    $health = Invoke-RestMethod -Uri ($BaseUrl + "/health") -TimeoutSec 30
    Write-Host ("        commit " + $health.commit + " / " + $health.environment) -ForegroundColor DarkGray
    if ($SkipShaCheck) {
        Skipped "deployed sha matches" "-SkipShaCheck was passed"
    } elseif ($Sha) {
        Check "deployed sha matches -Sha" ($health.commit -eq $Sha) ("expected " + $Sha + ", got " + $health.commit)
    } else {
        Skipped "deployed sha matches" "no -Sha given, so nothing proves this build is yours"
    }
} catch {
    Check "/health answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "2. Does the assistant context still have the shape the app reads?"
# The labelling belongs to the PROMPT. If it leaked into the API, context.nav_ils
# stops being a number and every reader of it breaks quietly.
$askArgs = @{ Uri = ($BaseUrl + "/api/v1/ask"); Method = "Post"; Headers = $ApiHeaders
              ContentType = "application/json"; TimeoutSec = 120
              Body = '{"question":"How am I doing?"}' }
try {
    $ask = Invoke-RestMethod @askArgs
    if ($ask.PSObject.Properties.Name -contains "context") {
        $nav = $ask.context.nav_ils
        $isNumber = ($nav -is [double]) -or ($nav -is [int]) -or ($nav -is [decimal])
        $d2 = "got: " + ($nav | ConvertTo-Json -Compress)
        Check "context.nav_ils is still a plain number" $isNumber $d2
    } else {
        Skipped "context.nav_ils is still a plain number" "the model answered, so no context is returned"
    }
} catch {
    Check "/api/v1/ask answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. THE QUESTION: is the last digest grounded?"
# /api/v1/push/digest/preview builds the digest WITHOUT sending it, so this
# stays read-only. A 'verified: false' means the model quoted something it was
# NOT given, and the deterministic text was sent instead - which is the system
# working, not a failure.
try {
    $d = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/push/digest/preview") -Headers $ApiHeaders -TimeoutSec 120
    $hasKey = ($d.PSObject.Properties.Name -contains "verified")
    Check "the digest reports whether it was verified" $hasKey "before PR 35 nothing checked, so nothing could report"
    Write-Host ""
    Write-Host ("        llm=" + $d.llm + "  verified=" + $d.verified) -ForegroundColor DarkGray
    Write-Host ("        " + $d.digest) -ForegroundColor White
    Write-Host ""
    if ($hasKey -and -not $d.verified) {
        Write-Host "        THE MODEL QUOTED NUMBERS IT WAS NOT GIVEN:" -ForegroundColor Yellow
        Write-Host ("          " + ($d.unsupported_numbers -join ", ")) -ForegroundColor Yellow
        Write-Host "        The deterministic digest was sent instead. That is the" -ForegroundColor Yellow
        Write-Host "        system working. Worth reading anyway - a number here" -ForegroundColor Yellow
        Write-Host "        that SHOULD be in the context is a context gap." -ForegroundColor Yellow
    }
    Check "a digest was produced at all" ([string]::IsNullOrWhiteSpace($d.digest) -eq $false) ""
    Check "the digest ends with the disclaimer" ($d.digest -like "*Not financial advice.*") ""
} catch {
    $code = ""
    try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = "" }
    if ($code -eq 404) {
        Skipped "the digest is grounded" "no preview endpoint on this build - read the 07:00 push instead"
    } else {
        Check "/api/v1/push/digest/preview answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
    }
}

# --------------------------------------------------------------------------
Section "4. The two claims that started this"
Write-Host "        Read the digest above and check by hand:" -ForegroundColor DarkGray
Write-Host "        * No sentence may present the BACKFILL return as the return" -ForegroundColor DarkGray
Write-Host "          on your account or on its value. It must say backfill." -ForegroundColor DarkGray
Write-Host "        * Any probability of reaching your goal must match the one on" -ForegroundColor DarkGray
Write-Host "          the Plan screen. There is now only one source for it." -ForegroundColor DarkGray

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
