# SMOKE - PR 13 / T6b, against the LIVE deploy.
#
#   .\scripts\smoke\smoke-t6b-core-search.ps1
#   .\scripts\smoke\smoke-t6b-core-search.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-t6b-core-search.ps1 -Search     # runs the real search
#
# -Search is opt-in because it is two simulations for each of fifteen
# candidates - measured at ~54 seconds against live data. It WRITES a stored
# verdict row. It changes no holdings, and there is no apply path from this
# route at all.
#
# Four claims, all false before this deploy:
#
#   1. The 14 catalog families can stand as candidate cores. They are stored as
#      [[ticker, weight]] and the solver needs {ticker: fraction}; no converter
#      existed.
#   2. Both windows are measured for every candidate. Ranked on the recent one,
#      with the ten-year beside every row - because BND measures -4% over 260
#      sessions and -24% over ten, and the four bond-heavy families are exactly
#      the ones a drawdown ceiling selects for.
#   3. A search that finds NOTHING admissible says so. On this book all fifteen
#      breach the 13.3% ceiling; the shallowest is 31.9%.
#   4. The Today card stops saying "change the core" once that is refuted.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$Search,
    [double]$MaxDrawdownPct = 13.3)

$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
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
Write-Host "InvestWise Pro - the solver varies the core" -ForegroundColor White
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
Sec "1  the search runs, and every candidate carries BOTH windows"
# =========================================================================== #
$res = $null
if ($Search) {
    Write-Host "        searching against a $MaxDrawdownPct% ceiling - this takes about a minute" -ForegroundColor DarkGray
    $res = Api GET "/api/v1/plan/target/cores?max_drawdown_pct=$MaxDrawdownPct&top_n=15" 900
    if ($null -eq $res) { Bad "the search did not return" }
    elseif (-not $res.ok) { Bad "search abstained: $($res.reason) $($res.detail)" }
} else {
    Skip "-Search not set, so nothing was measured this run"
    Write-Host "        This is NOT a pass. Sections 1-3 need a live search." -ForegroundColor DarkGray
}

if ($null -ne $res -and $res.ok) {
    $ranked = @($res.ranked)
    Ok "measured $($res.coverage.measured) of $($res.coverage.candidates_offered) candidates"

    if ($res.coverage.excluded -gt 0) {
        Write-Host "        excluded, each with a reason:" -ForegroundColor DarkGray
        foreach ($e in @($res.excluded)) {
            Write-Host "          $($e.id): $($e.reason) - $($e.detail)" -ForegroundColor DarkGray
        }
    }
    if ($res.coverage.incomplete_reason) {
        Bad "the search was incomplete: $($res.coverage.incomplete_reason)"
        Write-Host "        Rate limit or open circuit, NOT a statement about those tickers." -ForegroundColor DarkGray
    }

    # Every row, not just the winner. This is the whole point of the window
    # decision: the short window understates the 2022 rate shock ~6x.
    $noRef = @($ranked | Where-Object { $null -eq $_.reference -and $_.flags -notcontains 'REFERENCE_NOT_MEASURED' })
    if ($noRef.Count -gt 0) {
        Bad "$($noRef.Count) row(s) have no ten-year figure and do not say why"
    } else { Ok "every ranked row carries the ten-year window or names why it cannot" }

    # The payload must say which window sorted it.
    if ($res.ranked_on.window) { Ok "ranked_on: $($res.ranked_on.window) / $($res.ranked_on.metric)" }
    else { Bad "the payload does not say which window produced the order" }

    # investing-discipline SS5.
    if ("$($res.disclaimer)" -match 'Not financial advice') { Ok "the disclaimer ships with the payload" }
    else { Bad "no disclaimer on the payload" }

    # And there must be NO apply path.
    if ($null -ne $res.execution_plan) {
        Bad "the search returned an execution_plan - this route must never have one"
    } else { Ok "no execution path from the search" }

    Write-Host ""
    Write-Host "        candidate                    ex     dd  |  dd10y   flags" -ForegroundColor DarkGray
    foreach ($r in $ranked) {
        $mark = if ($r.admissible) { "OK " } else { "no " }
        $rd = if ($null -ne $r.reference) { "{0,6:N2}" -f $r.reference.max_drawdown_pct } else { "     -" }
        $fmt = "        {0}{1,-26} {2,6:N2} {3,6:N2}  | {4}   {5}"
        $line = $fmt -f $mark, $r.name, $r.excess_pct, $r.max_drawdown_pct, $rd, ($r.flags -join ',')
        Write-Host $line -ForegroundColor DarkGray
    }
}

