# SMOKE - PR 33: does the history chart still report 0.00% on a day it cannot explain?
#
#   .\scripts\smoke\smoke-history-measured.ps1
#   .\scripts\smoke\smoke-history-measured.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET.
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
Section "2. THE DEFECT: is any changed day still drawn as a flat zero?"
$h = $null
try {
    $h = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/portfolio/nav-history?range=1Q") -Headers $ApiHeaders -TimeoutSec 90
    if (-not $h.ok) {
        Skipped "the series answered" ("not enough history yet: " + $h.reason)
    } else {
        $hasKeys = ($h.PSObject.Properties.Name -contains "unmeasured_days") -and
                   ($h.PSObject.Properties.Name -contains "resolved_changes")
        Check "the payload carries the new attribution fields" $hasKeys "unmeasured_days + resolved_changes"

        # Every date the book changed must be exactly one of: an estimated flow,
        # a resolved change, or an unmeasured hole. None of them may be a plain
        # 0.00% step, which is what all four were before this PR.
        $changed = @()
        foreach ($u in $h.unexplained_flows) { $changed += $u.as_of }
        foreach ($u in $h.resolved_changes)  { $changed += $u.as_of }
        foreach ($u in $h.unmeasured_days)   { $changed += $u.as_of }
        Write-Host ("        days the book changed: " + $changed.Count) -ForegroundColor DarkGray

        $holes = @($h.unmeasured_days | ForEach-Object { $_.as_of })
        $i = 0
        $badZero = @()
        foreach ($d in $h.dates) {
            $v = $h.pct[$i]
            if (($holes -contains $d) -and ($null -ne $v)) { $badZero += $d }
            $i++
        }
        $d2 = ($badZero -join ", ")
        Check "every unmeasured day is a hole in the line, not a value" ($badZero.Count -eq 0) $d2
    }
} catch {
    Check "/portfolio/nav-history answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. What the headline actually covers"
if ($null -ne $h -and $h.ok) {
    Write-Host ("        total " + $h.total_pct + "% over " + $h.measured_steps +
                " of " + $h.steps + " steps") -ForegroundColor DarkGray
    Write-Host ("        reconstruction attempted=" + $h.reconstruction.attempted +
                " available=" + $h.reconstruction.available) -ForegroundColor DarkGray
    if ($h.measured_steps -lt $h.steps) {
        Write-Host "        The headline is compounded over the measured steps only." -ForegroundColor Yellow
        Write-Host "        The card says so; if it does not, that is the finding." -ForegroundColor Yellow
    }
    Check "measured_steps never exceeds the number of steps" ($h.measured_steps -le $h.steps) ""
    foreach ($u in $h.resolved_changes) {
        Write-Host ("        " + $u.as_of + "  checked: held book " +
                    $u.held_book_return_pct + "% - counted as market, not money") -ForegroundColor DarkGray
    }
    foreach ($u in $h.unmeasured_days) {
        Write-Host ("        " + $u.as_of + "  no measurement (" +
                    $u.positions_before + " -> " + $u.positions_after + " holdings)") -ForegroundColor Yellow
    }
} else {
    Skipped "the headline covers what it says" "no series to read"
}

# --------------------------------------------------------------------------
Section "4. A removal can never be reported as money arriving"
if ($null -ne $h -and $h.ok) {
    $wrong = @($h.unexplained_flows | Where-Object {
        (($_.positions_after -lt $_.positions_before) -and ($_.amount_ils -gt 0)) -or
        (($_.positions_after -gt $_.positions_before) -and ($_.amount_ils -lt 0)) })
    foreach ($u in $wrong) {
        Write-Host ("        " + $u.as_of + "  " + $u.positions_before + " -> " +
                    $u.positions_after + " holdings, " + $u.amount_ils + " ILS") -ForegroundColor Red
    }
    Check "no flow contradicts the direction the book moved" ($wrong.Count -eq 0) ""
} else {
    Skipped "no flow contradicts the book" "no series to read"
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
