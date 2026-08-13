# SMOKE - C2: a book runs N sleeves.
#
#   .\scripts\smoke\smoke-c2.ps1                 # read-only
#   .\scripts\smoke\smoke-c2.ps1 -Execute        # ALSO adds and removes a test sleeve
#   .\scripts\smoke\smoke-c2.ps1 -SkipShaCheck
#
# Read-only by default, and the read-only half is the important half: it checks
# that what production ALREADY holds is self-consistent - caps agree with
# sleeves, the arithmetic closes, the legacy pointer names a sleeve that exists.
#
# -Execute additionally runs a real round trip: add a small second sleeve,
# confirm the cap maths and the refusal, then remove it and confirm the cap is
# retired. It writes to your live book and puts it back. It never sells
# anything - a sleeve is a target, and removing one moves no shares - but it
# does arm and retire a real max_weight rule, so it is opt-in.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$Execute,
    [string]$ProbeStrategy = 'btm_factor_stack',
    [double]$ProbePct = 5,
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

function Api($method, $path, $tmo = 90) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

function Caps() {
    $r = Api GET '/api/v1/rules'
    if ($null -eq $r) { return $null }
    $out = @{}
    foreach ($x in @($r.rules)) {
        if ($x.rule_type -eq 'max_weight' -and $x.active) { $out[$x.ticker.ToUpper()] = $x }
    }
    return $out
}

Write-Host "Smoke: C2 N sleeves  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "C2.0  am I talking to the new container?"
# =========================================================================== #
if ($SkipShaCheck) {
    Skip "SHA check skipped - everything below assumes you confirmed the deploy"
} else {
    if (-not $Sha) { try { $Sha = (git rev-parse --short HEAD).Trim() } catch { $Sha = '' } }
    $h = Api GET '/health'
    if ($null -eq $h) { Bad "/health unreachable - stopping"; exit 1 }
    $live = "$($h.commit)"
    if (-not $live -or $live -eq 'unknown') { Bad "/health reports no commit" }
    elseif (-not $Sha) { Skip "no local SHA to compare; deployed is $live" }
    elseif ($live.StartsWith($Sha) -or $Sha.StartsWith($live)) { Ok "deployed commit is $live" }
    else { Bad "deployed is $live, you are smoking $Sha - the deploy has not rolled"; exit 1 }
}

# =========================================================================== #
Sec "C2.1  the DELETE route exists (a 405 here means C2 has not deployed)"
# =========================================================================== #
# Probing with a strategy id nobody runs: the handler refuses it by name, which
# proves the route is wired without touching a real sleeve.
$probe = Api DELETE '/api/v1/plan/sleeves/__not_a_strategy__'
if ($null -eq $probe) {
    Bad "DELETE /api/v1/plan/sleeves/{id} did not respond - C2 is not deployed"
    Write-Host "`nC2 SMOKE: cannot continue." -ForegroundColor Red
    exit 1
}
if ($probe.ok -eq $false -and "$($probe.error)" -match 'not a sleeve') {
    Ok "the remove route is wired, and refuses a strategy you do not run"
} else {
    Bad "unexpected response from the remove probe: $($probe | ConvertTo-Json -Compress)"
}

# =========================================================================== #
Sec "C2.2  what the book actually runs right now"
# =========================================================================== #
$s = Api GET '/api/v1/plan/sleeves'
if ($null -eq $s) { Bad "/api/v1/plan/sleeves unreachable"; exit 1 }
$rows = @($s.sleeves)
foreach ($r in $rows) {
    Write-Host ("        {0,-24} {1,6:N1}%" -f $r.strategy_id, $r.sleeve_pct) -ForegroundColor Gray
}
Write-Host ("        {0,-24} {1,6:N1}%   (core, the remainder)" -f '-', $s.core_pct) -ForegroundColor DarkGray

if ([Math]::Abs(([double]$s.allocated_pct + [double]$s.core_pct) - 100) -lt 0.01) {
    Ok "allocated + core = 100%"
} else {
    Bad "allocated ($($s.allocated_pct)) + core ($($s.core_pct)) does not close to 100"
}
if ([double]$s.allocated_pct -le 100.0001) {
    Ok "the sleeves do not claim more of the book than there is"
} else {
    Bad "OVER-ALLOCATED at $($s.allocated_pct)% - nothing should have been able to write this"
}
if ($null -ne $rows[0].PSObject.Properties['created_at'] -or $rows.Count -eq 0) {
    Ok "rows carry created_at"
} else {
    Bad "created_at missing - the endpoint is the C1 shape, not C2's"
}

# =========================================================================== #
Sec "C2.3  the legacy pointer names a sleeve that exists"
# =========================================================================== #
$legacyId = "$($s.legacy.strategy)"
if (-not $legacyId) {
    if ($rows.Count -eq 0) { Ok "no strategy and no sleeves - consistent" }
    else { Bad "$($rows.Count) sleeve(s) run but /plan names no strategy" }
} elseif (@($rows | Where-Object { $_.strategy_id -eq $legacyId }).Count -eq 1) {
    Ok "/plan names '$legacyId', which is one of the sleeves running"
} else {
    Bad "/plan names '$legacyId' but that is not a sleeve on this book"
}

