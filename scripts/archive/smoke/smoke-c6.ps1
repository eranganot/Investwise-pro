# SMOKE - C6: the core has a name, and replacing the book cannot eat your sleeves.
#
#   .\scripts\smoke\smoke-c6.ps1                 # read-only
#   .\scripts\smoke\smoke-c6.ps1 -Execute        # ALSO sets and restores your core
#   .\scripts\smoke\smoke-c6.ps1 -SkipShaCheck
#
# The read-only half is the important half. It proves three things about the
# book as it already stands:
#
#   * the core row does not leak into the sleeve arithmetic (this is the one
#     that would be expensive to get wrong - a core counted as a sleeve would
#     arm max_weight caps across its whole basket);
#   * /mix and the Today rebalance card agree about the target allocation,
#     which they did not before C6 - they read it from two different places;
#   * "Replace book with this basket" is REFUSED while you run sleeves. That
#     button deletes every holding and is entirely sleeve-unaware. It is checked
#     with dry_run=true, so the check itself can never be the thing that fires.
#
# -Execute additionally sets a core, confirms it, and puts the previous one back
# (or clears it, if you had none). It writes to your live book and restores it.
# It sells NOTHING - a core is a target mix, not a set of positions.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$Execute,
    [string]$ProbeCore = 'bal_6040',
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

# A refusal is a 200 with ok=false here, not an HTTP error - but a body is worth
# reading either way, because a 4xx that carries a reason is still information.
function ApiBody($method, $path, $json, $tmo = 180) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders `
            -ContentType 'application/json' -Body $json -TimeoutSec $tmo
    } catch {
        try {
            $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
            return $sr.ReadToEnd() | ConvertFrom-Json
        } catch { return $null }
    }
}

Write-Host "Smoke: C6 the core is a choice  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "C6.0  am I talking to the new container?"
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
Sec "C6.1  the core is reported, and the response knows it may be nothing"
# =========================================================================== #
$s = Api GET '/api/v1/plan/sleeves'
if ($null -eq $s) { Bad "/api/v1/plan/sleeves unreachable - stopping"; exit 1 }
if ($s.PSObject.Properties.Name -notcontains 'core') {
    Bad "the response carries no 'core' key - C6 is not deployed"
    exit 1
}
Ok "/plan/sleeves reports a core key"

$rows = @($s.sleeves)
$core = $s.core
if ($null -eq $core) {
    Skip "no core strategy chosen on this book - the objective manages it"
    Write-Host "        Pick one on the Plan tab, or re-run with -Execute to probe." -ForegroundColor DarkGray
} else {
    Ok "core is '$($core.strategy_id)' ($($core.name))"
    $mix = ($core.target_allocation.PSObject.Properties |
            Where-Object { $_.Value -gt 0 } |
            ForEach-Object { "{0:N0}% {1}" -f ($_.Value * 100), $_.Name }) -join ' / '
    Write-Host "        managed to $mix" -ForegroundColor Gray
}

Write-Host ("        sleeves {0}    allocated {1:N1}%    core {2:N1}%" -f `
    $rows.Count, $s.allocated_pct, $s.core_pct) -ForegroundColor Gray

# =========================================================================== #
Sec "C6.2  the core row does not leak into the sleeve arithmetic"
# =========================================================================== #
# The expensive failure: a core counted as a sleeve would claim a share of the
# book it does not have, and _arm_sleeve_caps would arm a max_weight on every
# ticker in its basket. Both are visible from here without writing anything.
$sum = 0.0
foreach ($r in $rows) { $sum += [double]$r.sleeve_pct }
if ([Math]::Abs($sum - [double]$s.allocated_pct) -lt 0.05) {
    Ok ("allocated_pct ({0:N1}%) is the sleeve rows and nothing else" -f $s.allocated_pct)
} else {
    Bad ("allocated_pct is {0:N1}% but the sleeve rows sum to {1:N1}% - something else is counted" -f $s.allocated_pct, $sum)
}
if ([Math]::Abs(($sum + [double]$s.core_pct) - 100.0) -lt 0.05) {
    Ok "sleeves + core = 100% of the book"
} else {
    Bad ("sleeves + core = {0:N1}%, not 100" -f ($sum + [double]$s.core_pct))
}
if ($null -ne $core -and @($rows | Where-Object { $_.strategy_id -eq $core.strategy_id }).Count -gt 0) {
    Bad "the core '$($core.strategy_id)' is ALSO listed as a sleeve"
} else {
    Ok "the core is not among the sleeves"
}

# Caps: no max_weight should exist for a ticker only the core wants.
$rules = Api GET '/api/v1/rules'
if ($null -eq $rules) { Skip "could not read rules - cap check not run" }
else {
    $caps = @(@($rules.rules) | Where-Object { $_.rule_type -eq 'max_weight' -and $_.active })
    $sleeveTk = @{}
    foreach ($r in $rows) { $sleeveTk[$r.strategy_id] = $true }
    if ($null -eq $core) { Skip "no core set - nothing to check caps against" }
    else {
        # A cap on a ticker the core holds and no sleeve does would be the leak.
        $coreOnly = @($caps | Where-Object { $_.strategy_id -eq 'sleeve' -and $_.note -match 'core' })
        if ($coreOnly.Count -eq 0) { Ok "no sleeve-owned cap mentions the core" }
        else { Bad "caps armed for the core: $(($coreOnly | ForEach-Object { $_.ticker }) -join ', ')" }
    }
}

