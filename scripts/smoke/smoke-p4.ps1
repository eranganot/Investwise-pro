# SMOKE - P4: entry/exit rules, and drift that knows it is not a cold start.
#
#   .\scripts\smoke\smoke-p4.ps1              # then chains p3 -> p2 -> p1 -> p0 -> e2e
#   .\scripts\smoke\smoke-p4.ps1 -SkipChain   # P4's own checks only
#
# Read-only. Notifications (P4.2) are NOT in this deploy - see STATUS.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM, and a
# backtick inside a double-quoted string is an escape character.

param(
    [switch]$SkipChain,
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

Write-Host "Smoke: P4 rules + drift  ($BaseUrl)" -ForegroundColor White

$recs = Api GET '/api/v1/recommendations' 180
$pf   = Api GET '/api/v1/portfolio'
$plan = Api GET '/api/v1/plan'
$rules = Api GET '/api/v1/rules'

# =========================================================================== #
Sec "P4.1  entry/exit rules that ARE the strategy"
# =========================================================================== #
if ($null -eq $recs) { Bad "/recommendations unreachable - P4 cannot be assessed" }
else {
    if ($recs.degraded -and @($recs.degraded).Count -gt 0) {
        Bad "agents degraded: $(@($recs.degraded) -join ', ') - a missing card says nothing"
    } else { Ok "no agent degraded" }

    $disc = @($recs.recommendations | Where-Object { $_.id -like 'stratrules_*' })
    if ($disc.Count -eq 0) {
        Skip "no discipline card right now - every suggested rule may already be armed"
    } else {
        $offered = @($disc[0].apply.rules)
        $sig = @($offered | Where-Object { $_.rule_type -eq 'strategy_signal' })
        if ($sig.Count -eq 0) {
            Skip "the discipline card offers no entry/exit rules - the strategy may have no stored backtest yet"
        } else {
            Ok "$($sig.Count) entry/exit rule(s) offered: $((@($sig) | ForEach-Object { "$($_.ticker) $($_.mode)" }) -join ', ')"
            $unpinned = @($sig | Where-Object { -not $_.strategy_id })
            if ($unpinned.Count -gt 0) {
                Bad "$($unpinned.Count) entry/exit rule(s) do not pin a strategy - changing your applied strategy would silently repoint them"
            } else { Ok "every entry/exit rule pins the strategy it follows" }

            # An armed rule that cannot say how it performed is an opinion.
            if ($null -eq $disc[0].stats) {
                Bad "the card offers entry/exit rules with no measured statistics"
            } else {
                $st = $disc[0].stats
                Ok "carries measured stats: win rate $($st.win_rate_pct)%, avg hold $($st.avg_holding_days)d, expectancy $($st.expectancy_pct_per_trade)%/trade"
            }
        }
        if ($disc[0].apply.kind -ne 'create_rules') { Bad "the discipline card would not arm anything (kind=$($disc[0].apply.kind))" }
        else { Ok "Accept arms them (kind=create_rules)" }
    }

    # Anything already armed must round-trip through /rules.
    if ($null -ne $rules) {
        $armedSig = @($rules.rules | Where-Object { $_.rule_type -eq 'strategy_signal' })
        if ($armedSig.Count -gt 0) { Ok "$($armedSig.Count) entry/exit rule(s) armed and listed" }
        else { Skip "no entry/exit rule armed yet - Accept the discipline card to exercise this" }
    }
}

# =========================================================================== #
Sec "P4.3  drift, and the cold start it must refuse"
# =========================================================================== #
if ($null -eq $recs -or $null -eq $pf -or $null -eq $plan) {
    Skip "cannot assess drift without recommendations + portfolio + plan"
} else {
    $chosen = $plan.strategy_sleeve_pct
    $sid = $plan.strategy
    if (-not $sid) { Skip "no rule-based strategy applied, so there is no sleeve to drift" }
    else {
        $cold = @($recs.recommendations | Where-Object { $_.title -like '*hold none of it*' })
        $drift = @($recs.recommendations | Where-Object { $_.title -like '*has drifted to*' })
        Write-Host "        plan: $sid at $chosen%" -ForegroundColor DarkGray

        if ($cold.Count -gt 0) {
            Ok "cold start detected and named: $($cold[0].title)"
            $k = $cold[0].apply.kind
            if ($k -and $k -ne 'none') {
                Bad "the cold-start card is executable ('$k') - starting a position in one tap is exactly what it must NOT do"
            } else { Ok "it routes to Fund this sleeve instead of executing" }
            if ($cold[0].action -notlike '*Fund this sleeve*') { Bad "it does not say where to go" }
            else { Ok "it says exactly where to go" }
        }
        elseif ($drift.Count -gt 0) {
            Ok "drift detected: $($drift[0].title)"
            $k = $drift[0].apply.kind
            if ($k -eq 'fund_sleeve' -or $k -eq 'trim') { Ok "Accept executes the rebalance (kind=$k)" }
            else { Bad "drift card apply kind is '$k' - Accept would not rebalance" }
            if ($drift[0].impact -notlike '*no brokerage order*') {
                Bad "the drift card does not state that no brokerage order is placed"
            } else { Ok "states plainly that no brokerage order is placed" }
        }
        else {
            # /recommendations returns only the top 12 by severity, so "no card"
            # and "the card exists but sorted below the cutoff" look identical
            # from here. A MEDIUM drift card on a busy Today is exactly the case
            # that gets truncated -- reporting PASS would be passing on missing
            # data, which is the rule this script opens by quoting.
            $shown = @($recs.recommendations).Count
            if ($recs.count -gt $shown) {
                Skip "no drift card in the top $shown of $($recs.count) - it may exist below the cutoff, which is not the same as absent"
                Write-Host "        -> check Today on the phone, or dismiss a card and re-run" -ForegroundColor DarkGray
            } else {
                Ok "no drift card, and nothing was truncated - the sleeve really is inside its band"
            }
        }

        if ($cold.Count -gt 0 -and $drift.Count -gt 0) {
            Bad "both a cold-start AND a drift card for the same sleeve - they are mutually exclusive"
        }
    }
}

# =========================================================================== #
Sec "P4  YOU must check these - an HTTP call cannot see rendering"
Write-Host @"
  On the phone, Today:

    [ ] The discipline card lists entry AND exit alongside the stops, and
        quotes a win rate / average hold / expectancy. Fail: entry-exit
        rules with no numbers, which is an opinion wearing a rule's clothes.

    [ ] Accept it. Holdings > Rules shows the new entry/exit rules, each
        naming the strategy it follows.

    [ ] The sleeve card. You hold no TQQQ against a chosen 20%, so it should
        say you have never funded it and point at "Fund this sleeve" -- NOT
        offer a one-tap Accept. Fail: an Accept button on a cold start.

    [ ] Once the sleeve IS funded, the same card becomes a real rebalance
        with an Accept that names its funding legs first.

  NOT in this deploy: notifications (P4.2). Nothing should push yet.
"@ -ForegroundColor Gray

Write-Host "`n===== P4: $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run." -ForegroundColor DarkYellow }