# =========================================================================== #
Sec "2  a search that finds nothing admissible SAYS SO"
# =========================================================================== #
if ($null -eq $res -or -not $res.ok) {
    Skip "no search result to read"
} else {
    $v = $res.verdict
    if ($null -eq $v -or -not $v.kind) {
        Bad "no verdict on the payload - a ranked table with no verdict reads as a menu"
    } else {
        Ok "verdict: $($v.kind)"
        $adm = @(@($res.ranked) | Where-Object { $_.admissible })
        if ($v.kind -eq 'NONE_ADMISSIBLE') {
            if ($adm.Count -gt 0) {
                Bad "verdict says NONE_ADMISSIBLE but $($adm.Count) row(s) are marked admissible"
            } else { Ok "and no row claims to be admissible" }
            if (-not $v.headline -or -not $v.means) {
                Bad "NONE_ADMISSIBLE with no headline or explanation"
            } else {
                Ok "it names the cause, not just the absence"
                Write-Host "        $($v.headline)" -ForegroundColor DarkGray
                Write-Host "        $($v.means)" -ForegroundColor DarkGray
            }
        } elseif ($v.kind -eq 'RANKED') {
            if ($adm.Count -eq 0) { Bad "verdict says RANKED but nothing is admissible" }
            else { Ok "$($adm.Count) admissible candidate(s)" }
            if ($null -ne $res.spread -and -not $res.spread.above_noise_floor) {
                Write-Host "        NOTE: best beats median by $($res.spread.best_minus_median_pct) pts" -ForegroundColor DarkYellow
                Write-Host "        - under the noise floor, so the ordering is not a finding." -ForegroundColor DarkYellow
            }
        }
    }
}

# =========================================================================== #
Sec "3  the Today card stops giving advice the search refuted"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations' 600
if ($null -eq $recs) {
    Skip "/api/v1/recommendations unreachable"
} else {
    $cd = @(@($recs.recommendations) | Where-Object { $_.meta.source -eq 'target_solver' })[0]
    if ($null -eq $cd) {
        Skip "no core-ceiling card on Today (expected only when the core breaches at zero sleeves)"
    } else {
        $cs = $cd.meta.core_search
        if ($null -eq $cs) {
            Bad "the card does not read the core search at all - this deploy predates T6b"
        } elseif (-not $cs.known) {
            Skip "no core search on record yet; the card keeps its original wording"
            if ("$($cd.action)" -notmatch 'Change the strategy managing your core') {
                Bad "the card dropped its original wording without a search to justify it"
            } else { Ok "original wording retained, correctly" }
        } elseif ($cs.none_admissible) {
            if ("$($cd.action)" -match 'Change the strategy managing your core') {
                Bad "the card still says 'change the core' after the search refuted it"
            } else { Ok "the card no longer recommends changing the core" }
            if ("$($cd.how)" -match 'sleeve') { Ok "it names the sleeves as the cause" }
            else { Bad "it does not say where the drawdown actually comes from" }
            if ($null -eq $cs.age_hours) { Bad "the core search cannot say how old it is" }
            else { Ok "the core search's age travels with it ($($cs.age_hours)h)" }
        } else {
            Ok "core search is $($cs.kind); the card keeps recommending a core change"
        }
    }
}

# =========================================================================== #
Sec "4  the nightly job runs both searches"
# =========================================================================== #
$push = Api GET '/api/v1/push/status' 120
if ($null -eq $push) {
    Skip "/api/v1/push/status unreachable"
} else {
    $txt = ($push | ConvertTo-Json -Depth 8)
    if ($txt -notmatch 'solver_refresh') {
        Skip "solver_refresh not in job state yet (it fires at 04:10 UTC)"
        Write-Host "        This is NOT a pass. Check again after the next 04:10." -ForegroundColor DarkGray
    } else {
        Ok "solver_refresh registered"
        $st = $push.history.solver_refresh
        if ($null -ne $st -and $st.last_ok -eq $false) {
            Bad "solver_refresh last run FAILED: $($st.last_error)"
        } elseif ($null -ne $st -and $null -ne $st.last_result) {
            if ($null -eq $st.last_result.cores) {
                Bad "the job ran but reported no 'cores' count - it is not running the core search"
            } else { Ok "last run searched $($st.last_result.cores) core(s)" }
        } else {
            Skip "registered but not yet run"
        }
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
