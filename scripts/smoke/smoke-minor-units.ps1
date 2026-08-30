# SMOKE - PR 18, against the LIVE deploy. READ-ONLY unless -AllowRefresh.
#
#   .\scripts\smoke\smoke-minor-units.ps1
#   .\scripts\smoke\smoke-minor-units.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-minor-units.ps1 -Sha 1234abc -AllowRefresh
#
# The claim: a Tel Aviv or London holding was valued ONE HUNDRED TIMES too high.
# Yahoo quotes those venues in a minor unit and says so in the CURRENCY CODE:
#
#     TEVA.TA  ILA 11030.0  = 110.30 ILS       VOD.L  GBp 118.45 = 1.1845 GBP
#
# The quote was stamped verbatim, so the position carried "ILA" and a price 100x
# too big, and every rate lookup for that code resolved to 1.0 - silently on the
# builtin provider, through the fail-safe on the live one.
#
# Sections 0-3 are read-only. Section 4 runs a price refresh, which is what
# repairs a row already stored in agorot; it reprices holdings but buys and sells
# nothing, and it is still opt-in.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$AllowRefresh)

. "$PSScriptRoot\..\agent-key.ps1"
$AgentKey = Get-AgentKey -BaseUrl $BaseUrl
if (-not $AgentKey) { exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $AgentKey }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

# Codes that mean "a hundredth of something". A position reporting one of these
# as its currency has not been normalised.
$MinorUnits = @('ILA', 'GBp', 'GBX', 'ZAc')
# Venues that quote in a minor unit, so the check knows where to look even when
# nothing is currently wrong.
$MinorVenues = @('TASE', 'LSE')

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }

