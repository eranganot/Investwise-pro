# SMOKE - PR 7b + PR 8, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-core-picker.ps1
#   .\scripts\smoke\smoke-core-picker.ps1 -Sha 8679a06
#
# Two claims, and only production can settle either of them:
#
#   PR 8  - plans.strategy / plans.strategy_sleeve_pct are GONE from THIS
#           database. alembic never runs against production; the ALTER list in
#           main.py is the mechanism, so "the migration exists" proves nothing.
#           And the sleeves must have SURVIVED the drop.
#   PR 7b - every core family carries its goal group, and the group actually
#           came from the catalogue rather than being invented on the screen.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; read paired state in ONE
# call; print the exception TYPE on first failure; $ApiHeaders, never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }

function Api($method, $path) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec 120
    } catch {
        Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - core picker + legacy column drop" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# =========================================================================== #
Sec "0  the deploy actually landed"
# =========================================================================== #
$health = Api GET '/health'
if ($null -eq $health) {
    Bad "/health unreachable - nothing below can be trusted"
} elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest of this run describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

# =========================================================================== #
Sec "1  PR 8 - the second answer is gone from THIS database"
# =========================================================================== #
$plan = Api GET '/api/v1/plan'
if ($null -eq $plan) {
    Bad "/api/v1/plan unreachable - cannot check the columns"
} else {
    $stale = @($plan.PSObject.Properties.Name | Where-Object { $_ -in @('strategy', 'strategy_sleeve_pct') })
    if ($stale.Count -eq 0) { Ok "/plan returns no strategy pointer" }
    else { Bad "/plan still returns: $($stale -join ', ') - the drop did not reach this deploy" }
}

# Paired with the above in ONE read: the sleeves and the core together, because
# "the columns are gone" and "the book still knows what it runs" is one claim.
$sl = Api GET '/api/v1/plan/sleeves'
if ($null -eq $sl) {
    Bad "/api/v1/plan/sleeves unreachable - cannot confirm the sleeves survived"
} else {
    if ($sl.PSObject.Properties.Name -contains 'legacy') {
        Bad "/plan/sleeves still carries a 'legacy' block"
    } else { Ok "no legacy block on /plan/sleeves" }

    $rows = @($sl.sleeves)
    if ($rows.Count -eq 0) {
        Bad "the book runs NO sleeves - if it ran some before this deploy, the drop took them"
    } else {
        Ok "sleeves intact: $((@($rows | ForEach-Object { "$($_.strategy_id) $($_.sleeve_pct)%" })) -join ', ')"
    }

    # A sleeve target of 0 would be the signature of a size read from a column
    # that no longer exists.
    $zero = @($rows | Where-Object { [double]$_.sleeve_pct -le 0 })
    if ($zero.Count -gt 0) { Bad "$($zero.Count) sleeve(s) sized at 0% - a size was lost" }
    elseif ($rows.Count -gt 0) { Ok "every sleeve still has a non-zero target" }

    $cores = @($rows | Where-Object { $_.is_core -eq $true })
    if ($cores.Count -gt 1) { Bad "$($cores.Count) core rows - get_core answers oldest-first and would ignore your newer choice" }
    else { Ok "at most one core row" }
}

# =========================================================================== #
Sec "2  PR 7b - every family says what it is FOR"
# =========================================================================== #
$opts = Api GET '/api/v1/plan/core/options'
if ($null -eq $opts) {
    Bad "/api/v1/plan/core/options unreachable"
} else {
    $all = @($opts.options)
    $missing = @($all | Where-Object { -not $_.goal })
    if ($missing.Count -gt 0) {
        Bad "$($missing.Count) family/families carry no goal group: $((@($missing | ForEach-Object { $_.strategy_id })) -join ', ')"
    } else { Ok "all $($all.Count) families carry a goal group" }

    $groups = @($all | ForEach-Object { $_.goal } | Sort-Object -Unique)
    $want = @('Balanced', 'Grow', 'Income', 'Preserve')
    if (@(Compare-Object $groups $want -SyncWindow 0).Count -eq 0) {
        Ok "the four groups are all represented: $($groups -join ', ')"
    } else {
        Bad "groups are '$($groups -join ', ')', expected '$($want -join ', ')'"
    }

    # The chip must not be decoration: a Preserve family cannot be modelled as
    # falling further than a Grow one, or the word is worse than no word.
    $pre = @($all | Where-Object { $_.goal -eq 'Preserve' -and $null -ne $_.est_max_drawdown_pct })
    $grw = @($all | Where-Object { $_.goal -eq 'Grow' -and $null -ne $_.est_max_drawdown_pct })
    if ($pre.Count -eq 0 -or $grw.Count -eq 0) {
        Skip "not enough drawdown figures to check the groups mean anything"
    } else {
        $worstPre = ($pre | Measure-Object est_max_drawdown_pct -Maximum).Maximum
        $bestGrw = ($grw | Measure-Object est_max_drawdown_pct -Minimum).Minimum
        if ($worstPre -le $bestGrw) {
            Ok "deepest Preserve fall ($worstPre%) is no worse than the shallowest Grow ($bestGrw%)"
        } else {
            Bad "a Preserve family models a $worstPre% fall against a Grow family's $bestGrw% - the chip is misleading"
        }
    }

    # Estimated and measured must stay separable. This is the invariant PR 7
    # shipped and the reason the DDL fix mattered at all.
    $blurred = @($all | Where-Object {
        ($_.drawdown_source -eq 'estimate' -and $null -ne $_.measured_max_drawdown_pct) -or
        ($_.drawdown_source -eq 'measured' -and $null -eq $_.measured_max_drawdown_pct) })
    if ($blurred.Count -gt 0) { Bad "$($blurred.Count) row(s) blur estimated and measured drawdown" }
    else { Ok "estimated and measured drawdown stay in separate fields" }

    $meas = @($all | Where-Object { $_.drawdown_source -eq 'measured' })
    Write-Host "        $($meas.Count)/$($all.Count) families have a MEASURED drawdown" -ForegroundColor DarkGray
    if ($meas.Count -eq 0) {
        Write-Host "        all estimates - the backtest refresh runs 03:30 UTC, so this" -ForegroundColor DarkGray
        Write-Host "        is expected on the first day after the benchmark_ticker fix." -ForegroundColor DarkGray
    }
    if ($opts.store_error) {
        Bad "the backtest store still cannot be read: $($opts.store_error)"
    } else { Ok "the backtest store is readable" }

    # Exactly one row may claim to be the current core, and it has to agree with
    # /plan/sleeves - two screens quoting different cores is the whole disease.
    $cur = @($all | Where-Object { $_.is_current -eq $true })
    $coreFromSleeves = if ($sl -and $sl.core) { "$($sl.core.strategy_id)" } else { "" }
    if ($cur.Count -gt 1) { Bad "$($cur.Count) families claim to be the current core" }
    elseif ($cur.Count -eq 1 -and "$($cur[0].strategy_id)" -ne $coreFromSleeves) {
        Bad "/core/options says '$($cur[0].strategy_id)' but /plan/sleeves says '$coreFromSleeves'"
    } elseif ($cur.Count -eq 1) {
        Ok "both routes name the same core: $coreFromSleeves ($($cur[0].goal))"
    } elseif (-not $coreFromSleeves) {
        Ok "no core chosen, and no row claims to be one"
    } else {
        Bad "/plan/sleeves says the core is '$coreFromSleeves' but no option row is marked current"
    }

    # A rule-based sleeve must never be offered as a core.
    $leaked = @($all | Where-Object { $_.strategy_id -like 'btm_*' })
    if ($leaked.Count -gt 0) { Bad "rule-based sleeve(s) offered as a core: $((@($leaked | ForEach-Object { $_.strategy_id })) -join ', ')" }
    else { Ok "no rule-based sleeve is offered as a core" }
}

# =========================================================================== #
Sec "3  the page can actually draw it"
# =========================================================================== #
try {
    $html = Invoke-WebRequest -Uri "$BaseUrl/" -Headers $ApiHeaders -TimeoutSec 60
    $body = "$($html.Content)"
    if ($body -match 'chip\(x\.goal\)') { Ok "the served page renders the goal chip" }
    else { Bad "the served page has no goal chip - an older index.html is deployed" }
    if ($body -match 'function toggleCorePicker\(\)') { Ok "the Change-core door is on the served page" }
    else { Bad "no toggleCorePicker on the served page" }
    if ($body -match '_appliedIds' -and $body -match 'legacy') {
        Skip "index.html still mentions 'legacy' somewhere - worth an eyeball, not a failure"
    }
} catch {
    Bad "could not fetch the app shell: $($_.Exception.GetType().Name)"
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
