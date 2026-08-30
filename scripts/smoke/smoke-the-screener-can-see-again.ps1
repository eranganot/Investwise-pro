# SMOKE - PR 25, against the LIVE deploy. READ-ONLY throughout.
#
#   .\scripts\smoke\smoke-the-screener-can-see-again.ps1
#   .\scripts\smoke\smoke-the-screener-can-see-again.ps1 -Sha 1234abc
#
# ONE QUESTION: does the app recommend stocks again?
#
# It has not. Measured on production 2026-08-30, from the counter PR 24 added:
#
#     equity candidates the provider says have no fundamentals: AAPL, ABBV,
#     ADBE, AMD, AMZN, AVGO, BA, BAC, ... XOM        (all 58, every scan)
#
# Buy ideas were three commodities and three bonds. No equities at all.
#
# CAUSE, PROVEN: Yahoo gated /v10/finance/quoteSummary behind a session cookie
# plus a crumb, and returned 401 "Invalid Crumb". Prices come from /v8/chart,
# which is NOT gated -- so NAV, the refresh job and every price on screen kept
# working while the screener produced nothing. get_fundamentals swallowed the
# 401 into None, and until PR 24 nothing counted it.
#
# Section 2 is the one that matters. If it is not green the fix did not land,
# whatever the sha says.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"
$AgentKey = Get-AgentKey -BaseUrl $BaseUrl
if (-not $AgentKey) { exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $AgentKey }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