# A 401 IS AN ANSWER. Reporting it as "did not answer" is the same class of
# defect these scripts exist to catch: two very different failures rendered
# identically, so the reader cannot tell an outage from a rejected key. The
# status code is printed whenever the server sent one.
function Api($method, $path, $timeout = 180) {
    # NOT $args. That is a PowerShell AUTOMATIC variable holding the function's
    # own unbound arguments, and assigning to it inside a function is the same
    # family of trap as the $h that once silently clobbered the auth headers.
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
Write-Host "InvestWise Pro - agorot and pence stop being valued as shekels and pounds" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

Sec "0  the deploy landed"
$health = Api GET '/health' 60
if ($null -eq $health) { Bad "/health unreachable - nothing below can be trusted" }
elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

# ONE read. NAV and the per-position currencies have to come from the same
# payload or the pair is incoherent.
Sec "1  no holding is priced in a minor unit"
$portfolio = Api GET '/api/v1/portfolio'
if ($null -eq $portfolio) { Bad "/portfolio returned nothing usable - see the line above for why" }
else {
    $rows = @($portfolio.positions)
    Write-Host "        NAV $([math]::Round([double]$portfolio.nav_ils, 2)) ILS across $($rows.Count) positions" -ForegroundColor DarkGray
    $bad = @($rows | Where-Object { $MinorUnits -contains "$($_.currency)" })
    if ($bad.Count -eq 0) {
        Ok "every position reports a major currency"
    } else {
        foreach ($b in $bad) {
            Write-Host "        $($b.ticker) reports $($b.currency) at $($b.current_price) - that is a hundredth unit" -ForegroundColor Red
        }
        Bad "$($bad.Count) position(s) still stamped in a minor unit; each is valued 100x too high"
    }
}

Sec "2  the venues that quote in minor units"
if ($null -eq $portfolio) { Skip "no portfolio payload. SKIP, not PASS." }
else {
    $atRisk = @($portfolio.positions | Where-Object { $MinorVenues -contains "$($_.market)" -and $_.ticker -ne 'CASH' })
    if ($atRisk.Count -eq 0) {
        Skip "no Tel Aviv or London holding on this book, so the fix is untested against a real one here. SKIP, not PASS."
        Write-Host "        The unit tests cover it: tests\test_minor_unit_currencies.py" -ForegroundColor DarkGray
    } else {
        foreach ($r in $atRisk) {
            $q = if ($r.price_currency_quoted) { " (provider said $($r.price_currency_quoted))" } else { "" }
            Write-Host "        $($r.ticker) on $($r.market): $($r.current_price) $($r.currency)$q, value $($r.value_ils) ILS" -ForegroundColor DarkGray
            if ($MinorUnits -contains "$($r.currency)") {
                Bad "$($r.ticker) is stored in $($r.currency)"
            } elseif ($r.price_currency_quoted -and ($MinorUnits -contains "$($r.price_currency_quoted)")) {
                Ok "$($r.ticker): the provider quoted $($r.price_currency_quoted) and it was normalised to $($r.currency)"
            } else {
                Ok "$($r.ticker) reports $($r.currency)"
            }
        }
    }
}

Sec "3  the payload can say what the provider actually quoted"
if ($null -eq $portfolio -or @($portfolio.positions).Count -eq 0) {
    Skip "no positions to inspect. SKIP, not PASS."
} else {
    $first = @($portfolio.positions)[0]
    # The FIELD must exist even when its value is null - that is what makes the
    # normalisation observable from outside the process instead of inferred.
    if ($first.PSObject.Properties.Name -contains 'price_currency_quoted') {
        Ok "positions carry price_currency_quoted, so a normalisation is auditable"
    } else {
        Bad "no price_currency_quoted on the payload - this is the OLD build"
    }
}

Sec "4  the repair pass"
if (-not $AllowRefresh) {
    Skip "read-only run. A row ALREADY stored in agorot is repaired by a price refresh."
    Write-Host "        Re-run with -AllowRefresh to trigger one. It reprices" -ForegroundColor DarkGray
    Write-Host "        holdings; it buys and sells nothing." -ForegroundColor DarkGray
} else {
    $res = Api POST '/api/v1/portfolio/refresh-prices'
    if ($null -eq $res) { Bad "the refresh returned nothing usable - see the line above for why" }
    else {
        Write-Host "        updated $($res.updated), failed $($res.failed), stale $($res.stale)" -ForegroundColor DarkGray
        if ($null -eq $res.PSObject.Properties['minor_unit_quotes']) {
            Bad "no minor_unit_quotes in the refresh report - this is the OLD build"
        } else {
            Ok "the refresh reports minor-unit handling: $($res.minor_unit_quotes) quote(s) normalised, $($res.repaired_minor_unit_rows) row(s) repaired"
        }
        $after = Api GET '/api/v1/portfolio'
        if ($null -eq $after) { Bad "/portfolio returned nothing usable after the refresh" }
        else {
            $stillBad = @($after.positions | Where-Object { $MinorUnits -contains "$($_.currency)" })
            if ($stillBad.Count -eq 0) { Ok "after the refresh, no holding is priced in a minor unit" }
            else { Bad "$($stillBad.Count) position(s) still in a minor unit after a refresh" }
            $delta = [double]$after.nav_ils - [double]$portfolio.nav_ils
            Write-Host "        NAV $([math]::Round([double]$portfolio.nav_ils,2)) -> $([math]::Round([double]$after.nav_ils,2)) (delta $([math]::Round($delta,2)) ILS)" -ForegroundColor DarkGray
            if ([math]::Abs($delta) -gt 1000) {
                Write-Host "        A LARGE move here is expected if a holding was stored in agorot:" -ForegroundColor Yellow
                Write-Host "        that is the 100x error being corrected, not new money." -ForegroundColor Yellow
            }
        }
    }
}

Write-Host ""
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NOT A PASS. $($script:Skipped) check(s) never ran." -ForegroundColor Yellow
}
Write-Host "  Not financial advice." -ForegroundColor DarkGray
Write-Host ""
if ($script:Fail -gt 0) { exit 1 }
exit 0
