# SMOKE - PR 37: is the core measured like the strategy it is?
#
#   .\scripts\smoke\smoke-core-measured.ps1
#   .\scripts\smoke\smoke-core-measured.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is measured, bought or resized.
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
Section "2. What core does the book name?"
$coreId = ""
try {
    $sl = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/plan/sleeves") -Headers $ApiHeaders -TimeoutSec 60
    if ($sl.core -and $sl.core.strategy_id) {
        $coreId = [string]$sl.core.strategy_id
        Write-Host ("        core " + $coreId + " (" + $sl.core.name + ") at " + $sl.core_pct + "% of book") -ForegroundColor DarkGray
        Check "the book names a core" $true ""
    } else {
        Skipped "the book names a core" "no core row - the rest of this script has nothing to check"
    }
} catch {
    Check "/api/v1/plan/sleeves answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. THE QUESTION: has that core been measured at all?"
# Before PR 37 no static family had ever been backtested, so this row did not
# exist for any core on any book.
if (-not $coreId) {
    Skipped "the core has a stored measurement" "no core named above"
} else {
    try {
        $btUri = $BaseUrl + "/api/v1/strategies/backtests?ids=" + $coreId
        $bt = Invoke-RestMethod -Uri $btUri -Headers $ApiHeaders -TimeoutSec 90
        $row = $null
        foreach ($s in $bt.strategies) { if ($s.id -eq $coreId) { $row = $s } }
        if ($null -eq $row) {
            Check "the core has a stored measurement" $false "no row for $coreId - the nightly job has not measured it yet"
        } else {
            $m = $row.backtest
            $have = ($null -ne $m)
            Check "the core has a stored measurement" $have "before PR 37 no core was ever backtested"
            if ($have) {
                Write-Host ("        cagr " + $m.cagr_pct + "%  vol " + $m.volatility_pct +
                            "%  maxdd " + $m.max_drawdown_pct + "%  obs " + $m.observations) -ForegroundColor DarkGray
                $d1 = "a fixed basket is not a rule; both halves come from one window"
                $bothHalves = ($null -ne $m.cagr_pct) -and ($null -ne $m.volatility_pct)
                Check "it carries a return and a volatility" $bothHalves $d1
            }
        }
    } catch {
        Check "/api/v1/strategies/backtests answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
    }
}

# --------------------------------------------------------------------------
Section "4. Is the book actually HOLDING that core?"
# The gate PR 37 adds. A measurement of a basket may only price holdings that
# ARE that basket - otherwise it describes a book that does not exist, which is
# the defect PR 6 removed at the other end.
try {
    $pl = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/plan") -Headers $ApiHeaders -TimeoutSec 90
    $asm = $pl.assumptions
    if ($null -eq $asm) {
        Skipped "the core's status is reported" "no assumptions block on this payload"
    } else {
        Write-Host ("        measured " + $asm.measured_pct_of_book + "% of book") -ForegroundColor DarkGray
        Write-Host ("        " + $asm.note) -ForegroundColor White
        Write-Host ""
        $coreComp = $null
        foreach ($c in $asm.components) { if ($c.kind -eq "core") { $coreComp = $c } }
        $notHeld = $null
        foreach ($a in $asm.abstained) { if ($a.reason -eq "CORE_NOT_HELD") { $notHeld = $a } }

        if ($null -ne $coreComp) {
            Write-Host "        THE CORE IS MEASURED AND HELD." -ForegroundColor Green
            Write-Host ("        " + $coreComp.name + " at " + $coreComp.weight_pct +
                        "% - return " + $coreComp.return_pct + "% (" + $coreComp.return_source +
                        "), vol " + $coreComp.volatility_pct + "% (" + $coreComp.volatility_source + ")") -ForegroundColor Green
            $d2 = "a core in the components list that is still priced from the table is the bug, not the fix"
            Check "the core is priced from its own measurement" ($coreComp.return_source -eq "measured") $d2
        } elseif ($null -ne $notHeld) {
            Write-Host "        THE CORE IS MEASURED AND THE BOOK IS NOT HOLDING IT." -ForegroundColor Yellow
            Write-Host ("        " + $notHeld.detail) -ForegroundColor Yellow
            $ev = $notHeld.evidence
            if ($ev) {
                Write-Host ("        missing: " + ($ev.missing -join ", ")) -ForegroundColor Yellow
                Write-Host ("        extra:   " + ($ev.extra -join ", ")) -ForegroundColor Yellow
            }
            $named = (($ev.missing.Count -gt 0) -or ($ev.extra.Count -gt 0) -or ($null -ne $ev.worst_drift_pct))
            $d3 = "'does not match' is not actionable; the ticker has to be named"
            Check "the mismatch names which holding is wrong" $named $d3
            Check "the note says the core IS measured" ($asm.note -like "*is measured*") ""
        } else {
            Skipped "the core is measured or explained" "neither a core component nor a CORE_NOT_HELD abstention"
        }

        # The invariant Eran's correction bought, checked against production.
        $honest = -not ($asm.note -like "*no strategy*")
        Check "the note never says the core has no strategy" $honest "it has one; the app just has to read it"
    }
} catch {
    Check "/api/v1/plan answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "5. What to do with a CORE_NOT_HELD"
Write-Host "        It is not an error. It says your core strategy is measured" -ForegroundColor DarkGray
Write-Host "        and your holdings are not that basket, so the measurement is" -ForegroundColor DarkGray
Write-Host "        deliberately NOT used to price them. Bring the core back to" -ForegroundColor DarkGray
Write-Host "        its basket and the same figure starts pricing your book." -ForegroundColor DarkGray

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
