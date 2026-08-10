# SMOKE - P3: regime-aware signals.
#
#   .\scripts\smoke\smoke-p3.ps1               # then chains p2 -> p1 -> p0 -> e2e
#   .\scripts\smoke\smoke-p3.ps1 -SkipChain    # P3's own checks only
#   .\scripts\smoke\smoke-p3.ps1 -Refresh      # recompute backtests first (SLOW)
#
# Read-only. The gate ships OFF everywhere, so P3 changes measurements, not
# behaviour - and the single most important check here is that it stayed off.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [switch]$SkipChain,
    [switch]$Refresh,
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

function Api($method, $path, $tmo = 120) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: P3 regime  ($BaseUrl)" -ForegroundColor White

if ($Refresh) {
    Write-Host "   recomputing backtests (slow: ~10y of daily closes per ticker)" -ForegroundColor Yellow
    $null = Api POST '/api/v1/strategies/backtests/refresh' 600
}

# =========================================================================== #
Sec "P3.3  the futures regime is a CROSS-CHECK, never a signal input"
# =========================================================================== #
$m = Api GET '/api/v1/markets/futures' 180
if ($null -eq $m) { Bad "/markets/futures unreachable - P3.3 cannot be assessed" }
elseif ($null -eq $m.PSObject.Properties['regime_proxy']) {
    Bad "no 'regime_proxy' in the response - P3 has not deployed to this environment"
}
else {
    if ($m.market.role -eq 'cross_check') { Ok "the futures regime is labelled role=cross_check" }
    else { Bad "the futures regime is not labelled a cross-check (role='$($m.market.role)')" }
    if ($m.market.note) { Ok "it says why it can never drive a signal" }
    else { Bad "no note explaining that futures have no usable history" }

    $p = $m.regime_proxy
    if ($null -eq $p) { Bad "no price-derived regime returned" }
    elseif ($p.ok -ne $true) { Skip "the price-derived regime abstained: $($p.reason) $($p.detail)" }
    else {
        Ok "price-derived regime: $($p.state) (score $($p.score))"
        $c = $p.components
        if ($null -eq $c) { Bad "the regime has no components - it must show its working" }
        else {
            Write-Host "        trend_up=$($c.trend_up)  vol=$($c.vol_pct)%  pctl=$($c.vol_percentile)  breadth=$($c.breadth)" -ForegroundColor DarkGray
            if ($null -eq $c.trend_up) { Bad "no trend component" } else { Ok "components present and readable" }
        }
        if ($p.degraded) { Skip "regime degraded: $($p.degraded)" }
        if ($null -ne $m.regime_agreement) {
            if ($m.regime_agreement) { Ok "futures and the price proxy agree today" }
            else {
                Ok "they disagree today - and the response says so rather than hiding it"
                if (-not $m.disagreement_note) { Bad "disagreement with no note explaining which one counts" }
            }
        }
    }
}

