# SMOKE - PR 31: is the bond counted as a bond?
#
#   .\scripts\smoke\smoke-asset-class.ps1
#   .\scripts\smoke\smoke-asset-class.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is bought, sold or repaired.
#
# House rules from smoke-p0.ps1: a SKIP is never a PASS; read paired state in
# one call so the two halves cannot come from different moments; print the
# exception TYPE on the first failure; the headers variable is $ApiHeaders and
# never $h, because PowerShell variable names are case-insensitive and $h has
# silently clobbered the auth headers before.

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
Section "2. THE DEFECT: is a bond holding filed as a bond?"
# One call. The holdings and the NAV they were measured against must come from
# the same read, or a price refresh between two GETs turns a weight into a lie.
$fi = $null
try {
    $pf = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/portfolio") -Headers $ApiHeaders -TimeoutSec 60
    $nav = [double]$pf.nav_ils
    $bonds = @($pf.positions | Where-Object { $_.asset_class -eq "Fixed Income" })
    $known = @("BND","AGG","TIP","SHY","IEF","TLT","LQD","HYG","BIL","SHV","MUB","GOVT",
               "VGSH","VGIT","VGLT","SCHP","VTIP","VCSH","VCIT","USHY","EMB","VTEB","SGOV","USFR")
    $heldKnown = @($pf.positions | Where-Object { $known -contains $_.ticker })
    $misfiled = @($heldKnown | Where-Object { $_.asset_class -ne "Fixed Income" })

    foreach ($p in $heldKnown) {
        Write-Host ("        " + $p.ticker.PadRight(6) + " " + ([math]::Round($p.value_ils,2)).ToString().PadLeft(10) +
                    " ILS  filed as " + $p.asset_class) -ForegroundColor DarkGray
    }
    if ($heldKnown.Count -eq 0) {
        Skipped "a known bond ETF is filed as Fixed Income" "this book holds none of the catalogue tickers"
    } else {
        $detail = "all " + $heldKnown.Count + " correct"
        if ($misfiled.Count -gt 0) {
            $detail = ($misfiled | ForEach-Object { $_.ticker + "=" + $_.asset_class }) -join ", "
        }
        Check "no known bond ETF is filed as anything else" ($misfiled.Count -eq 0) $detail
    }
    $fiValue = ($bonds | Measure-Object -Property value_ils -Sum).Sum
    if (-not $fiValue) { $fiValue = 0 }
    $fi = @{ value = [double]$fiValue; nav = $nav }
    Write-Host ("        Fixed Income " + [math]::Round($fiValue,2) + " ILS of a " + [math]::Round($nav,2) + " NAV") -ForegroundColor DarkGray
} catch {
    Check "/api/v1/portfolio answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. Does the mix agree with the holdings?"
try {
    $mix = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/mix") -Headers $ApiHeaders -TimeoutSec 60
    $cur = $mix.current_allocation
    $curFI = 0.0
    if ($cur.PSObject.Properties.Name -contains "Fixed Income") { $curFI = [double]$cur."Fixed Income" }
    Write-Host ("        current " + (($cur.PSObject.Properties | ForEach-Object { $_.Name + " " + [math]::Round([double]$_.Value * 100,2) + "%" }) -join " / ")) -ForegroundColor DarkGray

    if ($null -eq $fi) {
        Skipped "the mix agrees with the holdings" "the portfolio read failed, so there is nothing to compare"
    } elseif ($fi.nav -le 0) {
        Skipped "the mix agrees with the holdings" "NAV is zero"
    } else {
        $expected = $fi.value / $fi.nav
        # THE REGRESSION. Before PR 31 this read 0 on a book holding 13.7%.
        $d3 = "mix says " + [math]::Round($curFI * 100,2) + "%, holdings say " + [math]::Round($expected * 100,2) + "%"
        Check "the mix reports the Fixed Income actually held" ([math]::Abs($curFI - $expected) -lt 0.01) $d3
    }

    # And the card must not ask to buy a target it already holds. 2243.96 ILS -
    # exactly 10.8% of NAV, the WHOLE target - is what it asked for while the
    # book already held 2842.15 of it.
    $buys = @($mix.rebalance_actions | Where-Object { $_.asset_class -eq "Fixed Income" -and $_.action_type -eq "BUY" })
    if ($buys.Count -eq 0) {
        Check "no card offers to buy the whole fixed-income target" $true "no Fixed Income BUY leg"
    } elseif ($null -eq $fi -or $fi.nav -le 0) {
        Skipped "no card offers to buy the whole fixed-income target" "no NAV to size it against"
    } else {
        $biggest = ($buys | Measure-Object -Property estimated_trade_value_currency -Maximum).Maximum
        $d4 = "buy leg " + [math]::Round([double]$biggest,2) + " ILS against " + [math]::Round($fi.value,2) + " ILS already held"
        Check "the fixed-income buy is a gap, not the whole target" ([double]$biggest -lt $fi.value) $d4
    }

    foreach ($a in $mix.rebalance_actions) {
        Write-Host ("        " + $a.action_type.PadRight(5) + " " + $a.asset_class.PadRight(14) +
                    [math]::Round([double]$a.estimated_trade_value_currency,2) + " ILS") -ForegroundColor DarkGray
    }
} catch {
    Check "/api/v1/mix answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "4. This PR moves no money"
try {
    $pf2 = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/portfolio") -Headers $ApiHeaders -TimeoutSec 60
    Write-Host ("        NAV " + [math]::Round([double]$pf2.nav_ils,2) + " ILS across " + $pf2.count + " positions") -ForegroundColor DarkGray
    Write-Host "        Compare against the value before the deploy. PR 31 changes" -ForegroundColor DarkGray
    Write-Host "        how a holding is LABELLED. Quantities and prices are untouched," -ForegroundColor DarkGray
    Write-Host "        so a NAV that moved by more than the market did is a finding." -ForegroundColor DarkGray
    Check "NAV is a number" ([double]$pf2.nav_ils -gt 0) ""
} catch {
    Check "/api/v1/portfolio answered again" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
