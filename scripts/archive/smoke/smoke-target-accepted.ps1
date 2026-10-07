# SMOKE - PR 36: does the target card remember what you accepted?
#
#   .\scripts\smoke\smoke-target-accepted.ps1
#   .\scripts\smoke\smoke-target-accepted.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is accepted, undone or solved.
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
Section "2. THE QUESTION: what does the server say you accepted?"
$acc = $null
try {
    $a = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/plan/target/accepted") -Headers $ApiHeaders -TimeoutSec 60
    Check "the endpoint answers" ($a.ok -eq $true) ""
    $hasKey = ($a.PSObject.Properties.Name -contains "accepted")
    Check "it distinguishes 'none' from 'broken'" $hasKey "a missing key and a null are different answers"
    if ($null -ne $a.accepted) {
        $acc = $a.accepted
        Write-Host ("        beat " + $acc.excess_pct + "% without exceeding " + $acc.ceiling_pct + "%") -ForegroundColor Green
        Write-Host ("        accepted " + $acc.accepted_at + "  outcome " + $acc.outcome) -ForegroundColor DarkGray
        Check "both figures are present" (($null -ne $acc.excess_pct) -and ($null -ne $acc.ceiling_pct)) ""
    } else {
        Skipped "an accepted target is remembered" "nothing accepted, or the last apply was undone - both are correct nulls"
    }
} catch {
    Check "/api/v1/plan/target/accepted answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. Does it agree with the application log?"
# The log is the source. If the two disagree, the read path is inventing.
try {
    $h = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/plan/applications?limit=5") -Headers $ApiHeaders -TimeoutSec 60
    $newest = $null
    if ($h.entries -and $h.entries.Count -gt 0) { $newest = $h.entries[0] }
    if ($null -eq $newest) {
        Skipped "the accepted target matches the newest application" "no applications recorded"
    } elseif ($newest.action -eq "undo") {
        Write-Host ("        newest entry is an UNDO at " + $newest.at) -ForegroundColor Yellow
        $d2 = "the last thing you did was undo, so nothing should be seeded"
        Check "an undone target is not reported as accepted" ($null -eq $acc) $d2
    } else {
        Write-Host ("        newest entry is an APPLY at " + $newest.at) -ForegroundColor DarkGray
        if ($null -eq $acc) {
            $ctx = $newest.context
            $partial = ($null -eq $ctx.excess_pct) -or ($null -eq $ctx.ceiling_pct)
            $d4 = "the newest apply has both figures and the endpoint still returned null"
            Check "a null is explained by a partial context" $partial $d4
        } else {
            $same = ([double]$acc.excess_pct -eq [double]$newest.context.excess_pct) -and
                    ([double]$acc.ceiling_pct -eq [double]$newest.context.ceiling_pct)
            $d3 = "endpoint " + $acc.excess_pct + "/" + $acc.ceiling_pct +
                  "  log " + $newest.context.excess_pct + "/" + $newest.context.ceiling_pct
            Check "the accepted target matches the newest application" $same $d3
        }
    }
} catch {
    Check "/api/v1/plan/applications answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "4. Does the assumptions line name what is NOT measured?"
try {
    $pl = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/plan") -Headers $ApiHeaders -TimeoutSec 60
    $head = ""
    if ($pl.assumptions -and $pl.assumptions.headline) { $head = [string]$pl.assumptions.headline }
    if ([string]::IsNullOrWhiteSpace($head)) {
        Skipped "the headline names the unmeasured share" "no assumptions headline on this payload"
    } else {
        Write-Host ("        " + $head) -ForegroundColor DarkGray
        $named = ($head -like "*core*") -and ($head -like "*cash*")
        $d5 = "a bare percentage reads as a coverage hole"
        Check "the headline names the unmeasured share" $named $d5
        # A CORE IS A STRATEGY. bal_6040 is a basket plus a rebalance rule, and
        # core_search already measures fourteen of them. The headline may report
        # that this app does not measure a core yet; it may never say the core
        # has nothing to measure.
        $honest = -not ($head -like "*no strategy*")
        $d6 = "the core has a strategy - the app just does not read it yet"
        Check "the headline does not deny the core its strategy" $honest $d6
    }
} catch {
    Check "/api/v1/plan answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
