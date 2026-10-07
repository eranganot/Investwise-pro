# SMOKE - PR 32: can a holding still be written down to zero?
#
#   .\scripts\smoke\smoke-unpriced.ps1
#   .\scripts\smoke\smoke-unpriced.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is bought, sold or repriced.
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
Section "2. Is anything valued at zero right now?"
# The whole point of this PR is that this question now has an ANSWER on the
# payload instead of a silent 0 in the total.
try {
    $pf = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/portfolio") -Headers $ApiHeaders -TimeoutSec 60
    $hasKey = ($pf.PSObject.Properties.Name -contains "unpriced_positions")
    $d2 = "before PR 32 there was no such field, and a zeroed holding was indistinguishable from a worthless one"
    Check "the payload discloses unpriced holdings at all" $hasKey $d2

    $zeroRows = @($pf.positions | Where-Object {
        ($_.ticker -ne "CASH") -and (-not ($_.current_price -gt 0)) })
    foreach ($p in $zeroRows) {
        Write-Host ("        " + $p.ticker.PadRight(6) + " price " + $p.current_price +
                    "  paid " + [math]::Round([double]$p.invested_ils,2) + " ILS") -ForegroundColor Yellow
    }
    if ($hasKey) {
        $named = @($pf.unpriced_positions | ForEach-Object { $_.ticker })
        $missing = @($zeroRows | Where-Object { $named -notcontains $_.ticker })
        $detail = "nothing is currently unpriced"
        if ($zeroRows.Count -gt 0) {
            $detail = "" + $zeroRows.Count + " row(s) at zero, " + $named.Count + " disclosed"
        }
        Check "every zero-priced holding is named in unpriced_positions" ($missing.Count -eq 0) $detail
        if ($zeroRows.Count -gt 0) {
            Write-Host ("        NAV is missing about " +
                        [math]::Round([double]$pf.unpriced_cost_ils,2) +
                        " ILS at cost because of them") -ForegroundColor Yellow
            Write-Host "        THIS IS A REAL FINDING. Check the holding before deleting it:" -ForegroundColor Yellow
            Write-Host "        a row the app could not price is not a row worth nothing." -ForegroundColor Yellow
        }
    } else {
        Skipped "every zero-priced holding is named" "the field is absent, so this build predates PR 32"
    }
} catch {
    Check "/api/v1/portfolio answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. What the last refresh refused"
# /api/v1/jobs is where the refresh reports itself. A run that refused a zero
# says so; a run with nothing to refuse says zero, which is the normal answer.
try {
    $jobs = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/jobs") -Headers $ApiHeaders -TimeoutSec 60
    $txt = ($jobs | ConvertTo-Json -Depth 6)
    if ($txt -match "unpriced") {
        Write-Host "        the job report carries the unpriced counter" -ForegroundColor DarkGray
        Check "the refresh reports what it refused" $true ""
    } else {
        Skipped "the refresh reports what it refused" "no refresh has run on this build yet, or /jobs does not carry the counter"
    }
} catch {
    Skipped "the refresh reports what it refused" ("/api/v1/jobs did not answer: " + $_.Exception.GetType().Name)
}

# --------------------------------------------------------------------------
Section "4. NAV is a total, not a survivor count"
try {
    $pf2 = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/portfolio") -Headers $ApiHeaders -TimeoutSec 60
    Write-Host ("        NAV " + [math]::Round([double]$pf2.nav_ils,2) + " ILS across " + $pf2.count + " positions") -ForegroundColor DarkGray
    Write-Host "        A holding the app cannot price is OUT of this total and now" -ForegroundColor DarkGray
    Write-Host "        says so. NAV is deliberately not padded with a guessed price." -ForegroundColor DarkGray
    Check "NAV is a number" ([double]$pf2.nav_ils -gt 0) ""
} catch {
    Check "/api/v1/portfolio answered again" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