# =========================================================================== #
Sec "C6.3  one answer to 'what is my target mix'"
# =========================================================================== #
# Before C6 /mix read OBJ_TARGET directly while the Today rebalance card read
# plans.strategy. Two answers, and which one you got depended on the screen.
$mixr = Api GET '/api/v1/mix'
if ($null -eq $mixr -or $null -eq $mixr.target_allocation) {
    Skip "/mix returned no target allocation (no holdings?)"
} else {
    if ($null -eq $core) {
        Ok "no core chosen; /mix falls back to the objective ($($mixr.objective))"
    } else {
        $agree = $true
        foreach ($p in $core.target_allocation.PSObject.Properties) {
            $there = [double]($mixr.target_allocation.$($p.Name))
            if ([Math]::Abs($there - [double]$p.Value) -gt 0.001) { $agree = $false }
        }
        if ($agree) { Ok "/mix targets the core's mix, not the objective's" }
        else {
            Bad "/mix and the core disagree about the target allocation"
            Write-Host "        core: $($core.target_allocation | ConvertTo-Json -Compress)" -ForegroundColor DarkRed
            Write-Host "        /mix: $($mixr.target_allocation | ConvertTo-Json -Compress)" -ForegroundColor DarkRed
        }
    }
}

# =========================================================================== #
Sec "C6.4  replacing the book is REFUSED while you run sleeves"
# =========================================================================== #
# dry_run=true throughout. This is a destructive path, so the check must not be
# able to become the incident - the C3.5 lesson, where an unconditional
# dry_run=false call turned into a live trade the moment the gate opened.
if ($rows.Count -eq 0) {
    Skip "no sleeves on this book, so there is nothing for the refusal to protect"
} else {
    $rep = ApiBody POST "/api/v1/strategies/$ProbeCore/load-basket" '{"mode":"replace","dry_run":true}'
    if ($null -eq $rep) { Bad "load-basket did not respond" }
    elseif ($rep.ok -eq $false -and "$($rep.error)" -match 'sell your sleeves') {
        Ok "refused: $($rep.error)"
        if ("$($rep.reason)" -match 'core') { Ok "and the refusal says what to do instead" }
        else { Bad "the refusal is a dead end - no alternative offered" }
    }
    elseif ($rep.ok -eq $true) {
        Bad "REPLACE WAS ACCEPTED on a book running $($rows.Count) sleeve(s)."
        Write-Host "        This path deletes every holding, sleeves included. Do not press" -ForegroundColor Red
        Write-Host "        'Replace book with this basket' until this is fixed." -ForegroundColor Red
    }
    else { Bad "refused for the wrong reason: $($rep.error)" }
}

# =========================================================================== #
if ($Execute) {
Sec "C6.5  ROUND TRIP - set a core, then put yours back"
# =========================================================================== #
    $was = if ($null -ne $core) { $core.strategy_id } else { $null }
    if ($was -eq $ProbeCore) {
        Skip "'$ProbeCore' is already your core - not probing with it"
    } else {
        $ap = Api POST "/api/v1/strategies/$ProbeCore/apply"
        if ($null -eq $ap -or -not $ap.ok) { Bad "could not set the probe core: $($ap.reason)" }
        else {
            Ok "set '$ProbeCore' as the core ($($ap.core.action))"
            if ($null -eq $ap.sleeve) { Ok "and it created no sleeve row" }
            else { Bad "applying a static family created a SLEEVE: $($ap.sleeve | ConvertTo-Json -Compress)" }

            $mid = Api GET '/api/v1/plan/sleeves'
            if ($mid.core.strategy_id -eq $ProbeCore) { Ok "the core reads back as '$ProbeCore'" }
            else { Bad "the core reads back as '$($mid.core.strategy_id)'" }
            if (@($mid.sleeves).Count -eq $rows.Count) { Ok "sleeve count unchanged at $($rows.Count)" }
            else { Bad "sleeve count went $($rows.Count) -> $(@($mid.sleeves).Count)" }

            # THE defect C6 fixes: resizing a sleeve used to drag the target mix
            # back to the objective's, because both read plans.strategy.
            if ($rows.Count -gt 0) {
                $first = $rows[0]
                $rs = Api POST "/api/v1/strategies/$($first.strategy_id)/apply?sleeve_pct=$($first.sleeve_pct)"
                if ($null -eq $rs -or -not $rs.ok) { Skip "could not re-apply a sleeve at its own size" }
                else {
                    $after = Api GET '/api/v1/plan/sleeves'
                    if ($after.core.strategy_id -eq $ProbeCore) {
                        Ok "re-applying a sleeve left the core alone (this is the C6 fix)"
                    } else {
                        Bad "re-applying a sleeve changed the core to '$($after.core.strategy_id)'"
                    }
                }
            }

            # Restore.
            if ($was) {
                $back = Api POST "/api/v1/strategies/$was/apply"
                if ($null -ne $back -and $back.ok) { Ok "restored your core to '$was'" }
                else {
                    Bad "COULD NOT RESTORE your core. Set it by hand on the Plan tab: $was"
                }
            } else {
                $del = Api DELETE '/api/v1/plan/core'
                if ($null -ne $del -and $del.ok) { Ok "cleared the probe core; you had none before" }
                else { Bad "COULD NOT CLEAR the probe core '$ProbeCore' - clear it on the Plan tab" }
            }
        }
    }
} else {
Sec "C6.5  ROUND TRIP - skipped"
    Skip "set/restore not exercised. Re-run with -Execute to prove it end to end."
    Write-Host "        It writes to your live book and puts it back. Nothing is sold -" -ForegroundColor DarkGray
    Write-Host "        a core is a target mix, not a set of positions." -ForegroundColor DarkGray
}

# =========================================================================== #
Write-Host ""
Write-Host ("{0} passed / {1} failed / {2} skipped" -f $pass, $fail, $skip) -ForegroundColor `
    $(if ($fail -gt 0) { 'Red' } else { 'Green' })
if ($fail -gt 0) { exit 1 }
