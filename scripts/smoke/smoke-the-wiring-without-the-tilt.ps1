# SMOKE - PR 24, against the LIVE deploy. READ-ONLY throughout.
#
#   .\scripts\smoke\smoke-the-wiring-without-the-tilt.ps1
#   .\scripts\smoke\smoke-the-wiring-without-the-tilt.ps1 -Sha 1234abc
#
# THIS PR CHANGES NOTHING ABOUT WHICH SECURITIES ARE RECOMMENDED, and section 2
# is here to prove that rather than assert it. The objective now reaches the
# screener, but no factor tilt is defined, so every book ranks exactly as it did
# before. A wiring change that quietly reordered real buy recommendations would
# be worse than the bug it fixes.
#
# THE SECTION TO ACTUALLY READ IS 3.
#
# Building this PR turned up a separate defect: the equity screener fires one
# fundamentals call per candidate in a tight loop, and the provider tier's bucket
# is TokenBucket(20, 20). In the sandbox on the builtin provider that means 20 of
# 58 candidates get ranked and 38 are dropped -- including ALL SEVEN Israeli
# names, which is a second cause for item 3.
#
# Production runs Yahoo, where each call takes real network time and the bucket
# refills as the loop runs, so it may drop nothing at all. That was a guess, and
# this PR replaces guessing with a number: 'coverage' now rides in the
# recommendations payload. Section 3 reads it, and the result decides PR 25.
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

