# SMOKE - PR 11 / T5, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-t5-solver-constraint.ps1
#   .\scripts\smoke\smoke-t5-solver-constraint.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-t5-solver-constraint.ps1 -Solve      # writes a verdict
#
# Two claims, both false before this deploy:
#
#   1. The Adversary can veto on the path that produces the cards people see.
#      adversary_enforce_veto has defaulted to True since it was added and was
#      honoured in exactly one function, reachable only from /decision-feed.
#      On the war-room path a BLOCK finding was a sentence in a transcript.
#   2. The war room consults the solver's verdict. Nothing stored one, so
#      nothing could - and a solve is far too expensive to run inline on the
#      slowest agent in the app.
#
# -Solve is opt-in because GET /plan/target is a real solve: a coarse-then-fine
# grid of ten-year simulations. It writes a verdict row; it changes no holdings.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$Solve,
    [double]$ExcessPct = 2.0,
    # NO DEFAULT. 13.3 used to be hardcoded here, and it was wrong: the ceiling
    # is the user's own number, seeded once from their book and then frozen into
    # the stored verdict. A smoke script that supplies its own would keep feeding
    # a stale figure back into production - which is exactly how 13.3 survived on
    # this book long after the measurement that produced it read 58.59%.
    # 0 means "use whatever ceiling the stored verdict already carries".
    [double]$MaxDrawdownPct = 0)

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
Write-Host "InvestWise Pro - the war room reads the solver" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# The ceiling is the USER'S number, not this script's. When -MaxDrawdownPct is
# not supplied it is read from the stored verdict, so a smoke run can never
# quietly re-assert a figure the user has since changed.
function Resolve-Ceiling($current) {
    if ($current -gt 0) { return $current }
    $wr = Api GET '/api/v1/war-room' 600
    $c = $null
    if ($null -ne $wr) { $c = $wr.solver_constraint }
    if ($null -ne $c -and $c.known -and $c.ceiling_pct -gt 0) {
        Write-Host "        using the stored ceiling: $($c.ceiling_pct)%" -ForegroundColor DarkGray
        return [double]$c.ceiling_pct
    }
    return 0
}


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
Sec "1  optionally re-solve, so there IS a verdict to read"
# =========================================================================== #
if ($Solve) {
    Write-Host "        solving (excess $ExcessPct%, ceiling $MaxDrawdownPct%) - this is slow" -ForegroundColor DarkGray
    $MaxDrawdownPct = Resolve-Ceiling $MaxDrawdownPct
    if ($MaxDrawdownPct -le 0) {
        Skip "no ceiling supplied and none on record - pass -MaxDrawdownPct to solve"
        $sv = $null
    } else {
    $sv = Api GET "/api/v1/plan/target?excess_pct=$ExcessPct&max_drawdown_pct=$MaxDrawdownPct" 600
    if ($null -eq $sv) { Bad "the solve did not return" }
    else { Ok "solver answered: $($sv.outcome)" }
    }
} else {
    Skip "-Solve not set, so this reads whatever verdict is already stored"
}

# =========================================================================== #
Sec "2  the war room carries a solver constraint, and it knows its own age"
# =========================================================================== #
$wr = Api GET '/api/v1/war-room' 600
if ($null -eq $wr) {
    Bad "/api/v1/war-room unreachable - nothing below can run"
} else {
    $c = $wr.solver_constraint
    if ($null -eq $c) {
        Bad "no solver_constraint on the payload - this deploy predates T5"
    } elseif (-not $c.known) {
        Skip "no solve on record for this book yet ($($c.reason))"
        Write-Host "        This is NOT a pass. Re-run with -Solve to store one." -ForegroundColor DarkGray
    } else {
        Ok "constraint present: $($c.outcome), $($c.age_hours)h old, blocks=$($c.blocks)"
        # A verdict allowed to disable a button must be able to say how old it is.
        if ($null -eq $c.age_hours) { Bad "the constraint cannot say how old it is" }
        else { Ok "the age travels with the answer" }

        # Stale must SPEAK and not BLOCK.
        if ($c.stale -and $c.blocks) {
            Bad "a stale verdict is still blocking - it should keep its finding and lose its teeth"
        } elseif ($c.stale) {
            Ok "stale verdict reported and not enforced"
        }

        # Only the two outcomes that mean "you cannot get there" may block.
        if ($c.blocks -and $c.outcome -notin @('UNREACHABLE', 'DRAWDOWN_BOUND')) {
            Bad "'$($c.outcome)' is blocking and should not be"
        } elseif (-not $c.blocks -and $c.outcome -in @('UNREACHABLE', 'DRAWDOWN_BOUND') -and -not $c.stale) {
            Bad "'$($c.outcome)' is fresh and should be blocking"
        } else {
            Ok "the blocking rule matches the outcome"
        }
    }
}

