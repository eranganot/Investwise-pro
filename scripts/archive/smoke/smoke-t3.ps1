# SMOKE - T0..T3: the target solver chain, against the deployed app.
#
#   .\scripts\smoke\smoke-t3.ps1                 # read-only, then chains beat-market
#   .\scripts\smoke\smoke-t3.ps1 -SkipChain      # T0..T3 checks only
#   .\scripts\smoke\smoke-t3.ps1 -SkipShaCheck
#
# ENTIRELY READ-ONLY. Every endpoint touched here is a GET, or the POST that
# only computes (/portfolio/performance). The solver is the point of the phase
# and it must not be able to move the book, so this script PROVES that rather
# than trusting it: sleeves, positions and cash are snapshotted before and after
# a solve and compared.
#
# Covers all four phases shipped so far, because T0 through T2 shipped without
# smokes of their own:
#   T0  two benchmarks, a window on every figure, staleness on a benchmark change
#   T1  the blend fields that only a simulated blend can produce
#   T2  the five outcomes, the constraints binding, read-only
#   T3  drawdown in shekels, median beside mean, the tax
#
# Rules carried over, each learned the hard way:
#   * A SKIP is never a PASS.
#   * Never assert a guess - read the app's own caps and figures, never assume.
#   * Read paired state in one breath, so two checks cannot disagree about it.
#   * Print the failing path and the HTTP code on the FIRST failure.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$SkipChain,
    [double]$Excess = 5.0,
    [double]$Ceiling = 30.0,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'

if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor Gray
    exit 1
}
$ApiHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY }

$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $tmo = 180) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

function Status($method, $path) {
    try {
        Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec 60 | Out-Null
        return 200
    } catch {
        try { return $_.Exception.Response.StatusCode.value__ } catch { return -1 }
    }
}