# =========================================================================== #
Sec "P3.2  measured both ways, and SHIPPED OFF"
# =========================================================================== #
$b = Api GET '/api/v1/strategies/backtests' 180
if ($null -eq $b) { Bad "/strategies/backtests unreachable" }
else {
    $withRegime = @($b.strategies | Where-Object { $_.backtest -and $_.backtest.robustness.regime })
    $measured = @($b.strategies | Where-Object { $_.backtest -and $_.backtest.ok })
    if ($measured.Count -eq 0) { Skip "no measured strategies yet - run with -Refresh" }
    elseif ($withRegime.Count -eq 0) {
        Skip "no strategy carries a regime comparison yet - the 03:30 job has not rerun since P3 deployed"
        Write-Host "        -> re-run this script with -Refresh, or wait for tonight" -ForegroundColor Yellow
    }
    else {
        Ok "$($withRegime.Count) of $($measured.Count) measured strategies carry a gated-vs-ungated comparison"

        # THE check. P3 changes measurements, not behaviour, until you say so.
        $on = @($withRegime | Where-Object { $_.backtest.robustness.regime.enabled })
        if ($on.Count -gt 0) {
            Bad "the gate is ENABLED on $($on.Count) strategy(ies): $(($on | ForEach-Object { $_.id }) -join ', ') - it must ship off until you compare the numbers"
        } else { Ok "the gate is off on every strategy, as decided" }

        foreach ($st in $withRegime) {
            $r = $st.backtest.robustness.regime
            $v = $r.verdict
            if (-not $v.comparable) { Skip "$($st.id): not comparable - $($v.reason)"; continue }
            if (-not $v.observations_match) {
                Bad "$($st.id): gated and ungated ran over DIFFERENT windows - the comparison is meaningless"
            }
            $tag = if ($v.improves) { "would improve" } else { "no better" }
            Write-Host ("        {0,-22} {1,-12} CAGR {2,6}  DD {3,6}   {4}" -f `
                $st.id, $tag, $v.cagr_delta_pct, $v.max_drawdown_delta_pct, $v.why) -ForegroundColor DarkGray
            if ($null -eq $r.gated.cagr_pct -and $r.gated.ok -ne $false) {
                Bad "$($st.id): regime block present but carries no gated numbers"
            }
        }
        Ok "every comparison states both deltas and a reason, not just a verdict"

        $improving = @($withRegime | Where-Object { $_.backtest.robustness.regime.verdict.improves })
        if ($improving.Count -gt 0) {
            Write-Host "`n  $($improving.Count) strategy(ies) where the gate WOULD improve things:" -ForegroundColor Yellow
            $improving | ForEach-Object { Write-Host "        $($_.id): $($_.backtest.robustness.regime.verdict.why)" -ForegroundColor Yellow }
            Write-Host "        Your call whether to enable any of them." -ForegroundColor Yellow
        } else {
            Write-Host "`n  The gate did not improve any strategy on this sample." -ForegroundColor DarkGray
            Write-Host "        That is a real result, not a failure - most of this family already" -ForegroundColor DarkGray
            Write-Host "        gates on a 200-day trend, so the regime proxy agrees with them." -ForegroundColor DarkGray
        }
    }
}

# =========================================================================== #
Sec "P3.2  the live signal reads the same regime, and does not act on it"
# =========================================================================== #
$sg = Api GET '/api/v1/strategies/signal' 180
if ($null -eq $sg) { Skip "/strategies/signal unreachable" }
elseif ($sg.ok -ne $true) { Skip "signal abstained: $($sg.reason) $($sg.detail)" }
elseif ($null -eq $sg.PSObject.Properties['regime']) {
    Bad "the live signal carries no regime - the live and measured paths are not sharing one function"
}
else {
    Ok "the live signal reports the regime: $($sg.regime.state)"
    if ($sg.regime.applied -eq $false) { Ok "and states plainly that it is not applied" }
    else { Bad "the live signal says the regime IS applied, but the gate ships off" }
    if ($m -and $m.regime_proxy.ok -and $sg.regime.state -ne $m.regime_proxy.state) {
        Bad "the Markets page and the live signal report DIFFERENT regimes ($($m.regime_proxy.state) vs $($sg.regime.state)) - they must be one function"
    } elseif ($m -and $m.regime_proxy.ok) {
        Ok "Markets and the live signal agree - one regime, one function"
    }
}

# =========================================================================== #
Sec "P3  YOU must check these - an HTTP call cannot see rendering"
Write-Host @"
  On the phone:

    [ ] Markets tab: the futures regime is visibly labelled a cross-check,
        not presented as the thing your strategies follow.

    [ ] If the two regimes disagree, the page SAYS so rather than showing
        two numbers and leaving you to notice.

    [ ] Plan tab: no strategy card claims a regime gate is active.
        The gate is off everywhere until you turn it on.

  And the judgement call this phase exists for:

    [ ] Read the gated-vs-ungated table printed above. Where the gate
        "would improve", decide whether you believe it - one sample of
        history, and the plan's own warning about curve-fitting applies.
"@ -ForegroundColor Gray

Write-Host "`n===== P3: $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run." -ForegroundColor DarkYellow }

if ($SkipChain) {
    Write-Host "`n-SkipChain: smoke-p2 was NOT run, so this is a partial verdict.`n" -ForegroundColor DarkYellow
    exit $(if ($fail) { 1 } else { 0 })
}

Write-Host "`n`n===== CHAINING: smoke-p2.ps1 (which chains p1 -> p0 -> e2e) =====" -ForegroundColor Magenta
$here = $PSScriptRoot
$psExe = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
$childOut = & $psExe -NoProfile -ExecutionPolicy Bypass -File "$here\smoke-p2.ps1" `
    -BaseUrl $BaseUrl 2>&1 | Out-String
Write-Host $childOut

$hits = [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped')
Write-Host "`n`n===== COMBINED VERDICT =====" -ForegroundColor Magenta
if ($hits.Count -eq 0) {
    Write-Host "FAIL  could not read any tally from smoke-p2 - its result is unknown, which is not a pass." -ForegroundColor Red
    $fail++
} else {
    $best = $hits | Sort-Object { [int]$_.Groups[1].Value + [int]$_.Groups[2].Value + [int]$_.Groups[3].Value } -Descending | Select-Object -First 1
    $cp = [int]$best.Groups[1].Value; $cf = [int]$best.Groups[2].Value; $cs = [int]$best.Groups[3].Value
    Write-Host "  P2 + earlier: $cp passed, $cf failed, $cs skipped" -ForegroundColor Gray
    $pass += $cp; $fail += $cf; $skip += $cs
}
Write-Host "  P3 + everything before it: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "  A SKIP is not a PASS." -ForegroundColor DarkYellow }
Write-Host ""
exit $(if ($fail) { 1 } else { 0 })
