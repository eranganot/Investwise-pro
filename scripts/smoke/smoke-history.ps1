# SMOKE - PR 5: the history provider, and gain measured against what you paid in.
#
#   .\scripts\smoke\smoke-history.ps1 -Sha <sha>      # READ-ONLY (default)
#   .\scripts\smoke\smoke-history.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-history.ps1 -Watch 20       # poll the breaker for N minutes
#
# READ-ONLY throughout. Nothing here writes.
#
# WHAT THIS EXISTS TO CATCH. `history` sat OPEN in production for a week while
# `market` - the SAME Yahoo provider, the SAME /v8/finance/chart/{sym} endpoint,
# one query parameter apart - stayed CLOSED and served quotes the whole time.
# The asymmetry was entirely in which keys each tier asks for: quotes go to the
# four tickers you HOLD, history goes to the screener's 25-name candidate set.
# A plain failures+=1 against a threshold of 5 could not tell "these symbols
# have no data" from "the provider is down".
#
# H2 IS THE POINT. Before this PR /providers/health returned circuit_state and
# NOTHING ELSE, so an OPEN breaker could not say which key failed, what the
# error was, or whether anything was succeeding alongside it. If H2 shows the
# new fields, the next outage is a five-minute diagnosis instead of a week.
#
# -Watch is the check that actually matters and it takes time: the breaker has
# to stay closed across several scans, not just look closed once.
#
# House rules: a SKIP is never a PASS. Read paired state in one call. Print the
# exception TYPE on the first failure. Headers are $ApiHeaders, never $h.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [int]$Watch = 0,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 120) {
    try {
        if ($null -ne $body) {
            $json = ($body | ConvertTo-Json -Depth 6)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $json -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host ""
Write-Host "SMOKE - the history provider (PR 5)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray

# --------------------------------------------------------------------------- #
Sec "H0. The build under test is the one serving"
# --------------------------------------------------------------------------- #
$health = Api GET "/health"
if ($null -eq $health) {
    Bad "H0.1 /health did not answer - nothing below is meaningful"
} else {
    Ok "H0.1 /health answered, serving $($health.commit)"
    if ($SkipShaCheck) { Skip "H0.2 -SkipShaCheck set" }
    elseif (-not $Sha) { Skip "H0.2 no -Sha given" }
    elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) { Ok "H0.2 serving the commit under test" }
    else { Bad "H0.2 serving $($health.commit), expected $Sha - STOP, everything below is about the old build" }
}

# --------------------------------------------------------------------------- #
Sec "H1. Is the history circuit closed?"
# --------------------------------------------------------------------------- #
$ph = Api GET "/api/v1/providers/health"
if ($null -eq $ph) {
    Bad "H1.0 /api/v1/providers/health did not answer"
} else {
    foreach ($n in @('market','fx','history','fundamentals','economic')) {
        $t = $ph.$n
        if ($null -eq $t) { continue }
        $extra = ""
        if ($t.PSObject.Properties['failing_key_count']) {
            $extra = " | failing $($t.failing_key_count) ok $($t.succeeding_key_count) opened $($t.opened_count)x"
        }
        Write-Host ("        {0,-13} {1}{2}" -f $n, $t.circuit_state, $extra) -ForegroundColor DarkGray
        if ($t.PSObject.Properties['last_error'] -and $t.last_error) {
            Write-Host ("                      last error on {0}: {1}" -f $t.last_error_key, $t.last_error) -ForegroundColor DarkYellow
        }
    }
    if ($ph.history.circuit_state -eq 'CLOSED') {
        Ok "H1.1 history circuit is CLOSED"
    } elseif ($ph.history.circuit_state -eq 'HALF_OPEN') {
        Skip "H1.1 history is HALF_OPEN - a probe is out. Re-run in a minute."
    } else {
        Bad "H1.1 history circuit is still $($ph.history.circuit_state)"
        if ($ph.history.last_error) {
            Write-Host "        THE CAUSE, which the old build could not tell you:" -ForegroundColor Yellow
            Write-Host "          key:   $($ph.history.last_error_key)" -ForegroundColor Yellow
            Write-Host "          error: $($ph.history.last_error)" -ForegroundColor Yellow
            Write-Host "        A 404/'no history' on a few candidate symbols is NOT a provider" -ForegroundColor Gray
            Write-Host "        outage and must no longer open this. A 429 or a timeout IS, and" -ForegroundColor Gray
            Write-Host "        means the next step is a fallback provider, not another retry." -ForegroundColor Gray
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "H2. Can the breaker say WHY? (the diagnostic that did not exist)"
# --------------------------------------------------------------------------- #
if ($null -eq $ph) {
    Bad "H2.0 no provider health to inspect"
} else {
    $want = @('circuit_state','consecutive_failures','failing_key_count',
              'succeeding_key_count','last_error','opened_count','stale_served')
    $missing = @($want | Where-Object { -not $ph.history.PSObject.Properties[$_] })
    if ($missing.Count -eq 0) {
        Ok "H2.1 the history tier reports state AND cause"
    } else {
        Bad ("H2.1 missing {0} - this deploy predates PR 5" -f ($missing -join ', '))
    }

    # A tier that has never succeeded at anything is a different problem from one
    # that is merely open, and only the new fields can tell them apart.
    if ($ph.history.PSObject.Properties['seconds_since_last_success']) {
        $s = $ph.history.seconds_since_last_success
        if ($null -eq $s) {
            Skip "H2.2 history has not served a single key since this process started"
        } else {
            Ok ("H2.2 history last served something {0:N0}s ago" -f $s)
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "H3. Do the agents that need history actually produce cards?"
# --------------------------------------------------------------------------- #
# 240s, not the default: a COLD scan fetches 25 tickers of history plus
# fundamentals serially and legitimately takes minutes on an empty cache.
$recs = Api GET "/api/v1/recommendations" $null 240
if ($null -eq $recs) {
    Bad "H3.0 /api/v1/recommendations did not answer (240s). On a cold cache the first scan is slow; try once more before believing it."
} else {
    $all = @($recs.recommendations)
    $deg = @($recs.degraded)
    Write-Host ("        {0} cards | degraded: {1}" -f $all.Count, $(if ($deg.Count) { $deg -join ', ' } else { '(none)' })) -ForegroundColor DarkGray

    # Every agent that reads guarded_history. `momentum` and `performance` are
    # the ones that reappear first, because they only need YOUR tickers.
    $needsHistory = @($all | Where-Object { $_.dimension -in @('holding','signal','momentum','performance') })
    if ($needsHistory.Count -gt 0) {
        Ok ("H3.1 {0} card(s) from history-backed agents" -f $needsHistory.Count)
    } elseif ($deg -contains 'holding_verdicts' -or $deg -contains 'war_room') {
        Bad "H3.1 no history-backed cards and those agents are named degraded - the provider is still down"
    } else {
        Skip "H3.1 no history-backed cards, but nothing is reported degraded either. Odd - check H1."
    }

    if ($deg -contains 'history_stale') {
        Skip ("H3.2 cards are being built on an EXPIRED series. That is the safety net working - you are seeing something rather than nothing - but the provider is not healthy. H1 says why.")
    } else {
        Ok "H3.2 nothing is being served from stale stock"
    }
}

# --------------------------------------------------------------------------- #
Sec "H4. Gain is measured against what you put in"
# --------------------------------------------------------------------------- #
$pf = Api GET "/api/v1/portfolio"
if ($null -eq $pf) {
    Bad "H4.0 /api/v1/portfolio did not answer"
} elseif ($null -eq $pf.PSObject.Properties['gain_vs_contributions_ils']) {
    Bad "H4.1 /portfolio has no gain_vs_contributions_ils - this deploy predates PR 5"
} elseif ($null -eq $pf.contributed_ils) {
    Skip "H4.1 no deposits recorded, so there is nothing to measure against"
} else {
    Write-Host ("        NAV {0:N2} | deposits {1:N2} | cost basis {2:N2}" -f $pf.nav_ils, $pf.contributed_ils, $pf.cost_basis_ils) -ForegroundColor DarkGray

    $expect = [double]$pf.nav_ils - [double]$pf.contributed_ils
    if ([Math]::Abs($expect - [double]$pf.gain_vs_contributions_ils) -lt 1.0) {
        Ok ("H4.1 gain vs deposits {0:N2} ({1:N2}%) - the headline" -f $pf.gain_vs_contributions_ils, $pf.gain_vs_contributions_pct)
    } else {
        Bad ("H4.1 gain_vs_contributions_ils {0:N2} but NAV - deposits is {1:N2}" -f $pf.gain_vs_contributions_ils, $expect)
    }

    # The identity that makes the residual explicable rather than unexplained.
    $realised = [double]$pf.cost_basis_ils - [double]$pf.contributed_ils
    $diff = [double]$pf.gain_vs_contributions_ils - [double]$pf.gain_vs_basis_ils
    if ([Math]::Abs($diff - $realised) -lt 1.0) {
        Ok ("H4.2 the two gain figures differ by exactly {0:N2} - realised gain, already banked and reinvested. Not a discrepancy." -f $realised)
    } else {
        Bad ("H4.2 the gain figures differ by {0:N2} but basis exceeds deposits by {1:N2} - these must be the same number" -f $diff, $realised)
    }
}

# --------------------------------------------------------------------------- #
Sec "H5. Does it STAY closed? (the only check that really counts)"
# --------------------------------------------------------------------------- #
if ($Watch -le 0) {
    Skip "H5.1 not run - pass -Watch <minutes> to poll the breaker. Looking closed ONCE proves very little: the old breaker also looked closed between the failures that re-opened it."
} else {
    $deadline = (Get-Date).AddMinutes($Watch)
    $opens = 0; $samples = 0; $worst = "CLOSED"
    while ((Get-Date) -lt $deadline) {
        # Drive a scan, then read the breaker it exercised - in that order, or
        # you are sampling a tier nothing has touched since the last poll.
        $null = Api GET "/api/v1/recommendations" $null 240
        $h = Api GET "/api/v1/providers/health"
        $samples++
        if ($null -ne $h) {
            $st = $h.history.circuit_state
            if ($st -ne 'CLOSED') { $opens++; $worst = $st }
            Write-Host ("        {0}  history={1} failing={2} ok={3} opened={4}x stale={5}" -f (Get-Date -Format 'HH:mm:ss'), $st, $h.history.failing_key_count, $h.history.succeeding_key_count, $h.history.opened_count, $h.history.stale_served) -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds 60
    }
    if ($samples -eq 0) {
        Bad "H5.1 no samples taken"
    } elseif ($opens -eq 0) {
        Ok ("H5.1 history stayed CLOSED across {0} scans over {1} minutes" -f $samples, $Watch)
    } else {
        Bad ("H5.1 history was {0} on {1} of {2} samples - still flapping" -f $worst, $opens, $samples)
    }
}

Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "    * Holdings should read: value, then the gain on what you PUT IN," -ForegroundColor Cyan
Write-Host "      then a quieter line splitting that into unrealised and realised." -ForegroundColor Cyan
Write-Host "      The amber 'no recorded deposit behind it' warning is gone -" -ForegroundColor Cyan
Write-Host "      that 350 was realised gain and is now counted, not flagged." -ForegroundColor Cyan
Write-Host "    * Today should show holding verdicts and momentum cards again." -ForegroundColor Cyan
Write-Host "    * If a provider does go down, degraded now says 'history_stale'" -ForegroundColor Cyan
Write-Host "      when you are looking at an older series rather than nothing." -ForegroundColor Cyan
Write-Host ""
Write-Host "  IF H1 IS STILL RED, READ THE last_error IT PRINTS:" -ForegroundColor DarkYellow
Write-Host "    'no history' / 404  -> a few dead candidate symbols. This PR is" -ForegroundColor DarkYellow
Write-Host "                           supposed to survive that; if it did not," -ForegroundColor DarkYellow
Write-Host "                           the threshold logic is wrong, tell me." -ForegroundColor DarkYellow
Write-Host "    429 / timeout       -> Yahoo is throttling Railway's IP. Nothing" -ForegroundColor DarkYellow
Write-Host "                           in this PR fixes that; the answer is a" -ForegroundColor DarkYellow
Write-Host "                           keyed fallback provider, and I did not add" -ForegroundColor DarkYellow
Write-Host "                           one on a guess. That is the next PR." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE (later PRs own these):" -ForegroundColor DarkYellow
Write-Host "    * Sleeves still read '64% of the book' when 15% is held (PR 6)." -ForegroundColor DarkYellow
Write-Host "    * No strategy manages the core; core is still null (PR 7)." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0
