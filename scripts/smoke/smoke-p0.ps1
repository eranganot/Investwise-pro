# SMOKE - P0 safety batch: the four ways the app could quietly mislead you.
#
#   .\scripts\smoke\smoke-p0.ps1                 # read-only, then chains smoke-e2e
#   .\scripts\smoke\smoke-p0.ps1 -SkipChain      # P0's own checks only
#   .\scripts\smoke\smoke-p0.ps1 -Execute        # ALSO really funds the sleeve (WRITES)
#
# READ-ONLY by default. Every P0.1 check uses dry_run, so nothing is bought,
# sold or deleted unless you pass -Execute. This script never exercises the
# destructive `mode:"replace"` path against your live book -- it asserts that the
# confirm DATA exists (which positions, worth how much) and that replace is never
# the default. Proving deletion works by deleting your holdings is not a test.
#
# Per the phase convention: own checks first, then every earlier phase's smoke
# (here: smoke-e2e.ps1), then one combined verdict.
#
# Rules carried over, each learned the hard way:
#   * A SKIP is never a PASS.
#   * Never assert a guess -- read the plan's own caps, never assume them.
#   * Read paired state in one breath (one /portfolio call feeds every check).
#   * Print `degraded` on failure: "no cards because nothing fired" and "no cards
#     because the agent raised" look identical without it.
#   * Print the exception TYPE and message on the FIRST failure.
#   * Headers are $ApiHeaders, never $h -- PowerShell variable names are
#     case-insensitive and a stray $h clobbered them once, costing a full day and
#     four wrong hypotheses.

