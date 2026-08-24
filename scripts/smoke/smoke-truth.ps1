# SMOKE - PR 2: the app says true things about money and time.
#
#   .\scripts\smoke\smoke-truth.ps1 -Sha <sha>       # READ-ONLY (default)
#   .\scripts\smoke\smoke-truth.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-truth.ps1 -TakeSnapshot     # WRITES one NAV row
#
# READ-ONLY by default. Every check is a GET unless you pass -TakeSnapshot, which
# POSTs one idempotent-per-day NAV snapshot.
#
# WHAT THIS EXISTS TO CATCH. Three reports that looked like reports:
#   * `nav_snapshot` failed 7 nights out of 7 on `ImportError: cannot import name
#     'async_session'`. The import is inside the function, so nothing at import
#     time and no linter saw it, and no test called it. Every missed night is a
#     day of history that cannot be reconstructed.
#   * /push/status computed blockers from a hardcoded two job ids, so the dead
#     job was not among the ones it could see and it answered "Nothing obviously
#     broken".
#   * /portfolio reported ONE `invested_ils` meaning contributions when a ledger
#     existed - so 7,388 of cost basis with no deposit behind it read as +42.48%
#     on a book that had gained 4.0%.
#
# T0.1 IS THE ONLY CHECK THAT PROVES THE SNAPSHOT FIX, AND IT TAKES A NIGHT.
# Everything else here can pass while the job is still dead, because the failure
# only shows at 22:10 UTC. Run this again tomorrow.
#
# House rules: a SKIP is never a PASS. Read paired state in one call. Print the
# exception TYPE on the first failure. Headers are $ApiHeaders, never $h.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [switch]$TakeSnapshot,
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
Write-Host "SMOKE - truth in reporting (PR 2)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray

# --------------------------------------------------------------------------- #
Sec "T0. The build under test is the one serving"
# --------------------------------------------------------------------------- #
$health = Api GET "/health"
if ($null -eq $health) {
    Bad "T0.1 /health did not answer - nothing below is meaningful"
} else {
    Ok "T0.1 /health answered, serving $($health.commit)"
    if ($SkipShaCheck) { Skip "T0.2 -SkipShaCheck set" }
    elseif (-not $Sha) { Skip "T0.2 no -Sha given" }
    elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) { Ok "T0.2 serving the commit under test" }
    else { Bad "T0.2 serving $($health.commit), expected $Sha - STOP" }
}

# --------------------------------------------------------------------------- #
Sec "T1. The scheduler reports its own dead jobs"
# --------------------------------------------------------------------------- #
$st = Api GET "/api/v1/push/status"
if ($null -eq $st) {
    Bad "T1.0 /api/v1/push/status did not answer"
} else {
    $hist = $st.scheduler.history
    $failing = @()
    foreach ($p in $hist.PSObject.Properties) {
        if ($p.Value.last_ok -eq $false) {
            $failing += "$($p.Name) ($($p.Value.failures)/$($p.Value.runs))"
            Write-Host "        $($p.Name): $($p.Value.failures) of $($p.Value.runs) failed - $($p.Value.last_error)" -ForegroundColor DarkYellow
        }
    }
    $blockers = @($st.blockers)
    $claimsHealth = ($blockers | Where-Object { $_ -match 'Nothing obviously broken' }).Count -gt 0

    if ($failing.Count -eq 0) {
        Ok "T1.1 no scheduled job is currently failing"
    } elseif ($claimsHealth) {
        Bad "T1.1 jobs failing ($($failing -join ', ')) but blockers claims 'Nothing obviously broken'"
    } else {
        $named = $true
        foreach ($f in $failing) {
            $jid = $f.Split(' ')[0]
            if (-not ($blockers | Where-Object { $_ -match [regex]::Escape($jid) })) { $named = $false; Bad "T1.1 job $jid is failing but is not named in blockers" }
        }
        if ($named) { Ok "T1.1 every failing job is named in blockers ($($failing -join ', '))" }
    }

    # The specific job this PR fixed.
    $nav = $hist.nav_snapshot
    if ($null -eq $nav) {
        Skip "T1.2 nav_snapshot has not run since this process started (it fires 22:10 UTC)"
    } elseif ($nav.last_ok -eq $true) {
        Ok "T1.2 nav_snapshot last run SUCCEEDED ($($nav.runs) runs, $($nav.failures) failures)"
    } else {
        Bad "T1.2 nav_snapshot still failing: $($nav.last_error)"
    }
}