function Fingerprint($sleeves, $portfolio) {
    # One string that changes if ANYTHING the solver could have touched moved.
    $s = ($sleeves.sleeves | Sort-Object strategy_id |
          ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ';'
    $p = ($portfolio.positions | Sort-Object ticker |
          ForEach-Object { "$($_.ticker)=$($_.quantity)" }) -join ';'
    return "$s|$p|$($portfolio.cash_ils)|$($portfolio.nav)"
}

Write-Host "Smoke: T0..T3 target solver  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "T.0  am I talking to the new container?"
# =========================================================================== #
if ($SkipShaCheck) { Skip "SHA check skipped" }
else {
    if (-not $Sha) { try { $Sha = (git rev-parse --short HEAD).Trim() } catch { $Sha = '' } }
    $h = Api GET '/health' 60
    if ($null -eq $h) { Bad "/health unreachable - stopping"; exit 1 }
    $live = "$($h.commit)"
    if (-not $live -or $live -eq 'unknown') { Bad "/health reports no commit" }
    elseif (-not $Sha) { Skip "no local SHA to compare; deployed is $live" }
    elseif ($live.StartsWith($Sha) -or $Sha.StartsWith($live)) { Ok "deployed commit is $live" }
    else { Bad "deployed is $live, you are smoking $Sha"; exit 1 }
}

# =========================================================================== #
Sec "T0.1  the performance card carries its window, its currency and both bases"
# =========================================================================== #
$perf = Api POST '/api/v1/portfolio/performance'
if ($null -eq $perf) { Bad "/portfolio/performance unreachable" }
elseif (-not $perf.ok) { Skip "performance says: $($perf.reason)" }
else {
    if ($perf.window -and $perf.window.kind -eq 'holdings_backfill' -and $perf.window.start) {
        Ok "window is $($perf.window.start)..$($perf.window.end) ($($perf.window.sessions) sessions, holdings_backfill)"
    } else { Bad "no window, or the wrong kind - a figure with no provenance" }

    if ($perf.base_currency) { Ok "valued in $($perf.base_currency), fx basis '$($perf.fx_basis)'" }
    else { Bad "no base_currency - the _ils fields are claiming a currency nobody set" }

    # T0.3(a): an annualized figure and a total-period figure must both exist, so
    # the card can put like beside like instead of mixing bases on one line.
    if ($null -ne $perf.excess_return_pct -and $null -ne $perf.excess_cagr_pct) {
        Ok "excess on both bases: $($perf.excess_return_pct)% total, $($perf.excess_cagr_pct)%/yr"
    } elseif ($null -eq $perf.benchmark_return_pct) {
        Skip "no benchmark overlap in this window"
    } else { Bad "only one excess basis is present - the card cannot compare like with like" }

    if ($perf.degraded -and ($perf.degraded -contains 'fx')) {
        $tk = ($perf.unconverted_holdings | ForEach-Object { $_.ticker }) -join ', '
        Bad "FX unavailable for $tk - those holdings are valued unconverted"
    } else { Ok "every holding was FX-converted" }
}

# =========================================================================== #
Sec "T0.2  a backtest knows which benchmark it was measured against"
# =========================================================================== #
$bt = Api GET '/api/v1/strategies/backtests'
if ($null -eq $bt) { Bad "/strategies/backtests unreachable" }
else {
    $rows = @()
    foreach ($p in $bt.PSObject.Properties) {
        if ($p.Value -and $p.Value.PSObject.Properties.Name -contains 'ok') {
            $rows += [pscustomobject]@{ id = $p.Name; row = $p.Value }
        }
    }
    if (-not $rows) {
        # `backtests` may nest under a key; try the common shapes before failing.
        if ($bt.backtests) {
            foreach ($p in $bt.backtests.PSObject.Properties) {
                $rows += [pscustomobject]@{ id = $p.Name; row = $p.Value }
            }
        }
    }
    if (-not $rows) { Skip "no backtest rows returned - has the refresh job run?" }
    else {
        $measured = $rows | Where-Object { $_.row.ok }
        if (-not $measured) { Skip "no row has ok=true yet" }
        else {
            $withBench = $measured | Where-Object { $_.row.benchmark_ticker }
            if ($withBench.Count -eq $measured.Count) {
                Ok "$($measured.Count) measured rows all record their benchmark"
            } elseif ($withBench.Count -eq 0) {
                $stale = $measured | Where-Object { $_.row.stale }
                if ($stale.Count -eq $measured.Count) {
                    Skip "no row records a benchmark yet, but every row is stale - recompute pending"
                } else { Bad "a row reports fresh AND has no benchmark_ticker" }
            } else { Bad "only $($withBench.Count)/$($measured.Count) rows record a benchmark" }

            $win = $measured | Where-Object { $_.row.window -and $_.row.window.kind -eq 'strategy_backtest' }
            if ($win.Count -eq $measured.Count) { Ok "every measured row carries a strategy_backtest window" }
            else { Bad "$($measured.Count - $win.Count) rows have no window" }

            # T0.1 -- the strategy measured against the thing it levers. Only the
            # rule-based families have a base; the factor stack legitimately
            # reports base_tickers null, and null is not the same as zero.
            $based = $measured | Where-Object { $_.row.metrics.base_tickers }
            if ($based) {
                $r = $based[0]
                if ($null -ne $r.row.metrics.excess_over_base_cagr_pct) {
                    Ok ("$($r.id): $($r.row.metrics.cagr_pct)%/yr vs its base " +
                        "$(($r.row.metrics.base_tickers) -join '+') = " +
                        "$($r.row.metrics.excess_over_base_cagr_pct)%/yr, " +
                        "vs the book benchmark = $($r.row.metrics.excess_cagr_pct)%/yr")
                } else { Bad "$($r.id) names a base but reports no excess over it" }
            } else {
                $anyStale = $measured | Where-Object { $_.row.stale }
                if ($anyStale) { Skip "no row carries base metrics yet - engine a4 recompute pending" }
                else { Bad "fresh rows, but none measured against its own base" }
            }
        }
    }
}

# =========================================================================== #
Sec "T2.0  the solver refuses the half-question"
# =========================================================================== #
# A return target with an implied drawdown tolerance is the thing this endpoint
# exists to stop being asked, so the ceiling is required, not defaulted.
$code = Status GET "/api/v1/plan/target?excess_pct=$Excess"
if ($code -eq 422) { Ok "a target without a ceiling is refused (HTTP 422)" }
elseif ($code -eq 200) { Bad "a target with no ceiling was accepted - the ceiling has a default somewhere" }
else { Skip "unexpected status $code for the missing-ceiling case" }

$zero = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=0"
if ($zero -and $zero.outcome -eq 'NOT_MEASURABLE' -and $zero.reason -eq 'NO_CEILING') {
    Ok "a zero ceiling abstains rather than solving"
} elseif ($zero) { Bad "a zero ceiling returned '$($zero.outcome)'" }
else { Bad "the zero-ceiling case did not respond" }

# =========================================================================== #
Sec "T2.1  a solve changes nothing"
# =========================================================================== #
$before = @{ s = (Api GET '/api/v1/plan/sleeves'); p = (Api GET '/api/v1/portfolio') }
$sol = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$Ceiling" 240
$after = @{ s = (Api GET '/api/v1/plan/sleeves'); p = (Api GET '/api/v1/portfolio') }

if ($null -eq $before.s -or $null -eq $after.s -or $null -eq $before.p -or $null -eq $after.p) {
    Bad "could not snapshot the book either side of the solve"
} else {
    $b = Fingerprint $before.s $before.p
    $a = Fingerprint $after.s $after.p
    if ($b -eq $a) { Ok "sleeves, positions and cash are byte-identical after the solve" }
    else {
        Bad "THE SOLVE MOVED THE BOOK"
        Write-Host "        before: $b" -ForegroundColor DarkRed
        Write-Host "        after : $a" -ForegroundColor DarkRed
    }
}

# =========================================================================== #
Sec "T2.2  the verdict is one of the five, and it means something"
# =========================================================================== #
$OUTCOMES = @('REACHED','REACHED_ABOVE_CAP','DRAWDOWN_BOUND','UNREACHABLE','NOT_MEASURABLE')
if ($null -eq $sol) { Bad "/plan/target did not respond"; }
elseif ($OUTCOMES -notcontains $sol.outcome) { Bad "unknown outcome '$($sol.outcome)'" }
else {
    Ok "beat $($sol.benchmark) by $Excess%/yr under a $Ceiling% ceiling -> $($sol.outcome)"

    if ($null -eq $sol.execution_plan) { Ok "execution_plan is null - read-only holds" }
    else { Bad "execution_plan is populated in a read-only phase" }

    if ($sol.would_execute) {
        if ($null -eq $sol.would_execute.legs) { Ok "would_execute prices nothing (legs null)" }
        else { Bad "priced legs in a read-only phase - a stale quote could ship as a claim" }
        if ($sol.would_execute.legs_schema -contains 'price_as_of') { Ok "the leg schema carries price_as_of" }
        else { Bad "the leg schema has no price_as_of - staleness could not be checked at apply time" }
    }

    switch ($sol.outcome) {
        'REACHED' {
            if ($sol.solved_total_sleeve_pct -gt 0) { Ok "solved at $($sol.solved_total_sleeve_pct)% total sleeve (shown as $($sol.display_total_sleeve_pct)%)" }
            else { Bad "REACHED with no size" }
            if ($sol.display_total_sleeve_pct -ge $sol.solved_total_sleeve_pct) { Ok "the displayed size rounds UP, never under the target" }
            else { Bad "the displayed size is below the solved size" }
            if ($sol.measured.max_drawdown_pct -le $Ceiling) { Ok "the answer respects the ceiling ($($sol.measured.max_drawdown_pct)% <= $Ceiling%)" }
            else { Bad "REACHED at $($sol.measured.max_drawdown_pct)% drawdown, above the $Ceiling% ceiling" }
        }
        'DRAWDOWN_BOUND' {
            $bc = $sol.binding_constraint
            if ($bc.would_require_pct -gt $bc.ceiling_pct) { Ok "reachable only at $($bc.would_require_pct)% drawdown vs your $($bc.ceiling_pct)% ceiling" }
            else { Bad "DRAWDOWN_BOUND but the required drawdown is not above the ceiling" }
            if ($sol.floor.breaches_ceiling) { Ok "and the floor is honest: the book breaches this ceiling at ZERO sleeve" }
            elseif ($sol.best_within_ceiling) { Ok "best inside the ceiling: $($sol.best_within_ceiling.excess_pct)%/yr at $($sol.best_within_ceiling.total_sleeve_pct)%" }
            else { Bad "nothing admissible, and no floor note explaining why" }
        }
        'REACHED_ABOVE_CAP' {
            $bc = $sol.binding_constraint
            if ($bc.ticker -and $bc.would_reach_pct -gt $bc.cap_pct) { Ok "the cap binds: $($bc.ticker) would reach $($bc.would_reach_pct)% against a $($bc.cap_pct)% cap" }
            else { Bad "REACHED_ABOVE_CAP without naming the ticker that breaches" }
        }
        'UNREACHABLE' {
            $bc = $sol.binding_constraint
            if ($bc -and $bc.component) { Ok "binding: $($bc.component) measured $($bc.component_excess_pct)%/yr against the benchmark" }
            else { Bad "UNREACHABLE with no binding constraint - a dead end, not a work item" }
        }
        'NOT_MEASURABLE' { Skip "not measurable: $($sol.reason) $($sol.detail)" }
    }
}

# =========================================================================== #
Sec "T2.3  a tighter ceiling never permits a larger sleeve"
# =========================================================================== #
if ($sol -and $sol.outcome -eq 'REACHED') {
    $tight = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$([math]::Round($Ceiling / 2, 2))" 240
    if ($null -eq $tight) { Bad "the tighter solve did not respond" }
    elseif ($tight.outcome -eq 'REACHED') {
        if ($tight.solved_total_sleeve_pct -le $sol.solved_total_sleeve_pct) {
            Ok "halving the ceiling did not raise the answer ($($tight.solved_total_sleeve_pct)% <= $($sol.solved_total_sleeve_pct)%)"
        } else { Bad "a TIGHTER ceiling returned a LARGER sleeve - the constraint is not binding" }
    } else { Ok "halving the ceiling turned the answer into $($tight.outcome), which is a legitimate no" }
} else { Skip "monotonicity needs a REACHED baseline" }

# =========================================================================== #
Sec "T1  the blend fields only a simulated blend can produce"
# =========================================================================== #
if ($sol -and $sol.measured) {
    $m = $sol.measured
    if ($m.detailed) { Ok "the reported point was measured in full, not from the sweep" }
    else { Bad "the card's point came from the light sweep - fields it never computed could render" }

    if ($m.components -and $m.components.Count -ge 1) {
        $names = ($m.components | ForEach-Object { "$($_.id) $($_.weight_pct)%" }) -join ', '
        Ok "components measured standalone: $names"
    } else { Bad "no per-component measurements" }

    if ($null -ne $m.diversification_delta_pct) {
        Ok "diversification delta $($m.diversification_delta_pct) points (near zero is expected - leveraged sleeves fall with the core)"
    } else { Bad "no diversification delta - the blend is asserting, not measuring" }

    # CASH is a real position row (ticker CASH, market TASE, price 1). It must
    # never reach the core basket: the backtest would ask for ten years of
    # history for it and abstain, and cash is already modelled as the weight the
    # blend does not allocate.
    $tks = @()
    if ($m.peak_weight_pct_by_ticker) {
        $tks = $m.peak_weight_pct_by_ticker.PSObject.Properties.Name
    }
    if ($tks -contains 'CASH') { Bad "CASH is being held as a component - cash is the unallocated remainder, not a holding" }
    elseif ($tks) { Ok "no CASH component; the book's tickers are $($tks -join ', ')" }
    else { Skip "no per-ticker peaks to inspect" }

    if ($null -ne $m.excess_at_equal_risk_pct) {
        Ok "raw excess $($m.excess_cagr_pct)%/yr, at EQUAL RISK $($m.excess_at_equal_risk_pct)%/yr (leverage $($m.equal_risk_leverage)x)"
    } else { Skip "no equal-risk figure - the blend may be safer than the benchmark at any leverage" }
} else { Skip "no measured blend to inspect" }

# =========================================================================== #
Sec "T3  what it costs"
# =========================================================================== #
if ($sol -and $sol.cost) {
    $c = $sol.cost
    if ($c.drawdown.recovery_pct -gt $c.drawdown.pct) {
        Ok "drawdown $($c.drawdown.pct)% = $($c.drawdown.ils) ILS, needing +$($c.drawdown.recovery_pct)% to recover"
    } else { Bad "recovery is not above the fall - the asymmetry is missing" }

    if ($c.projection) {
        $p = $c.projection
        if ($p.basis -eq 'real') { Ok "projected in real terms over $($p.horizon_years) years" }
        else { Bad "the projection does not lead in real terms" }
        if ($p.real.median_ils -lt $p.real.mean_ils) {
            Ok "median $($p.real.median_ils) sits $($p.median_below_mean_pct)% BELOW the mean $($p.real.mean_ils) - the average is not the likely outcome"
        } else { Bad "the median is not below the mean - the volatility drag is missing" }
        if ($null -ne $p.probability_of_real_loss) { Ok "probability of a real loss: $($p.probability_of_real_loss)" }
        else { Bad "no probability of real loss" }
    } else { Skip "no projection (NAV is zero?)" }

    # A blend built on _metrics alone never produced these at all, and a null
    # renders identically to a strategy that pays no tax. Null is the failure;
    # a measured zero would be fine and would say 0.
    if ($null -ne $c.tax.drag_pct_per_year) {
        Ok "tax drag to STAY in it: $($c.tax.drag_pct_per_year)%/yr at $($c.tax.cgt_rate_pct)% CGT (gross $($c.tax.gross_cagr_pct), net $($c.tax.net_cagr_pct))"
    } else { Bad "no tax drag reported - the blend never computed it, which reads as 'pays no tax'" }

    if ($c.funding) {
        if ($c.funding.degraded) { Bad "the funding preview is degraded: $($c.funding.note)" }
        elseif ($c.funding.needed_ils -eq 0) { Ok "nothing to fund - the solved size is not larger than what you run" }
        else { Ok "cost to ARRIVE: $($c.funding.needed_ils) ILS, CGT $($c.funding.estimated_cgt_ils), shortfall $($c.funding.shortfall_ils)" }
    } else { Skip "no funding preview" }
} elseif ($sol -and $sol.measured) { Bad "a measured blend with no cost block - T3 did not attach" }
else { Skip "no cost to check without a measured blend" }

# =========================================================================== #
Sec "T3.1  the solver's NAV is the app's NAV"
# =========================================================================== #
# Two implementations of NAV is two numbers that can disagree on one screen.
# The drawdown block is denominated in shekels, so it pins the NAV the solver
# used -- and that has to match what /portfolio reports.
if ($sol -and $sol.cost -and $sol.cost.drawdown.pct -gt 0 -and $after.p) {
    $implied = [math]::Round($sol.cost.drawdown.ils / ($sol.cost.drawdown.pct / 100.0), 2)
    $reported = [double]$after.p.nav
    if ($reported -le 0) { Skip "/portfolio reports no NAV to compare against" }
    elseif ([math]::Abs($implied - $reported) -le [math]::Max(1.0, $reported * 0.01)) {
        Ok "the solver priced the book at $implied ILS, matching /portfolio ($reported)"
    } else {
        Bad "NAV disagreement: the solver used $implied ILS, /portfolio says $reported"
    }
} else { Skip "no drawdown in shekels to cross-check NAV against" }

if ($sol -and ($sol.degraded -contains 'price')) {
    Bad "unpriced holdings ($($sol.unpriced_holdings -join ', ')) - NAV understates the book, and every shekel figure with it"
} elseif ($sol) { Ok "every holding was priced" }

# =========================================================================== #
if (-not $SkipChain) {
    Sec "chaining smoke-beat-market"
    $prev = Join-Path $PSScriptRoot 'smoke-beat-market.ps1'
    if (Test-Path $prev) {
        & $prev -SkipShaCheck
        if ($LASTEXITCODE -ne 0) { Bad "smoke-beat-market failed" } else { Ok "smoke-beat-market passed" }
    } else { Skip "smoke-beat-market.ps1 not found" }
} else { Skip "chain skipped (-SkipChain) - this is NOT a pass" }

# =========================================================================== #
Write-Host "`n================ T0..T3 ================" -ForegroundColor White
Write-Host ("  {0} pass / {1} fail / {2} skip" -f $pass, $fail, $skip) -ForegroundColor `
    $(if ($fail) { 'Red' } elseif ($skip) { 'Yellow' } else { 'Green' })
if ($fail) { exit 1 }
exit 0
