# SMOKE - PR 13b / T6b second half, against the LIVE deploy.
#
#   .\scripts\smoke\smoke-t6b-sleeve-count.ps1
#   .\scripts\smoke\smoke-t6b-sleeve-count.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-t6b-sleeve-count.ps1 -Search
#
# -Search is opt-in: one simulation per candidate set over ten years of daily
# closes. It writes nothing and there is no apply path from this route.
#
# Three claims, all false before this deploy:
#
#   1. The sleeve COUNT can be measured. T2 resizes a fixed set and T6a sizes
#      each member of that fixed set; neither can answer "should there be a
#      third at all", and this book runs two for no measured reason.
#   2. The search says WHERE it stopped and BY HOW MUCH the last one helped.
#      A count with no stop reason renders convergence and a budget limit
#      identically.
#   3. A baseline that already breaches the ceiling is its own outcome. Found
#      by running this against the real book: every candidate third breached,
#      so "no admissible addition" was technically true and read as "two is
#      right" when the two already held breach by 31 points.
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
Write-Host "InvestWise Pro - how many sleeves earn their place" -ForegroundColor White
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

Sec "1  the count is measured, and the search says how it ended"
$res = $null
if ($Search) {
    Write-Host "        searching against a $MaxDrawdownPct% ceiling" -ForegroundColor DarkGray
    $res = Api GET "/api/v1/plan/target/sleeve-count?max_drawdown_pct=$MaxDrawdownPct" 900
} else {
    Skip "-Search not set, so nothing was measured this run"
    Write-Host "        This is NOT a pass. Sections 1-3 need a live search." -ForegroundColor DarkGray
}

if ($null -ne $res) {
    if (-not $res.ok) {
        if ("$($res.stopped_because)" -eq 'NOTHING_MEASURABLE') {
            Bad "the starting set could not be measured: $($res.detail)"
        } else {
            Bad "search abstained: $($res.reason) $($res.detail)"
        }
    } else {
        Ok "count = $($res.count): $((@($res.chosen)) -join ', ')"
        if (-not $res.stopped_because) {
            Bad "no stop reason - convergence and a budget limit read identically"
        } else { Ok "stopped_because: $($res.stopped_because)" }

        # Convergence and a floor are different claims and both must be present.
        if ($null -eq $res.converged -or $null -eq $res.count_is_a_floor) {
            Bad "the payload cannot say whether the count is a result or a floor"
        } else {
            Ok "converged=$($res.converged)  count_is_a_floor=$($res.count_is_a_floor)"
            if ($res.converged -and $res.count_is_a_floor) {
                Bad "converged AND a floor - those cannot both be true"
            }
        }

        # It must not claim to be exhaustive.
        if ("$($res.method.kind)" -ne 'greedy_forward_selection') {
            Bad "the payload does not name its method"
        } elseif (-not $res.method.caveat) {
            Bad "greedy with no caveat - 'discovered' would imply exhaustive"
        } else { Ok "method named, with its caveat" }

        if ("$($res.disclaimer)" -match 'Not financial advice') { Ok "the disclaimer ships with the payload" }
        else { Bad "no disclaimer on the payload" }

        if ($null -ne $res.execution_plan) {
            Bad "the search returned an execution_plan - this route must never have one"
        } else { Ok "no execution path from the search" }
    }
}

Sec "2  every round reports what it rejected"
if ($null -eq $res -or -not $res.ok) {
    Skip "no search result to read"
} elseif ("$($res.stopped_because)" -eq 'BASELINE_BREACHES_CEILING') {
    Skip "the baseline already breaches, so no round was run - correctly"
    Write-Host "        $($res.verdict.headline)" -ForegroundColor DarkGray
} else {
    $rounds = @($res.rounds)
    if ($rounds.Count -eq 0) {
        Bad "no rounds recorded - 'it stopped at N' cannot be checked"
    } else {
        Ok "$($rounds.Count) round(s) recorded"
        $empty = @($rounds | Where-Object { @($_.tried).Count -eq 0 })
        if ($empty.Count -gt 0) { Bad "$($empty.Count) round(s) recorded no candidates" }
        else { Ok "every round lists the candidates it tried" }
        foreach ($r in $rounds) {
            $what = if ($r.added) { "added $($r.added)" } else { "rejected $($r.best_rejected)" }
            Write-Host "        round $($r.round): $what  gain=$($r.gain_pct)" -ForegroundColor DarkGray
        }
        # The stated requirement: by how much the last one helped.
        if ("$($res.stopped_because)" -eq 'BELOW_NOISE_FLOOR' -and $null -eq $res.last_gain_pct) {
            Bad "converged but does not say by how much the last addition helped"
        }
    }
}

Sec "3  a breaching baseline refuses to be read as a recommendation"
if ($null -eq $res -or -not $res.ok) {
    Skip "no search result to read"
} else {
    $v = $res.verdict
    if ($null -eq $v -or -not $v.kind) {
        Bad "no verdict on the payload - a bare count cannot say what it means"
    } else {
        Ok "verdict: $($v.kind)"
        Write-Host "        $($v.headline)" -ForegroundColor DarkGray
        if ($v.baseline_breaches_ceiling) {
            if ("$($v.means)" -notmatch 'not a recommendation') {
                Bad "the baseline breaches and the verdict does not disown the count"
            } else { Ok "the count is explicitly not a recommendation" }
            if ("$($res.stopped_because)" -ne 'BASELINE_BREACHES_CEILING') {
                Bad "baseline breaches but stopped_because is '$($res.stopped_because)'"
            }
        } else {
            Ok "the held set is admissible"
        }
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