# --------------------------------------------------------------------------- #
Sec "T2. Gain is measured against cost basis, and deposits are separate"
# --------------------------------------------------------------------------- #
$pf = Api GET "/api/v1/portfolio"
if ($null -eq $pf) {
    Bad "T2.0 /api/v1/portfolio did not answer"
} else {
    foreach ($f in @('cost_basis_ils', 'gain_vs_basis_ils', 'gain_vs_basis_pct', 'contributed_ils', 'unexplained_basis_ils')) {
        if ($null -eq $pf.PSObject.Properties[$f]) { Bad "T2.1 /portfolio is missing $f - the deploy may predate PR 2" }
    }
    if ($null -ne $pf.PSObject.Properties['cost_basis_ils']) {
        Ok "T2.1 /portfolio reports both views"

        # The basis figure must equal the sum of the rows' own basis. If it does
        # not, the headline is computed from something the rows do not show.
        $sum = 0.0
        foreach ($p in $pf.positions) { $sum += [double]$p.invested_ils }
        if ([Math]::Abs($sum - [double]$pf.cost_basis_ils) -lt 1.0) {
            Ok ("T2.2 cost basis {0:N2} matches the rows" -f $pf.cost_basis_ils)
        } else {
            Bad ("T2.2 cost_basis_ils {0:N2} but the rows sum to {1:N2}" -f $pf.cost_basis_ils, $sum)
        }

        $expect = [double]$pf.nav_ils - [double]$pf.cost_basis_ils
        if ([Math]::Abs($expect - [double]$pf.gain_vs_basis_ils) -lt 1.0) {
            Ok ("T2.3 gain vs basis {0:N2} ({1:N2}%) - the honest performance number" -f $pf.gain_vs_basis_ils, $pf.gain_vs_basis_pct)
        } else {
            Bad ("T2.3 gain_vs_basis_ils {0:N2} but NAV - basis is {1:N2}" -f $pf.gain_vs_basis_ils, $expect)
        }

        $gap = $pf.unexplained_basis_ils
        if ($null -eq $gap) {
            Skip "T2.4 no contributions ledger, so there is no gap to report"
        } elseif ([Math]::Abs([double]$gap) -lt 1.0) {
            Ok "T2.4 cost basis and deposits agree"
        } else {
            Write-Host ("        basis {0:N2} | deposits {1:N2}" -f $pf.cost_basis_ils, $pf.contributed_ils) -ForegroundColor DarkGray
            Skip ("T2.4 {0:N2} of cost basis has no deposit behind it - NAMED, not hidden. PR 3 reconciles it." -f $gap)
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "T3. A completed card comes back in a fortnight, not a quarter"
# --------------------------------------------------------------------------- #
$recs = Api GET "/api/v1/recommendations"
if ($null -eq $recs) {
    Bad "T3.0 /api/v1/recommendations did not answer"
} else {
    Write-Host ("        {0} live | {1} dismissed | {2} completed" -f $recs.count, $recs.dismissed_count, $recs.completed_count) -ForegroundColor DarkGray
    if ($recs.degraded -and $recs.degraded.Count -gt 0) {
        Write-Host "        degraded agents: $($recs.degraded -join ', ')" -ForegroundColor DarkYellow
    }
    # The TTL itself is not visible over the API, so this reports the suppression
    # counts and leaves the verdict to you. Asserting a guess would be worse.
    if ([int]$recs.completed_count -gt 0) {
        Skip ("T3.1 {0} card(s) still suppressed. They now expire 14 days after the Accept, not 90 - but the ones you accepted on 08-20/21 were filed under the OLD ttl and are already past it, so they should be BACK. If they are not, run /recommendations/restore-completed once." -f $recs.completed_count)
    } else {
        Ok "T3.1 nothing is in the completed bucket"
    }
}

# --------------------------------------------------------------------------- #
Sec "T4. Record a NAV row (WRITES - opt in)"
# --------------------------------------------------------------------------- #
if (-not $TakeSnapshot) {
    Skip "T4.1 not run - pass -TakeSnapshot to record today's row by hand"
} else {
    $snap = Api POST "/api/v1/portfolio/nav-history/snapshot"
    if ($null -eq $snap) {
        Bad "T4.1 snapshot POST failed"
    } else {
        Ok "T4.1 snapshot recorded"
        $h = Api GET "/api/v1/portfolio/nav-history?range=1M"
        if ($null -ne $h) {
            Write-Host "        dates: $($h.dates -join ', ')" -ForegroundColor DarkGray
            if ($h.gaps -and $h.gaps.Count -gt 0) {
                foreach ($g in $h.gaps) { Write-Host "        gap $($g.from) -> $($g.to) ($($g.days) days)" -ForegroundColor DarkYellow }
                Skip "T4.2 gaps remain - those days are gone permanently and cannot be backfilled"
            } else {
                Ok "T4.2 no gaps in the recorded series"
            }
        }
    }
}

Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
Write-Host "  T1.2 is the one that matters and it takes a night. Run this again" -ForegroundColor Cyan
Write-Host "  after 22:10 UTC: nav_snapshot must show last_ok true." -ForegroundColor Cyan
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0
