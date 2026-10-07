# SMOKE - PR 27, against the LIVE deploy. READ-ONLY throughout.
#
#   .\scripts\smoke\smoke-the-numbers-it-was-given.ps1
#   .\scripts\smoke\smoke-the-numbers-it-was-given.ps1 -Sha 1234abc
#
# ONE QUESTION: are the numbers the screener ranks on numbers it can defend?
#
# PR 25 got the equity screener answering again. Measured on production
# 2026-08-31, the answers it gave back contained three defects, all of the same
# family PR 25 fixed one layer down - a missing answer and a bad answer
# rendering identically:
#
#   BRK-B  #7   pb 0.00096701446   value 98.2  "Trades near book value (P/B 0.0)"
#               class-B price over class-A book value per share, off by ~1650x
#   MCD    #11  pb + roe + debt_to_equity all null, quality 78.3 - the HIGHEST
#               of the names checked, computed from profit margin alone
#   JPM #2 / BAC #5   debt_to_equity null, quality scored without leverage
#
# Section 2 is the one that matters. If BRK-B still carries a P/B under 0.01 in
# a scored pillar, the fix did not land, whatever the sha says.
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

# Below this a price-to-book is not a bargain, it is a unit or share-class
# error. Mirrors _PB_FLOOR in app/engines/screener_engine.py.
$PbFloor = 0.05
$PbCeiling = 500.0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# A 401 IS AN ANSWER. Reporting it as "did not answer" renders a rejected key
# and an outage identically, and the reader cannot tell them apart.
function Api($method, $path, $timeout = 240) {
    # NOT $args - a PowerShell AUTOMATIC variable.
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
Write-Host "InvestWise Pro - can it defend the numbers it ranked on?" -ForegroundColor White
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
Sec "1  PR 25 still holds - there are equity ideas at all"
# This PR changes which names win. It must not take the list back to zero,
# which is the regression that would matter most.
$recs = Api GET '/api/v1/recommendations' 300
$ideas = @()
if ($null -eq $recs) {
    Bad "/recommendations returned nothing - see the line above for why"
} else {
    $ideas = @($recs.buy_ideas)
    $eq = @($ideas | Where-Object { $_.sector -ne 'Commodities' -and $_.sector -ne 'Fixed Income' })
    Note "buy ideas $($ideas.Count)   equities $($eq.Count)"
    if ($eq.Count -gt 0) {
        Ok "$($eq.Count) equity idea(s) - PR 25 still holds"
        foreach ($e in $eq) {
            $gaps = @($e.data_gaps)
            $g = if ($gaps.Count -gt 0) { "  gaps: $($gaps -join '; ')" } else { "" }
            Note "  $($e.ticker)  score $($e.score)$g"
        }
    } else {
        Bad "ZERO equity ideas - this PR took the list back to where PR 25 found it"
    }
}

# ---------------------------------------------------------------------------
Sec "2  THE ONE THAT MATTERS - no scored pillar rests on an impossible P/B"
if ($ideas.Count -eq 0) {
    Skip "no ideas to inspect - section 1 already failed"
} else {
    $bad = @()
    $rejected = @()
    foreach ($e in $ideas) {
        $pb = $e.metrics.pb
        if ($null -eq $pb) { continue }
        if ([double]$pb -lt $PbFloor -or [double]$pb -gt $PbCeiling) {
            # The raw number is SUPPOSED to still be in metrics - it is the
            # audit trail. What must not happen is it being scored, or quoted.
            $gaps = @($e.data_gaps)
            $named = @($gaps | Where-Object { $_ -like 'pb:*' })
            $quoted = @($e.reasons | Where-Object { $_ -like '*book value*' })
            if ($named.Count -eq 0) { $bad += "$($e.ticker) pb=$pb was NOT recorded as a gap" }
            elseif ($quoted.Count -gt 0) { $bad += "$($e.ticker) pb=$pb rejected but the card still says: $($quoted[0])" }
            else { $rejected += "$($e.ticker) pb=$pb -> $($named[0])" }
        }
    }
    if ($bad.Count -gt 0) {
        foreach ($b in $bad) { Bad $b }
    } else {
        Ok "every implausible P/B is rejected, recorded and not quoted"
        if ($rejected.Count -gt 0) {
            foreach ($r in $rejected) { Note "  $r" }
        } else {
            Note "none of the ideas on this scan carried an implausible P/B."
            Note "BRK-B is the known case (pb 0.00096701446); it is only visible"
            Note "here when it makes the top 5, so this is not proof on its own."
            Note "Section 4 checks the full ranked list, which is where to look."
        }
    }
}

# ---------------------------------------------------------------------------
Sec "3  coverage rides on the payload even when nothing ranked"
if ($null -eq $recs) {
    Skip "no payload"
} else {
    $pin = $recs.buy_ideas_ranked_for
    if ($null -eq $pin) {
        Bad "no ranked_for pin at all"
    } elseif ($null -eq $pin.coverage -or $null -eq $pin.coverage.requested) {
        Bad "the pin is there but carries NO coverage - the PR 25 defect is back"
    } else {
        $cov = $pin.coverage
        Note "offered $($cov.requested)   ranked $($cov.ranked)   unreachable $(@($cov.unreachable).Count)   no data $(@($cov.no_data).Count)"
        Ok "coverage is on the payload"
        if ([int]$cov.ranked -eq 0) {
            Note "ranked ZERO - and the coverage still says so, which is the whole"
            Note "point of the change. Read no_data / unreachable above."
        }
    }
}

# ---------------------------------------------------------------------------
Sec "4  the full ranked list - gaps are disclosed, not inferred"
$scr = Api GET '/api/v1/screener?n_equities=25' 240
if ($null -eq $scr) {
    Bad "/screener returned nothing - see the line above for why"
} else {
    $eqs = @($scr.equities)
    Note "ranked names returned: $($eqs.Count)"
    if ($eqs.Count -eq 0) {
        Bad "the screener route returns no equities"
    } else {
        Ok "the screener route returns equities"
        $withGaps = @($eqs | Where-Object { @($_.data_gaps).Count -gt 0 })
        Note "names carrying at least one data gap: $($withGaps.Count) of $($eqs.Count)"
        Note "(measured before this PR: 8 of 25, and none of them said so)"
        foreach ($e in $withGaps) {
            Note "  $($e.ticker)  score $($e.score)  $(@($e.data_gaps) -join '; ')"
        }
        # Every gapped name must also carry the flag - a payload where the two
        # disagree is worse than one that says nothing, because it looks checked.
        $mismatched = @($withGaps | Where-Object { @($_.flags) -notcontains 'partial-data' })
        if ($mismatched.Count -eq 0) {
            Ok "every gapped name is flagged partial-data"
        } else {
            Bad "$($mismatched.Count) name(s) have gaps but no partial-data flag: $((@($mismatched | ForEach-Object { $_.ticker })) -join ', ')"
        }
        # And no card may quote a P/B the engine refused to score.
        $liars = @($eqs | Where-Object {
            $_.metrics.pb -ne $null -and
            ([double]$_.metrics.pb -lt $PbFloor -or [double]$_.metrics.pb -gt $PbCeiling) -and
            @($_.reasons | Where-Object { $_ -like '*book value*' }).Count -gt 0 })
        if ($liars.Count -eq 0) {
            Ok "no card quotes a book value the engine rejected"
        } else {
            Bad "$($liars.Count) card(s) still quote a rejected P/B: $((@($liars | ForEach-Object { $_.ticker })) -join ', ')"
        }
    }
}

# ---------------------------------------------------------------------------
Sec "5  prices are untouched"
# This PR changes RANKING only. It buys nothing and it must not move NAV.
$portfolio = Api GET '/api/v1/portfolio' 120
if ($null -eq $portfolio) {
    Bad "/portfolio returned nothing - see the line above for why"
} else {
    $nav = [math]::Round([double]$portfolio.nav_ils, 2)
    Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
    Note "MEASURED BEFORE THIS DEPLOY, 2026-08-31: 21333.59 ILS across 4."
    Note "A different number here is a finding, not a rounding difference."
    $stale = @($portfolio.positions | Where-Object { $_.stale -eq $true })
    if ($stale.Count -eq 0) { Ok "no stale prices" }
    else { Bad "$($stale.Count) stale price(s): $((@($stale | ForEach-Object { $_.ticker })) -join ', ')" }
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
Write-Host "  Green." -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor White
Write-Host "  * Today / buy ideas. The equity list SHOULD have moved - names" -ForegroundColor DarkGray
Write-Host "    whose scores rested on an unmeasured factor now rank lower." -ForegroundColor DarkGray
Write-Host "    BRK-B in particular loses a value pillar it had not earned." -ForegroundColor DarkGray
Write-Host "  * No card should say 'Trades near book value (P/B 0.0)'." -ForegroundColor DarkGray
Write-Host "  * NAV must be unchanged. This PR ranks; it does not trade." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe. The P/B" -ForegroundColor DarkYellow
Write-Host "    trap that blocked that work is disarmed here; the listings are" -ForegroundColor DarkYellow
Write-Host "    still the other half of item 3 and are not added." -ForegroundColor DarkYellow
Write-Host "  * Item 2 has no factor tilt: nothing derives one." -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS rebalance leak is UNEXPLAINED." -ForegroundColor DarkYellow
Write-Host "  * AGENT_API_KEY and AUTH_PASSWORD are STILL the published values" -ForegroundColor DarkYellow
Write-Host "    on Railway. Verified 2026-08-31: the key committed in 33 scripts" -ForegroundColor DarkYellow
Write-Host "    still returns 200 on /api/v1/portfolio. See ROTATION.md." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
