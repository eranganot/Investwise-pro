# SMOKE - PR 19, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-contradictions.ps1
#   .\scripts\smoke\smoke-contradictions.ps1 -Sha 1234abc
#
# The claim: on 2026-08-29 one card sold ~1,071 ILS of Equities ("Rebalance
# toward your target mix") while another bought ~3,139 of SOXL - which IS
# Equities - and contradictions.py rule 2 reported nothing. Measured on that
# pair: 0 contradictions, 0 notes on either card.
#
# Two holes: the sell side matched on title text, and a ticker-scoped card with
# no asset_class was skipped entirely. Rule 2 now reads apply.legs[] and
# classifies tickers instead of reading titles.
#
# Entirely read-only: contradictions are computed on the GET, so nothing here
# needs to accept anything.
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

$MovesMoney = @('trim', 'sell', 'sell_losers', 'buy', 'buy_funded',
                'rebalance_to_objective', 'redeploy_cash', 'fund_sleeve', 'swap')

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
Write-Host "InvestWise Pro - the contradiction check stops reading card titles" -ForegroundColor White
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

# ONE read. The cards and the contradictions computed over them must come from
# the same payload, or they describe two different feeds.
Sec "1  the feed, and what it says about itself"
$recs = Api GET '/api/v1/recommendations'
$cards = @()
if ($null -eq $recs) { Bad "/recommendations returned nothing usable - see the line above for why" }
else {
    $cards = @($recs.recommendations)
    $money = @($cards | Where-Object { $MovesMoney -contains "$($_.apply.kind)" })
    Write-Host "        $($cards.Count) card(s), $($money.Count) of which move money" -ForegroundColor DarkGray
    if ($null -eq $recs.PSObject.Properties['contradictions']) {
        Bad "no contradictions[] on the payload at all"
    } else {
        Ok "the payload reports contradictions[]: $(@($recs.contradictions).Count) found"
        foreach ($c in @($recs.contradictions)) {
            Write-Host "        $($c.kind): $($c.detail)" -ForegroundColor DarkGray
        }
    }
}

Sec "2  a merged rebalance describes itself as data, not as prose"
$rebal = @($cards | Where-Object { $_.apply.kind -eq 'rebalance_to_objective' })
if ($rebal.Count -eq 0) {
    Skip "no rebalance card on the feed right now. SKIP, not PASS - untested, not fixed."
} else {
    foreach ($r in $rebal) {
        $legCount = @($r.apply.legs).Count
        $mergedCount = @($r.meta.merged_legs).Count
        Write-Host "        '$($r.title)': $legCount structural leg(s), $mergedCount merged-leg label(s)" -ForegroundColor DarkGray
        if ($mergedCount -gt 1 -and $legCount -lt $mergedCount) {
            Bad "the card absorbed $mergedCount legs but carries $legCount as data - this is the OLD build"
        } elseif ($mergedCount -gt 1) {
            Ok "a merged card carries one structural leg per leg it absorbed"
            foreach ($lg in @($r.apply.legs)) {
                Write-Host "          leg: $($lg.direction) $($lg.asset_class) ~$([math]::Round([double]$lg.amount_ils,0))" -ForegroundColor DarkGray
                if (-not $lg.direction) { Bad "a leg has no direction, so its way is unreadable except from prose" }
            }
        } else {
            # Unmerged single-leg card: the direction still has to be recorded.
            if ($r.apply.direction) { Ok "'$($r.title)': direction recorded as '$($r.apply.direction)'" }
            else { Bad "'$($r.title)' records no direction - the only way left to read it is the title" }
        }
    }
}

Sec "3  no two live cards buy and sell the same class without saying so"
if ($null -eq $recs) { Skip "no feed to inspect. SKIP, not PASS." }
else {
    $clashes = @($recs.contradictions | Where-Object { $_.kind -eq 'buys_and_sells_one_class' })
    if ($clashes.Count -eq 0) {
        Ok "nothing on this feed buys and sells one class (or there is only one such card)"
    } else {
        # Reported, not suppressed - so BOTH cards must still be there and BOTH
        # must carry a note. A contradiction nobody is told about is the bug.
        foreach ($c in $clashes) {
            Write-Host "        $($c.asset_class): $($c.card) vs $($c.other)" -ForegroundColor DarkGray
            $a = @($cards | Where-Object { $_.id -eq $c.card })
            $b = @($cards | Where-Object { $_.id -eq $c.other })
            if ($a.Count -eq 0 -or $b.Count -eq 0) {
                Bad "a contradiction names a card that is not on the feed"
            } elseif (@($a[0].notes).Count -gt 0 -and @($b[0].notes).Count -gt 0) {
                Ok "$($c.asset_class): both cards survive and both name the other"
            } else {
                Bad "$($c.asset_class): a card in a contradiction carries no note, so the screen says nothing"
            }
        }
    }
}

Sec "4  every money-moving card can be read without its title"
if ($cards.Count -eq 0) { Skip "no cards to inspect. SKIP, not PASS." }
else {
    $money = @($cards | Where-Object { $MovesMoney -contains "$($_.apply.kind)" })
    if ($money.Count -eq 0) {
        Skip "no money-moving card on the feed. SKIP, not PASS."
    } else {
        $unreadable = @()
        foreach ($m in $money) {
            $hasLegs = @($m.apply.legs).Count -gt 0
            $hasClass = [bool]$m.apply.asset_class
            $hasTickers = ([bool]$m.apply.ticker) -or (@($m.apply.tickers).Count -gt 0)
            if (-not ($hasLegs -or $hasClass -or $hasTickers)) { $unreadable += $m }
        }
        if ($unreadable.Count -eq 0) {
            Ok "all $($money.Count) money-moving card(s) name legs, a class, or tickers"
        } else {
            foreach ($u in $unreadable) { Write-Host "        '$($u.title)' ($($u.apply.kind))" -ForegroundColor Red }
            Bad "$($unreadable.Count) card(s) can only be classified by reading their title"
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