if ($SkipChain) {
    Write-Host "`n-SkipChain: smoke-p3 was NOT run, so this is a partial verdict.`n" -ForegroundColor DarkYellow
    exit $(if ($fail) { 1 } else { 0 })
}

Write-Host "`n`n===== CHAINING: smoke-p3.ps1 (which chains p2 -> p1 -> p0 -> e2e) =====" -ForegroundColor Magenta
$here = $PSScriptRoot
$psExe = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
$childOut = & $psExe -NoProfile -ExecutionPolicy Bypass -File "$here\smoke-p3.ps1" `
    -BaseUrl $BaseUrl 2>&1 | Out-String
Write-Host $childOut

$hits = [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped')
Write-Host "`n`n===== COMBINED VERDICT =====" -ForegroundColor Magenta
if ($hits.Count -eq 0) {
    Write-Host "FAIL  could not read any tally from smoke-p3 - its result is unknown, which is not a pass." -ForegroundColor Red
    $fail++
} else {
    $best = $hits | Sort-Object { [int]$_.Groups[1].Value + [int]$_.Groups[2].Value + [int]$_.Groups[3].Value } -Descending | Select-Object -First 1
    $cp = [int]$best.Groups[1].Value; $cf = [int]$best.Groups[2].Value; $cs = [int]$best.Groups[3].Value
    Write-Host "  P3 + earlier: $cp passed, $cf failed, $cs skipped" -ForegroundColor Gray
    $pass += $cp; $fail += $cf; $skip += $cs
}
Write-Host "  P4 + everything before it: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "  A SKIP is not a PASS." -ForegroundColor DarkYellow }
Write-Host ""
exit $(if ($fail) { 1 } else { 0 })
