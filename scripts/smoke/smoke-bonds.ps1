# SMOKE - PR 10, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-bonds.ps1
#   .\scripts\smoke\smoke-bonds.ps1 -Sha 1234abc
#
# One claim, false on every book this app has ever managed until today: the
# Fixed Income target can actually be filled. The universe held 64 Equities, 19
# Commodities and ZERO bonds, so a Grow plan targeting 10% bonds - or this
# book's core targeting 14.4% - reported the money as unplaced on every scan.
#
# The suite proves the catalogue and the ranker. What only production can answer
# is whether THIS book's bond leg now gets a ticker instead of an apology.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }

function Api($method, $path, $timeout = 300) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $timeout
    } catch {
        Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - the Fixed Income leg can be filled" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# =========================================================================== #
Sec "0  the deploy landed"
# =========================================================================== #
$health = Api GET '/health' 60
if ($null -eq $health) { Bad "/health unreachable - nothing below can be trusted" }
elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

# =========================================================================== #
Sec "1  the book wants bonds, and a candidate exists for them"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations'
if ($null -eq $recs) {
    Bad "cannot read /recommendations - nothing below can run"
} else {
    $fiTarget = 0.0
    if ($recs.book_target -and $recs.book_target.target) {
        $fiTarget = [double]($recs.book_target.target.'Fixed Income')
    }
    if ($fiTarget -le 0) {
        Skip "this book targets no Fixed Income, so there is no leg to fill"
    } else {
        Write-Host "        Fixed Income target: $([Math]::Round($fiTarget*100,1))% of the book" -ForegroundColor DarkGray
    }

    $bondIdeas = @(@($recs.buy_ideas) | Where-Object { $_.sector -eq 'Fixed Income' })
    if ($bondIdeas.Count -gt 0) {
        Ok "the screener offers $($bondIdeas.Count) bond candidate(s): $((@($bondIdeas | ForEach-Object { $_.ticker })) -join ', ')"
    } else {
        Bad "no Fixed Income buy idea at all - this deploy predates PR 10, or the provider is down"
    }

    # A bond must never be ranked on momentum. If this flag is missing the
    # commodity ranker has been reused, which buys the most duration right
    # after the rate move that rewarded it.
    $momentum = @($bondIdeas | Where-Object { $_.flags -contains 'momentum-ranked' })
    if ($momentum.Count -eq 0 -and $bondIdeas.Count -gt 0) {
        Ok "no bond idea is momentum-ranked"
    } elseif ($momentum.Count -gt 0) {
        Bad "$($momentum.Count) bond idea(s) are momentum-ranked - wrong ranker"
    }

    # Credit is equity risk in a bond wrapper. It must not arrive as ballast.
    $credit = @($bondIdeas | Where-Object { $_.ticker -in @('HYG', 'JNK', 'USHY', 'SHYG', 'EMB') })
    if ($credit.Count -eq 0) { Ok "no high-yield or EM credit offered as ballast" }
    else { Bad "credit offered as ballast: $((@($credit | ForEach-Object { $_.ticker })) -join ', ')" }
}

# =========================================================================== #
Sec "2  US municipals are withheld from a non-USD book"
# =========================================================================== #
$plan = Api GET '/api/v1/plan' 60
if ($null -eq $plan -or $null -eq $recs) {
    Skip "cannot read the plan currency"
} else {
    $ccy = "$($plan.currency)"
    $munis = @(@($recs.buy_ideas) | Where-Object { $_.ticker -in @('MUB', 'VTEB') })
    if ($ccy -eq 'USD') {
        Skip "USD book - municipals are a legitimate option here"
    } elseif ($munis.Count -eq 0) {
        Ok "$ccy book, no US municipal bond offered (its tax break is priced into the yield)"
    } else {
        Bad "$ccy book is being offered $((@($munis | ForEach-Object { $_.ticker })) -join ', ')"
    }
}

# =========================================================================== #
Sec "3  THE ONE THING ONLY PRODUCTION CAN ANSWER: is the leg still unplaced?"
# =========================================================================== #
if ($null -eq $recs) {
    Skip "no recommendations payload"
} else {
    $redeploy = @(@($recs.recommendations) | Where-Object { "$($_.apply.kind)" -eq 'redeploy_cash' })
    if ($redeploy.Count -eq 0) {
        Skip "no redeploy card right now (no spendable cash), so no leg to place"
        Write-Host "        This is NOT a pass. The fix is unproven on this book until" -ForegroundColor DarkGray
        Write-Host "        there is idle cash above the floor for a card to spend." -ForegroundColor DarkGray
    } else {
        $card = $redeploy[0]
        $legs = @($card.apply.legs)
        $fiLegs = @($legs | Where-Object { $_.asset_class -eq 'Fixed Income' })
        $unplaced = @($card.meta.unplaced)
        $fiUnplaced = @($unplaced | Where-Object { $_.asset_class -eq 'Fixed Income' })

        foreach ($l in $legs) {
            Write-Host "        LEG $($l.asset_class): $($l.ticker) $([Math]::Round([double]$l.amount_ils))" -ForegroundColor DarkGray
        }
        foreach ($u in $unplaced) {
            Write-Host "        UNPLACED $($u.asset_class): $([Math]::Round([double]$u.amount_ils)) - $($u.reason)" -ForegroundColor DarkGray
        }

        if ($fiUnplaced.Count -gt 0) {
            Bad "the Fixed Income leg is STILL unplaced: $($fiUnplaced[0].reason)"
        } elseif ($fiLegs.Count -gt 0) {
            Ok "Fixed Income leg placed: $($fiLegs[0].ticker) for $([Math]::Round([double]$fiLegs[0].amount_ils))"
        } else {
            Skip "no Fixed Income leg on this card - the class may already be at target"
        }
    }
}

# =========================================================================== #
Sec "4  every bond named is one the provider can actually price"
# =========================================================================== #
# The COW lesson, applied before it costs anything: four delisted ETNs sat in
# the commodity catalogue being recommended for months because nothing checked.
if ($null -eq $recs) {
    Skip "no recommendations payload"
} else {
    $named = @()
    foreach ($b in @(@($recs.buy_ideas) | Where-Object { $_.sector -eq 'Fixed Income' })) { $named += $b.ticker }
    foreach ($cd in @($recs.recommendations)) {
        foreach ($l in @($cd.apply.legs)) { if ($l.asset_class -eq 'Fixed Income') { $named += $l.ticker } }
    }
    $named = @($named | Sort-Object -Unique)
    if ($named.Count -eq 0) {
        Skip "no bond named anywhere on Today"
    } else {
        $stale = @()
        $unreachable = @()
        foreach ($t in $named) {
            try {
                $uri = "$BaseUrl/api/v1/market/quote?ticker=$t"
                $q = Invoke-RestMethod -Method GET -Uri $uri -Headers $ApiHeaders -TimeoutSec 60
                if ($null -eq $q.price -or [double]$q.price -le 0) { $stale += $t }
            } catch {
                # 429 / 503 are the PROVIDER, not the ticker - the same
                # distinction PR 10 fixed in the screener. Reporting a
                # rate-limited quote as a dead bond would be the exact bug.
                $code = try { [int]$_.Exception.Response.StatusCode } catch { 0 }
                if ($code -in @(429, 503)) { $unreachable += $t }
                else {
                    Write-Host "        $t : $($_.Exception.GetType().Name)" -ForegroundColor DarkGray
                    $stale += $t
                }
            }
        }
        if ($stale.Count -eq 0 -and $unreachable.Count -eq 0) {
            Ok "all $($named.Count) named bond(s) have a live price: $($named -join ', ')"
        } elseif ($stale.Count -eq 0) {
            Skip "provider unreachable for $($unreachable -join ', ') - NOT evidence they are dead"
        } else {
            Bad "named but not priceable: $($stale -join ', ')"
        }
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