# =========================================================================== #
Sec "3  the constraint reaches the transcripts, biting or not"
# =========================================================================== #
if ($null -eq $wr -or $null -eq $wr.sessions) {
    Skip "no war-room sessions"
} else {
    $shown = @(@($wr.sessions) | Where-Object { $_.outcome -eq 'DISPLAYED' })
    if ($shown.Count -eq 0) {
        Skip "no DISPLAYED sessions right now, so there is nothing to constrain"
    } elseif (-not $wr.solver_constraint.known) {
        Skip "no verdict on record, so no constraint line is expected"
    } else {
        $withLine = @($shown | Where-Object {
            @($_.transcript | Where-Object { "$($_.role)" -like '*Target solver*' }).Count -gt 0 })
        if ($withLine.Count -eq $shown.Count) {
            Ok "all $($shown.Count) shown session(s) record that the solver was consulted"
        } else {
            Bad "$($shown.Count - $withLine.Count) shown session(s) never mention the solver"
        }

        # De-risking must never be blocked.
        $wrongly = @($shown | Where-Object {
            $_.blocked_by -and "$($_.action_type)".ToUpper() -ne 'BUY' })
        if ($wrongly.Count -eq 0) { Ok "no de-risking signal was blocked" }
        else { Bad "$($wrongly.Count) non-BUY signal(s) blocked - that turns a safety feature into a trap" }

        $blocked = @($shown | Where-Object { $_.blocked_by })
        if ($blocked.Count -gt 0) {
            Ok "$($blocked.Count) risk-adding session(s) marked blocked, and still SHOWN"
            foreach ($b in $blocked) {
                Write-Host "        $($b.ticker): $($b.blocked_by.headline)" -ForegroundColor DarkGray
            }
        }
    }
}

# =========================================================================== #
Sec "4  a blocked card keeps its explanation and loses its one tap"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations' 600
if ($null -eq $recs) {
    Skip "/api/v1/recommendations unreachable"
} else {
    $cards = @(@($recs.recommendations) | Where-Object { $_.blocked_by })
    if ($cards.Count -eq 0) {
        Skip "no blocked card on Today right now"
        Write-Host "        Expected whenever the stored verdict does not block, or when" -ForegroundColor DarkGray
        Write-Host "        no risk-adding war-room signal is firing." -ForegroundColor DarkGray
    } else {
        foreach ($cd in $cards) {
            if ("$($cd.apply.kind)" -ne 'none') {
                Bad "'$($cd.title)' is blocked but still one-tap executable ($($cd.apply.kind))"
            } elseif ($cd.actionable -eq $true) {
                Bad "'$($cd.title)' is blocked but still reports actionable"
            } elseif (@($cd.notes).Count -eq 0) {
                Bad "'$($cd.title)' is blocked and says nothing about why"
            } else {
                Ok "'$($cd.title)': one-tap removed, reason shown"
                Write-Host "        $($cd.notes[0])" -ForegroundColor DarkGray
            }
        }
    }
}

# =========================================================================== #
Sec "5  the nightly re-solve is registered"
# =========================================================================== #
$push = Api GET '/api/v1/push/status' 120
if ($null -eq $push) {
    Skip "/api/v1/push/status unreachable - cannot see job state"
} else {
    $names = @()
    foreach ($p in $push.PSObject.Properties) { $names += $p.Name }
    $txt = ($push | ConvertTo-Json -Depth 6)
    if ($txt -match 'solver_refresh') {
        Ok "solver_refresh appears in job state"
        if ($txt -match '"solver_refresh"[^}]*"last_ok"\s*:\s*false') {
            Bad "solver_refresh has run and FAILED - check the logs"
        }
    } else {
        Skip "solver_refresh not in job state yet (it first fires at 04:10 UTC)"
        Write-Host "        This is NOT a pass. Check again after the next 04:10." -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