# The seven Israeli names already in the universe. All are US listings -- there
# is still not one Tel Aviv listing in the candidate list, which is the OTHER
# half of item 3 and is not fixed here.
$IsraeliNames = @('TEVA', 'NICE', 'CHKP', 'ESLT', 'WIX', 'MNDY', 'GLOB')

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# A 401 IS AN ANSWER. Reporting it as "did not answer" renders a rejected key
# and an outage identically, and the reader cannot tell them apart.
function Api($method, $path, $timeout = 240) {
    # NOT $args - a PowerShell AUTOMATIC variable, same family as the $h that
    # once silently clobbered the auth headers.
    $reqArgs = @{
        Method     = $method
        Uri        = "$BaseUrl$path"
        Headers    = $ApiHeaders
        TimeoutSec = $timeout
    }
    try {
        return Invoke-RestMethod @reqArgs
    } catch {
        $code = 0
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        }
        if ($code -eq 401 -or $code -eq 403) {
            Write-Host "        HTTP $code - the server REJECTED the agent key. Not an outage." -ForegroundColor Yellow
            Write-Host "        Clear a stale one and let agent-key.ps1 resolve it:" -ForegroundColor DarkGray
            Write-Host "          Remove-Item Env:\IW_AGENT_KEY" -ForegroundColor DarkGray
        } elseif ($code -gt 0) {
            Write-Host "        HTTP $code from $path - $($_.Exception.GetType().Name)" -ForegroundColor DarkGray
        } else {
            Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        }
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - can the screener see stocks again?" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
Sec "0  which build is serving"
$health = Api GET '/health' 60
if ($null -eq $health) {
    Bad "/health unreachable - nothing below can be trusted"
} else {
    Note "serving commit $($health.commit), version $($health.version)"
    if ($Sha) {
        if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
        else { Bad "serving '$($health.commit)', expected '$Sha' - everything below describes the OLD build" }
    } else {
        Skip "no -Sha given, so the sha was not verified"
    }
}

# ---------------------------------------------------------------------------
Sec "2  THE ONE THAT MATTERS - are there equity buy ideas at all?"
# Before this PR: 0. Three commodities and three bonds, nothing else.
$recs = Api GET '/api/v1/recommendations' 300
if ($null -eq $recs) {
    Bad "/recommendations returned nothing - see the line above for why"
} else {
    $ideas = @($recs.buy_ideas)
    $eq = @($ideas | Where-Object { $_.sector -ne 'Commodities' -and $_.sector -ne 'Fixed Income' })
    Note "buy ideas total : $($ideas.Count)"
    Note "equities        : $($eq.Count)"
    Note "commodities     : $(@($ideas | Where-Object { $_.sector -eq 'Commodities' }).Count)"
    Note "fixed income    : $(@($ideas | Where-Object { $_.sector -eq 'Fixed Income' }).Count)"

    if ($eq.Count -gt 0) {
        Ok "$($eq.Count) EQUITY idea(s) - the screener can see stocks again"
        foreach ($e in $eq) {
            Note "  $($e.ticker)  score $($e.score)  $($e.sector)"
        }
    } else {
        Bad "still ZERO equity ideas. The crumb fix did not take on this build."
        Note "Check the log for which branch it took now:"
        Note "  railway logs --lines 400 | Select-String 'equity candidates'"
        Note "  ...UNREACHABLE  -> the handshake is failing (auth/transport)"
        Note "  ...no fundamentals -> Yahoo answers but has nothing (different bug)"
    }
}

# ---------------------------------------------------------------------------
Sec "3  coverage - how much of the universe got scored"
if ($null -ne $recs -and $null -ne $recs.buy_ideas_ranked_for) {
    $cov = $recs.buy_ideas_ranked_for.coverage
    if ($null -eq $cov -or $null -eq $cov.requested) {
        Skip "no coverage on the pin - it rides on equity ideas, and there may be none"
        Note "PR 24 attached the coverage pin only to equity ideas, so it"
        Note "disappears in exactly the case worth diagnosing. Known defect."
    } else {
        $req = [int]$cov.requested; $got = [int]$cov.ranked
        $unreach = @($cov.unreachable); $nodata = @($cov.no_data)
        Note "offered $req   ranked $got   unreachable $($unreach.Count)   no data $($nodata.Count)"
        if ($got -eq $req) {
            Ok "every candidate was scored"
        } elseif ($got -gt 0) {
            Ok "$got of $req scored - partial, but no longer zero"
            if ($unreach.Count -gt 0) { Note "unreachable: $($unreach -join ', ')" }
            if ($nodata.Count -gt 0)  { Note "no data    : $($nodata -join ', ')" }
        } else {
            Bad "zero candidates scored"
        }

        $seenIsraeli = @($IsraeliNames | Where-Object { $unreach -notcontains $_ -and $nodata -notcontains $_ })
        if ($seenIsraeli.Count -gt 0) {
            Note ""
            Note "ITEM 3, HALF: $($seenIsraeli.Count) of the 7 Israeli names were scored"
            Note "($($seenIsraeli -join ', ')). They can now compete for a card."
            Note "The other half is unfixed: every one is a US listing, and there"
            Note "is still not one Tel Aviv listing in the universe."
        }
    }
} else {
    Skip "no pin on the payload, so coverage could not be read"
}

# ---------------------------------------------------------------------------
Sec "4  the screener endpoint agrees"
$scr = Api GET '/api/v1/screener?n_equities=5' 240
if ($null -eq $scr) {
    Bad "/screener returned nothing - see the line above for why"
} else {
    $tk = @($scr.equities | ForEach-Object { $_.ticker })
    Note "equities: $($tk.Count)   $($tk -join ', ')"
    if ($tk.Count -gt 0) { Ok "the screener route returns equities" }
    else { Bad "the screener route still returns no equities" }
}

# ---------------------------------------------------------------------------
Sec "5  prices are untouched"
# get_fundamentals and get_quote use DIFFERENT Yahoo endpoints. Prices were
# never broken and must not become so: /v8/chart is not gated and this PR does
# not go near it.
$portfolio = Api GET '/api/v1/portfolio' 120
if ($null -eq $portfolio) {
    Bad "/portfolio returned nothing - see the line above for why"
} else {
    $nav = [math]::Round([double]$portfolio.nav_ils, 2)
    Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
    $stale = @($portfolio.positions | Where-Object { $_.stale -eq $true })
    if ($stale.Count -eq 0) { Ok "no stale prices" }
    else { Bad "$($stale.Count) stale price(s): $((@($stale | ForEach-Object { $_.ticker })) -join ', ')" }
    Note "COMPARE THIS NAV TO BEFORE THE DEPLOY. This PR changes which names get"
    Note "RANKED. It buys nothing and it must not move NAV by an agora."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-- result" -ForegroundColor Cyan
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NEVER A PASS. $($script:Skipped) check(s) did not run." -ForegroundColor Yellow
}
Write-Host ""
if ($script:Fail -gt 0) {
    Write-Host "  RED." -ForegroundColor Red
    exit 1
}
Write-Host "  Green. The screener can see stocks again." -ForegroundColor Green
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The crumb handshake is undocumented and Yahoo can change it." -ForegroundColor DarkYellow
Write-Host "    What is different now: a failure RAISES and gets counted, so the" -ForegroundColor DarkYellow
Write-Host "    log names it on the first scan instead of going quiet for months." -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe. The other" -ForegroundColor DarkYellow
Write-Host "    half of item 3, and the next PR." -ForegroundColor DarkYellow
Write-Host "  * P/B for a .TA listing is off by FX x 100 (TEVA 5.48 vs TEVA.TA" -ForegroundColor DarkYellow
Write-Host "    1657.9). P/E is fine - it is a same-currency ratio. Must be" -ForegroundColor DarkYellow
Write-Host "    handled BEFORE Tel Aviv listings are added." -ForegroundColor DarkYellow
Write-Host "  * The coverage pin rides on equity ideas, so it vanishes when" -ForegroundColor DarkYellow
Write-Host "    there are none. Known, and worth fixing next." -ForegroundColor DarkYellow
Write-Host "  * Item 2 has no factor tilt: nothing derives one." -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is a live literal in 33 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
