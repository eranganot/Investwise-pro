# SMOKE - PR 12, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-core-card-and-price-refresh.ps1
#   .\scripts\smoke\smoke-core-card-and-price-refresh.ps1 -Sha 1234abc
#
# Three claims, all false before this deploy:
#
#   1. The price refresh reports a failure as a failure. It crashed on every
#      boot with "asyncio.run() cannot be called from a running event loop" and
#      then returned {"updated": 0, "failed": 0} - the same two numbers a clean
#      run produces. It was also outside _guarded, so JOB_STATE never heard.
#   2. The solver's structural finding has a card. floor_breaches_ceiling means
#      the core alone breaches the ceiling and NO sleeve size repairs it. On the
#      live book that was 35.1% against 13.3% and Today said nothing.
#   3. The rebalance card scales with the drift it measured, instead of being
#      the literal string "MEDIUM" on a book 20.4 points out of target.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

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
Write-Host "InvestWise Pro - the job that lied and the card that was missing" -ForegroundColor White
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
Sec "1  the price refresh ran, and its outcome is recorded"
# =========================================================================== #
# The prime now goes through _guarded via next_run_time, so a booted app must
# have a price_refresh entry. Before this deploy there was none, ever - the
# prime was unguarded and the 30-minute job had not fired yet on a fresh boot.
$push = Api GET '/api/v1/push/status' 120
if ($null -eq $push) {
    Skip "/api/v1/push/status unreachable - cannot see job state"
} else {
    $txt = ($push | ConvertTo-Json -Depth 8)
    if ($txt -notmatch 'price_refresh') {
        Bad "price_refresh is absent from job state - the prime is still not guarded"
    } else {
        Ok "price_refresh appears in job state"
        $st = $push.history.price_refresh
        if ($null -eq $st) {
            Skip "job state has the name but no run record yet"
        } elseif ($st.last_ok -eq $true) {
            Ok "last run OK ($($st.runs) run(s), $($st.failures) failure(s))"
            # The old bug's signature: a "successful" run that repriced nothing.
            # Not a FAIL on its own - a book with no holdings legitimately
            # updates zero - but it is the number that hid the crash.
            $res = $st.last_result
            if ($null -ne $res -and $res.updated -eq 0 -and $res.failed -eq 0) {
                Write-Host "        NOTE: updated=0 failed=0. Clean if you hold nothing;" -ForegroundColor DarkYellow
                Write-Host "        this is exactly the shape the crash used to return." -ForegroundColor DarkYellow
            } elseif ($null -ne $res) {
                Write-Host "        updated=$($res.updated) failed=$($res.failed)" -ForegroundColor DarkGray
            }
        } else {
            Bad "price_refresh last run FAILED: $($st.last_error)"
            Write-Host "        This is the fix WORKING if the error is real - the old" -ForegroundColor DarkGray
            Write-Host "        code would have shown last_ok true with nothing done." -ForegroundColor DarkGray
        }
    }
}

# =========================================================================== #
Sec "2  the solver's structural finding is on Today"
# =========================================================================== #
$wr = Api GET '/api/v1/war-room' 600
$recs = Api GET '/api/v1/recommendations' 600
if ($null -eq $wr -or $null -eq $recs) {
    Skip "war-room or recommendations unreachable"
} else {
    $c = $wr.solver_constraint
    $cards = @(@($recs.recommendations) | Where-Object { $_.meta.source -eq 'target_solver' })
    if ($null -eq $c -or -not $c.known) {
        Skip "no solve on record - run smoke-t5-solver-constraint.ps1 -Solve first"
        Write-Host "        This is NOT a pass. Without a verdict there is nothing to show." -ForegroundColor DarkGray
        if ($cards.Count -gt 0) {
            Bad "$($cards.Count) solver card(s) with NO verdict on record - invented finding"
        }
    } elseif (-not $c.floor_breaches_ceiling) {
        Skip "the stored verdict does not breach at zero sleeves, so no card is expected"
        if ($cards.Count -gt 0) {
            Bad "a core-breach card is showing while floor_breaches_ceiling is false"
        }
    } else {
        # The verdict says the core alone breaches. There MUST be a card.
        if ($cards.Count -eq 0) {
            Bad "core breaches the ceiling and Today says nothing - this is the bug"
        } else {
            $cd = $cards[0]
            Ok "core-breach card present: '$($cd.title)'"
            if ("$($cd.severity)" -ne 'CRITICAL') {
                Bad "severity is '$($cd.severity)', not CRITICAL"
            } else { Ok "severity CRITICAL" }

            # Advisory, permanently. Swapping a core is a whole-book change.
            if ("$($cd.apply.kind)" -ne 'none') {
                Bad "the card is one-tap executable ($($cd.apply.kind)) - it must never be"
            } else { Ok "advisory only, no one-tap" }

            # The age has to travel with the answer.
            if ($null -eq $cd.meta.age_hours) {
                Bad "the card cannot say how old the verdict is"
            } else { Ok "carries the verdict's age ($($cd.meta.age_hours)h)" }

            # The numbers, not just the prose.
            if ("$($cd.why)" -match '\d+\.\d+%') { Ok "the measured figures are on the card" }
            else { Bad "no measured percentage on the card" }
        }
    }
}

# =========================================================================== #
Sec "3  the rebalance card scales with the drift it measured"
# =========================================================================== #
if ($null -eq $recs) {
    Skip "/api/v1/recommendations unreachable"
} else {
    $rb = @(@($recs.recommendations) | Where-Object {
        "$($_.apply.kind)" -eq 'rebalance_to_objective' })
    if ($rb.Count -eq 0) {
        Skip "no rebalance card right now, so there is no severity to check"
    } else {
        foreach ($r in $rb) {
            Write-Host "        '$($r.title)' -> $($r.severity)" -ForegroundColor DarkGray
        }
        # The book that prompted this is 20.4 points out of its Equities target,
        # which is 6.8x the 3% trigger. If a card is drifted that far and still
        # says MEDIUM, the scale is not wired to the card.
        $bt = $recs.book_target
        if ($null -eq $bt) {
            Skip "no book_target on the payload, cannot compute the true drift"
        } else {
            $worst = 0.0
            foreach ($p in $bt.target.PSObject.Properties) {
                $cur = 0.0
                if ($null -ne $recs.market -and $null -ne $recs.market.mix) {
                    $cur = [double]($recs.market.mix.$($p.Name))
                }
                $d = [math]::Abs([double]$p.Value - $cur)
                if ($d -gt $worst) { $worst = $d }
            }
            $x = if ($worst -gt 0) { $worst / 0.03 } else { 0 }
            Write-Host "        worst class drift ~$([math]::Round($worst*100,1)) pts (~$([math]::Round($x,1))x the 3% trigger)" -ForegroundColor DarkGray
            $top = ($rb | ForEach-Object { $_.severity }) -join ','
            if ($x -ge 6.0 -and $top -notmatch 'CRITICAL') {
                Bad "drift is ${x}x the trigger but no rebalance card is CRITICAL"
            } elseif ($x -ge 3.0 -and $top -match 'MEDIUM' -and $top -notmatch 'HIGH|CRITICAL') {
                Bad "drift is past 3x the trigger and every card still says MEDIUM"
            } else {
                Ok "severity is consistent with the measured drift"
            }
        }
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