param(
    [switch]$Execute,
    [switch]$SkipChain,
    [string]$Strategy = "btm_trend_tqqq",
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 90) {
    try {
        if ($null -ne $body) {
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders `
                -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 6) -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        # The exception TYPE and message, on the FIRST failure. Collapsing errors
        # into "NO RESPONSE" once produced four wrong hypotheses and a production
        # DB change shipped on false evidence.
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: P0 safety batch  ($BaseUrl)" -ForegroundColor White
if (-not $Execute) { Write-Host "read-only (pass -Execute to actually fund a sleeve)" -ForegroundColor DarkGray }

# One read of the paired state, used by every check below. Two failures in an
# earlier session were a stale snapshot compared against fresh data -- the app
# was right both times.
$pf   = Api GET '/api/v1/portfolio'
$plan = Api GET '/api/v1/plan'

# =========================================================================== #
Sec "P0.1  'Load this basket' must not delete the book it is meant to join"
# =========================================================================== #
$dryFund = Api POST "/api/v1/strategies/$Strategy/load-basket" @{ dry_run = $true }
if ($null -eq $dryFund) {
    Bad "load-basket (dry_run) unreachable - P0.1 cannot be assessed"
}
elseif ($null -eq $dryFund.mode) {
    Bad "response carries no 'mode' - P0.1 has not deployed to this environment"
}
else {
    # The safety property: omitting `mode` on a rule-based strategy must NOT
    # resolve to the destructive path.
    if ($dryFund.mode -eq 'fund') { Ok "default mode for '$Strategy' is 'fund', not 'replace'" }
    else { Bad "default mode for '$Strategy' is '$($dryFund.mode)' - the destructive path is one click away again" }

    if ($dryFund.dry_run -ne $true) { Bad "dry_run was requested but the response does not say dry_run:true" }
    else { Ok "dry_run acknowledged - nothing was written" }

    if ($dryFund.ok -eq $false) {
        # Abstaining is CORRECT when the sleeve cannot be funded. It must say why.
        if ($dryFund.reason) { Ok "abstained rather than half-executing: $($dryFund.reason)" }
        else { Bad "refused with no reason - an abstention without a cause is indistinguishable from a bug" }
    }
    elseif ($dryFund.nothing_to_do) {
        Skip "already at the sleeve target, so there is nothing to fund: $($dryFund.message)"
    }
    else {
        if ($dryFund.buys -and $dryFund.buys.Count -gt 0) {
            Ok "$($dryFund.buys.Count) sleeve leg(s) sized: $(($dryFund.buys | ForEach-Object { "$($_.ticker) ~$([math]::Round($_.buy_ils))" }) -join ', ')"
        } else { Bad "no buy legs in a fund plan that claims ok:true" }

        $f = $dryFund.funding
        if ($null -eq $f) { Bad "no funding plan - the whole point of fund mode is naming the money" }
        else {
            Ok "funded from cash $([math]::Round($f.from_cash_ils)) + $(@($f.sells).Count) trim(s), est. tax $([math]::Round($f.tax_ils))"
            foreach ($s in @($f.sells)) {
                if (-not $s.ticker -or -not $s.shares -or $null -eq $s.tax_ils -or -not $s.reason) {
                    Bad "a funding leg is missing ticker / shares / est. tax / reason: $($s | ConvertTo-Json -Compress)"
                }
            }
            if (@($f.sells).Count -gt 0) { Ok "every funding leg names ticker, shares, est. CGT and why it was chosen" }

            # The money must not come out of the sleeve it is buying.
            $sleeveTickers = @($dryFund.buys | ForEach-Object { $_.ticker })
            $selfFunded = @($f.sells | Where-Object { $sleeveTickers -contains $_.ticker })
            if ($selfFunded.Count -gt 0) { Bad "the plan sells $($selfFunded[0].ticker) to buy $($selfFunded[0].ticker)" }
            else { Ok "no leg funds itself" }
        }
        if (-not $dryFund.broker_note) { Bad "no broker note - every money card must say no order was placed" }
        else { Ok "states plainly that no brokerage order is placed" }
    }
}

# The confirm data for the destructive path must exist WITHOUT running it.
$dryRepl = Api POST "/api/v1/strategies/$Strategy/load-basket" @{ mode = 'replace'; dry_run = $true }
if ($null -eq $dryRepl) { Bad "replace dry_run unreachable" }
elseif ($dryRepl.ok -eq $false) { Skip "replace preview refused: $($dryRepl.error)" }
elseif ($null -eq $dryRepl.removing) {
    Bad "replace preview does not list what it would delete - the confirm would say 'your current holdings' again"
}
else {
    $held = @($pf.positions | Where-Object { $_.ticker -ne 'CASH' })
    $named = @($dryRepl.removing | ForEach-Object { $_.ticker })
    $missing = @($held | Where-Object { $named -notcontains $_.ticker })
    if ($null -eq $pf) { Skip "portfolio unreachable, cannot compare the deletion list against the book" }
    elseif ($missing.Count -gt 0) { Bad "replace preview omits $(($missing | ForEach-Object { $_.ticker }) -join ', ') from what it would delete" }
    else { Ok "replace names every holding it would delete ($($named.Count)), worth $([math]::Round($dryRepl.removing_value_ils))" }
    if ($dryRepl.removing.Count -gt 0 -and ($dryRepl.removing | Where-Object { $null -eq $_.value_ils }).Count -gt 0) {
        Bad "a deletion line has no value - the confirm must show what each position is worth"
    }
}

if ($Execute) {
    Sec "P0.1  executing the funding plan for real (WRITES to your tracked book)"
    $before = @{}
    foreach ($p in @($pf.positions)) { $before[$p.ticker] = [double]$p.quantity }
    $run = Api POST "/api/v1/strategies/$Strategy/load-basket" @{ mode = 'fund' } 180
    if ($null -eq $run) { Bad "fund execution unreachable" }
    elseif ($run.ok -eq $false) { Skip "fund abstained (correct if unfundable): $($run.reason)" }
    else {
        $after = Api GET '/api/v1/portfolio'
        if ($null -eq $after) { Bad "portfolio unreachable after funding - cannot verify the book survived" }
        else {
            $soldTickers = @($run.sold | ForEach-Object { $_.ticker })
            $vanished = @()
            foreach ($tk in $before.Keys) {
                if ($tk -eq 'CASH') { continue }
                $still = @($after.positions | Where-Object { $_.ticker -eq $tk })
                if ($still.Count -eq 0 -and $soldTickers -notcontains $tk) { $vanished += $tk }
            }
            if ($vanished.Count -gt 0) { Bad "holdings disappeared without being named as funding legs: $($vanished -join ', ')" }
            else { Ok "every holding not named as a funding leg survived" }
            foreach ($b in @($run.bought)) { Ok "bought $($b.ticker): $([math]::Round($b.shares,4)) sh (~$([math]::Round($b.amount_ils)))" }
            foreach ($s in @($run.skipped)) { Skip "leg skipped: $($s.ticker) - $($s.reason)" }
        }
    }
} else {
    Skip "fund execution not run (read-only). Re-run with -Execute to verify the write path."
}

# =========================================================================== #
Sec "P0.2  a kept measurement must render as numbers, not 'Couldn't measure'"
# =========================================================================== #
$bt = Api GET '/api/v1/strategies/backtests' 120
if ($null -eq $bt) { Bad "/strategies/backtests unreachable" }
elseif (@($bt.strategies).Count -eq 0) { Bad "catalog is empty" }
else {
    $kept = @($bt.strategies | Where-Object { $_.backtest -and -not $_.backtest.ok -and $null -ne $_.backtest.metrics.cagr_pct })
    $blank = @($bt.strategies | Where-Object { $_.backtest -and -not $_.backtest.ok -and $null -eq $_.backtest.metrics.cagr_pct })
    if ($kept.Count -gt 0) {
        Ok "$($kept.Count) strategy(ies) kept their measurement through a failed refresh - the card must show chips"
        foreach ($k in $kept) {
            if ($null -eq $k.backtest.last_error -and -not $k.backtest.refresh_failing) {
                Bad "$($k.id): kept metrics but carries no last_error/refresh_failing, so the UI cannot say the refresh is failing"
            } else {
                Ok "$($k.id): ~$($k.backtest.metrics.cagr_pct)%/yr kept, refresh failing since $($k.backtest.last_error_at)"
            }
        }
    } else {
        Skip "no strategy is currently in the kept-metrics state, so this path is not exercised right now"
    }
    if ($blank.Count -gt 0) { Ok "$($blank.Count) genuinely unmeasured strategy(ies) - these SHOULD show the failure state" }
}

# =========================================================================== #
Sec "P0.3  a dead ticker's frozen price must not be accepted as current"
# =========================================================================== #
if ($null -eq $pf) { Bad "/portfolio unreachable - freshness cannot be assessed" }
elseif ($null -eq $pf.PSObject.Properties['stale_positions']) {
    Bad "/portfolio has no stale_positions field - P0.3 has not deployed to this environment"
}
else {
    Ok "/portfolio reports stale_positions ($(@($pf.stale_positions).Count)) and stale_value_ils ($([math]::Round($pf.stale_value_ils)))"

    $equities = @($pf.positions | Where-Object { $_.ticker -ne 'CASH' -and $_.asset_class -ne 'Cash' })
    if ($equities.Count -eq 0) { Skip "no non-cash holdings to audit" }
    else {
        $unknown = @($equities | Where-Object { -not $_.price_freshness })
        if ($unknown.Count -eq $equities.Count) {
            Bad "no holding carries price_freshness - the 30-min job has not run since P0.3 deployed"
            Write-Host "        -> POST /api/v1/portfolio/refresh-prices, then re-run" -ForegroundColor Yellow
        }
        elseif ($unknown.Count -gt 0) {
            Skip "$($unknown.Count) holding(s) not yet re-examined: $(($unknown | ForEach-Object { $_.ticker }) -join ', ')"
        }

        # The invariant: an old price_as_of and price_stale:false is the original
        # bug reappearing. Five trading days is roughly a calendar week; allow 9
        # calendar days so a holiday-shortened week can never manufacture a FAIL.
        $now = (Get-Date).ToUniversalTime()
        $liars = @()
        foreach ($p in $equities) {
            if (-not $p.price_as_of) { continue }
            $asOf = $null
            try { $asOf = [datetime]::Parse($p.price_as_of).ToUniversalTime() } catch { continue }
            $age = ($now - $asOf).TotalDays
            if ($age -gt 9 -and -not $p.price_stale) { $liars += "$($p.ticker) (last trade $($asOf.ToString('yyyy-MM-dd')), $([math]::Round($age))d, NOT flagged)" }
        }
        if ($liars.Count -gt 0) {
            Bad "a frozen price is still being treated as current: $($liars -join '; ')"
        } else {
            Ok "every holding's price is either inside the freshness window or flagged"
        }

        foreach ($s in @($pf.stale_positions)) {
            Write-Host "        stale: $($s.ticker) - last trade $($s.price_as_of), worth $([math]::Round($s.value_ils))" -ForegroundColor DarkYellow
        }
        # A flagged holding must be visible on Today as guidance, not silently
        # written off.
        if (@($pf.stale_positions).Count -gt 0) {
            $recsForStale = Api GET '/api/v1/recommendations' $null 120
            if ($null -eq $recsForStale) { Skip "recommendations unreachable, cannot confirm the stale holding reaches Today" }
            else {
                foreach ($s in @($pf.stale_positions)) {
                    $card = @($recsForStale.recommendations | Where-Object { $_.title -like "*$($s.ticker) has not traded since*" })
                    if ($card.Count -eq 0) {
                        Bad "$($s.ticker) is flagged stale but produced no Today card (degraded: $($recsForStale.degraded -join ', '))"
                    } else {
                        $kind = $card[0].apply.kind
                        if ($kind -and $kind -ne 'none') { Bad "$($s.ticker)'s stale card is executable ('$kind') - it must be guidance, never an automatic write-off" }
                        else { Ok "$($s.ticker) surfaces on Today as guidance" }
                    }
                }
            }
        } else {
            Ok "nothing is currently stale (and the check is live, not absent)"
        }
    }
}

# =========================================================================== #
Sec "P0.4  suggested protective rules must reach Today"
# =========================================================================== #
$sugg = Api GET '/api/v1/rules/suggestions' $null 120
$recs = Api GET '/api/v1/recommendations' $null 120
if ($null -eq $recs) { Bad "/recommendations unreachable - P0.4 cannot be assessed" }
else {
    if ($recs.degraded -and @($recs.degraded).Count -gt 0) {
        Bad "agents degraded: $(@($recs.degraded) -join ', ')"
        Write-Host "        a degraded agent means a MISSING card says nothing about the underlying state" -ForegroundColor Yellow
    } else { Ok "no agent degraded" }

    $cards = @($recs.recommendations | Where-Object { $_.title -like '*ready to arm*' })
    if ($null -eq $sugg) {
        Skip "/rules/suggestions unreachable, so a missing card cannot be judged"
    }
    else {
        $count = 0
        foreach ($h in @($sugg.suggestions)) { $count += @($h.rules).Count }
        if ($count -eq 0 -and @($sugg).Count -gt 0 -and $null -eq $sugg.suggestions) {
            # Older shape: a bare list of holdings.
            foreach ($h in @($sugg)) { $count += @($h.rules).Count }
        }
        if ($count -eq 0) {
            if ($cards.Count -eq 0) { Ok "nothing to suggest and no card - consistent (every rule type is already armed)" }
            else { Bad "a 'ready to arm' card is showing but the suggester has nothing to suggest" }
        }
        elseif ($cards.Count -eq 0) {
            Bad "$count suggested rule(s) exist but none reached Today - they are still buried on the Rules page"
        }
        elseif ($cards.Count -gt 1) {
            Bad "$($cards.Count) suggestion cards on Today - it must be ONE card for the set, not one per rule"
        }
        else {
            Ok "$count suggested rule(s) reach Today as exactly one card"
            $ap = $cards[0].apply
            if ($ap.kind -ne 'create_rules') { Bad "the card's apply kind is '$($ap.kind)', so Accept would not arm anything" }
            elseif (@($ap.rules).Count -ne $count) { Bad "the card carries $(@($ap.rules).Count) specs against $count suggestions" }
            else { Ok "Accept would arm all $count of them (kind=create_rules)" }
            foreach ($r in @($ap.rules)) {
                if (-not $r.ticker -or -not $r.rule_type -or $null -eq $r.level) {
                    Bad "an armable spec is incomplete: $($r | ConvertTo-Json -Compress)"
                }
            }
        }
    }
}

# =========================================================================== #
Write-Host "`n===== P0: $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run." -ForegroundColor DarkYellow }

# ---------- chain every earlier phase ----------
if ($SkipChain) {
    Write-Host "`n-SkipChain: smoke-e2e was NOT run, so this is a partial verdict.`n" -ForegroundColor DarkYellow
    exit $(if ($fail) { 1 } else { 0 })
}

Write-Host "`n`n===== CHAINING: smoke-e2e.ps1 (everything before P0) =====" -ForegroundColor Magenta
$here = $PSScriptRoot
$childOut = & "$here\smoke-e2e.ps1" -BaseUrl $BaseUrl 2>&1 | Tee-Object -Variable _tee | Out-String

# Parse the child tallies rather than trusting an exit code the child never sets.
# If they cannot be read, that is a FAIL: an unreadable result is not a pass.
$cp = 0; $cf = 0; $cs = 0; $found = 0
foreach ($m in [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped')) {
    $cp += [int]$m.Groups[1].Value; $cf += [int]$m.Groups[2].Value; $cs += [int]$m.Groups[3].Value; $found++
}

Write-Host "`n`n===== COMBINED VERDICT =====" -ForegroundColor Magenta
if ($found -eq 0) {
    Write-Host "FAIL  could not read any tally from smoke-e2e - its result is unknown, which is not a pass." -ForegroundColor Red
    $fail++
} else {
    Write-Host "  smoke-e2e ($found section(s)): $cp passed, $cf failed, $cs skipped" -ForegroundColor Gray
    $pass += $cp; $fail += $cf; $skip += $cs
}
Write-Host "  P0 + earlier phases: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "  A SKIP is not a PASS." -ForegroundColor DarkYellow }
Write-Host ""
exit $(if ($fail) { 1 } else { 0 })