# The seven Israeli names already in the candidate universe. Every one is a US
# LISTING (NYSE/NASDAQ) -- there is still not one Tel Aviv listing in the list,
# which is item 3's other cause and is PR 25.
$IsraeliNames = @('TEVA', 'NICE', 'CHKP', 'ESLT', 'WIX', 'MNDY', 'GLOB')

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# A 401 IS AN ANSWER. Reporting it as "did not answer" renders a rejected key
# and an outage identically, and the reader cannot tell them apart.
function Api($method, $path, $timeout = 180) {
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
Write-Host "InvestWise Pro - the objective reaches the screener, and nothing else moved" -ForegroundColor White
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
Sec "1  the wiring is live - the ranking records your objective"
# ONE read. The ideas and the pin that produced them must come from the same
# payload or the pair says nothing.
$recs = Api GET '/api/v1/recommendations' 240
if ($null -eq $recs) {
    Bad "/recommendations returned nothing - see the line above for why"
} else {
    $pin = $recs.buy_ideas_ranked_for
    if ($null -eq $pin) {
        Bad "no 'buy_ideas_ranked_for' in the payload - PR 24 is not in this build"
    } else {
        Note "objective on the pin : $($pin.objective)"
        Note "weights version      : $($pin.weights_version)"
        Note "source               : $($pin.source)"

        $plan = Api GET '/api/v1/plan' 90
        if ($null -eq $plan) {
            Skip "/plan unreachable, so the pin could not be checked against your real objective"
        } elseif ("$($plan.objective)" -eq "$($pin.objective)") {
            Ok "the ranking was pinned to your real objective '$($plan.objective)'"
            Note "Before this PR the objective could not reach the screener at all."
        } else {
            Bad "your plan says '$($plan.objective)' and the ranking was pinned to '$($pin.objective)'"
        }
    }
}

# ---------------------------------------------------------------------------
Sec "2  AND NOTHING WAS TILTED - the safety property of this PR"
# The table is deliberately empty. Hand-chosen weights were rejected as
# hardcoding, and the one derivation available (the factor signature of each
# objective's own strategy baskets) produced Income weighting GROWTH highest and
# INCOME lowest, because the sandbox provider seeds fundamentals off a sha1 of
# the ticker. So no tilt ships until there is a basis that survives measurement.
if ($null -ne $recs -and $null -ne $recs.buy_ideas_ranked_for) {
    $pin = $recs.buy_ideas_ranked_for
    $disc = "$($recs.buy_ideas_tilt_disclosure)"

    if ($pin.tilted -eq $true -or $null -ne $pin.weights) {
        Bad "a factor tilt is live: $($pin.weights | ConvertTo-Json -Compress)"
        Note "That is not what this PR shipped. Either someone added a tilt, or"
        Note "the build is not the one this script describes. Check before"
        Note "trusting any buy idea on the screen."
    } else {
        Ok "no tilt applied - every objective still ranks on DEFAULT_WEIGHTS"
        Note "Item 2 is HALF fixed: the wiring is in, the weights are not, and"
        Note "the reason is written down in docs/AGENTS.md rather than guessed."
    }
    if ($disc) {
        Bad "a tilt disclosure was printed while no tilt exists: $disc"
    } else {
        Ok "no card claims to have been tailored to your objective"
    }
}

# ---------------------------------------------------------------------------
Sec "3  THE ONE TO READ - how much of the universe was actually ranked?"
# The measurement this PR was built to make possible. In the sandbox: 20 of 58,
# with all seven Israeli names among the 38 dropped. Production may be fine.
# This prints the real number either way, and it decides PR 25.
if ($null -ne $recs -and $null -ne $recs.buy_ideas_ranked_for) {
    $cov = $recs.buy_ideas_ranked_for.coverage
    if ($null -eq $cov -or $null -eq $cov.requested) {
        Bad "no coverage on the pin - PR 24's reporting is not in this build"
    } else {
        $req = [int]$cov.requested; $got = [int]$cov.ranked
        $unreach = @($cov.unreachable)
        $nodata = @($cov.no_data)
        Note "candidates offered  : $req"
        Note "actually ranked     : $got"
        Note "unreachable         : $($unreach.Count)   (provider, not the ticker)"
        Note "no data             : $($nodata.Count)   (the provider answered: nothing there)"

        if ($unreach.Count -eq 0) {
            Ok "EVERY candidate was ranked. The rate limiter does not bite on Yahoo."
            Note "That answers it: the token-bucket problem is a sandbox artifact"
            Note "of the builtin provider being instant. PR 25 does not need to"
            Note "touch the limiter - only to add real Tel Aviv listings."
        } else {
            Bad "$($unreach.Count) of $req candidates were NEVER SCORED on the live app"
            Note "These names cannot win a buy card no matter how good they are,"
            Note "and which ones get dropped depends on transport timing rather"
            Note "than merit. PR 25 has to fix the limiter FIRST - adding new"
            Note "listings to a list that never gets read would change nothing."
            Note "Not scored: $($unreach -join ', ')"
        }

        $droppedIsraeli = @($unreach | Where-Object { $IsraeliNames -contains $_ })
        if ($droppedIsraeli.Count -gt 0) {
            Note ""
            Note "ITEM 3, MEASURED: $($droppedIsraeli.Count) of the 7 Israeli names in the"
            Note "universe were dropped before scoring - $($droppedIsraeli -join ', ')."
            Note "That is the SECOND cause of 'I never get Israeli cards'. The"
            Note "first is that all seven are US listings; there is still not one"
            Note "Tel Aviv listing in the candidate list. Both are PR 25."
        } elseif ($unreach.Count -eq 0) {
            Note ""
            Note "ITEM 3: all seven Israeli names WERE scored. So the only"
            Note "remaining cause is that every one of them is a US listing"
            Note "(NYSE/NASDAQ) - there is still not one Tel Aviv listing in the"
            Note "universe. PR 25."
        }
    }
}

# ---------------------------------------------------------------------------
Sec "4  the screener endpoint reads your objective"
# Before PR 24 the only tilt input on this route was a query string nothing
# built, so the one code path capable of tilting the screener was unreachable
# from the product. It is reachable now, even though it currently resolves to
# no tilt.
$scr = Api GET '/api/v1/screener?n_equities=5&n_commodities=0' 180
if ($null -eq $scr) {
    Bad "/screener returned nothing - see the line above for why"
} else {
    $src = "$($scr.ranked_for.source)"
    Note "tilt source: $src"
    if ($src -eq 'default') {
        Ok "the route consulted your objective and found no tilt defined"
        Note "That is the expected branch today, and it is a real branch rather"
        Note "than a fallthrough: an objective with no row ranks on"
        Note "DEFAULT_WEIGHTS instead of on whichever row looked closest."
    } elseif ($src -eq 'objective') {
        Bad "the route applied an objective tilt, which should not exist yet"
    } else {
        Bad "expected 'default', got '$src'"
    }
    Note "top picks: $((@($scr.equities) | ForEach-Object { $_.ticker }) -join ', ')"
}

# ---------------------------------------------------------------------------
Sec "5  nothing about your money moved"
$portfolio = Api GET '/api/v1/portfolio' 120
if ($null -eq $portfolio) {
    Bad "/portfolio returned nothing - see the line above for why"
} else {
    $nav = [math]::Round([double]$portfolio.nav_ils, 2)
    Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
    Ok "portfolio reads cleanly"
    Note "COMPARE THIS NAV TO BEFORE THE DEPLOY. This PR ranks nothing"
    Note "differently and trades nothing. If it moved, stop and tell me."
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
    Write-Host "  RED. Send me section 3's numbers either way - they decide PR 25." -ForegroundColor Red
    exit 1
}
Write-Host "  Green." -ForegroundColor Green
Write-Host ""
Write-Host "  SEND ME SECTION 3 REGARDLESS. Its numbers decide whether PR 25" -ForegroundColor Cyan
Write-Host "  has to fix the rate limiter or only add Tel Aviv listings." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Item 2 is HALF fixed. The objective reaches the screener; no" -ForegroundColor DarkYellow
Write-Host "    factor tilt is defined, because nothing in the codebase derives" -ForegroundColor DarkYellow
Write-Host "    one and hand-chosen numbers are hardcoding. Grow and Preserve" -ForegroundColor DarkYellow
Write-Host "    still rank identically." -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe, and all" -ForegroundColor DarkYellow
Write-Host "    seven Israeli names may be rate-limited out before scoring." -ForegroundColor DarkYellow
Write-Host "    Both are item 3. PR 25." -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows," -ForegroundColor DarkYellow
Write-Host "    forever - and is captioned with the price-refresh clock." -ForegroundColor DarkYellow
Write-Host "  * The Allocation Agent's veto is unreachable from the app." -ForegroundColor DarkYellow
Write-Host "  * settings.objective is threaded into every engine and read by" -ForegroundColor DarkYellow
Write-Host "    nothing; TaxEngine reads no plan setting at all." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED, and a Postgres run went red" -ForegroundColor DarkYellow
Write-Host "    once on trading_rules without repeating. Send me either block." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you accepted." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