# =========================================================================== #
Sec "C2.4  ONE cap per ticker, and every cap belongs to a sleeve"
# =========================================================================== #
# The P1 duplicate bug at N scale is the thing to catch here. Counting raw rules
# rather than the deduped map, because a duplicate is exactly what a map hides.
$allRules = Api GET '/api/v1/rules'
if ($null -eq $allRules) {
    Skip "/api/v1/rules unreachable - cannot check the caps"
} else {
    $mw = @(@($allRules.rules) | Where-Object { $_.rule_type -eq 'max_weight' -and $_.active })
    $dupes = @($mw | Group-Object { $_.ticker.ToUpper() } | Where-Object { $_.Count -gt 1 })
    if ($dupes.Count -eq 0) {
        Ok "$($mw.Count) active max_weight cap(s), no ticker capped twice"
    } else {
        Bad "duplicate caps on: $(($dupes | ForEach-Object { $_.Name }) -join ', ') - the P1 bug is back"
    }
    foreach ($cap in $mw) {
        Write-Host ("        cap {0,-6} {1,6:N1}%" -f $cap.ticker, $cap.level) -ForegroundColor Gray
    }
}

# =========================================================================== #
Sec "C2.5  a sleeve did not rewrite the book's guardrails"
# =========================================================================== #
$p = Api GET '/api/v1/plan'
if ($null -eq $p) {
    Skip "/api/v1/plan unreachable"
} else {
    Write-Host "        objective $($p.objective), $($p.risk_tolerance) risk, cap $($p.caps.concentration_cap)" -ForegroundColor Gray
    Skip "objective/risk are yours to set now - this prints them so a CHANGE is visible"
    Write-Host "        Compare against what you had before the deploy. C2 stops sleeves" -ForegroundColor DarkGray
    Write-Host "        writing these; it must not have moved the existing values." -ForegroundColor DarkGray
}

# =========================================================================== #
if ($Execute) {
Sec "C2.6  ROUND TRIP - add a probe sleeve, then remove it"
# =========================================================================== #
    if (@($rows | Where-Object { $_.strategy_id -eq $ProbeStrategy }).Count -gt 0) {
        Skip "'$ProbeStrategy' is already a sleeve you run - not probing with it"
    } elseif ([double]$s.core_pct -lt $ProbePct) {
        Skip "only $($s.core_pct)% of the book is free - not enough room to probe with $ProbePct%"
    } else {
        $capsBefore = Caps
        $add = Api POST "/api/v1/strategies/$ProbeStrategy/apply?sleeve_pct=$ProbePct"
        if ($null -eq $add -or -not $add.ok) {
            Bad "could not add the probe sleeve: $($add.reason)"
        } else {
            Ok "added '$ProbeStrategy' at $ProbePct% ($($add.sleeve.action))"
            $mid = Api GET '/api/v1/plan/sleeves'
            if (@($mid.sleeves).Count -eq $rows.Count + 1) {
                Ok "the book now runs $(@($mid.sleeves).Count) sleeves - apply is ADDITIVE, not replacing"
            } else {
                Bad "sleeve count went $($rows.Count) -> $(@($mid.sleeves).Count); apply replaced instead of added"
            }

            # Over-allocation must refuse. Asking for more than the whole book.
            $over = Api POST "/api/v1/strategies/btm_dual_momentum/apply?sleeve_pct=101"
            if ($null -ne $over -and $over.ok -eq $false) { Ok "over-allocating is refused: $($over.reason)" }
            else { Bad "an over-allocating apply was ACCEPTED" }

            $del = Api DELETE "/api/v1/plan/sleeves/$ProbeStrategy"
            if ($null -eq $del -or -not $del.ok) {
                Bad "REMOVE FAILED - '$ProbeStrategy' is still on your book. Remove it by hand:"
                Write-Host "        .\scripts\set-sleeves.ps1 -Remove $ProbeStrategy" -ForegroundColor Red
            } else {
                Ok "removed it again; caps retired: $(@($del.retired_caps) -join ', ')"
                $end = Api GET '/api/v1/plan/sleeves'
                if (@($end.sleeves).Count -eq $rows.Count) { Ok "back to $($rows.Count) sleeve(s) - the book is as it was" }
                else { Bad "ended with $(@($end.sleeves).Count) sleeves, started with $($rows.Count)" }

                $capsAfter = Caps
                if ($null -ne $capsBefore -and $null -ne $capsAfter) {
                    $leaked = @($capsAfter.Keys | Where-Object { -not $capsBefore.ContainsKey($_) })
                    if ($leaked.Count -eq 0) { Ok "no cap left behind by the probe" }
                    else { Bad "caps survived the removal: $($leaked -join ', ')" }
                }
            }
        }
    }
} else {
Sec "C2.6  ROUND TRIP - skipped"
    Skip "add/remove not exercised. Re-run with -Execute to prove it end to end."
    Write-Host "        It writes to your live book and puts it back. Nothing is sold." -ForegroundColor DarkGray
}

# =========================================================================== #
Write-Host "`n----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "C2 SMOKE: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a pass - read the note under each one." -ForegroundColor Yellow }
Write-Host "Phone: the Plan tab still says 'One strategy applies at a time' and shows" -ForegroundColor DarkGray
Write-Host "one card ticked. That is expected until C5. set-sleeves.ps1 is the truth." -ForegroundColor DarkGray
if ($fail) { exit 1 }
