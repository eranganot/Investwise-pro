# SMOKE - C1: plan_sleeves lands inert.
#
#   .\scripts\smoke\smoke-c1.ps1                    # verifies against local HEAD
#   .\scripts\smoke\smoke-c1.ps1 -Sha 1a2b3c4       # verify a specific commit
#   .\scripts\smoke\smoke-c1.ps1 -SkipShaCheck      # already confirmed the deploy
#
# Read-only, every request. C1 adds a table, a one-shot backfill and a GET.
# Nothing it ships is allowed to change what the app does, so most of the value
# here is in the checks that assert NOTHING moved.
#
# The one thing only production can tell us is whether the backfill ran against
# the real database. The suite proves it works on an empty SQLite file; it says
# nothing about whether Eran's actually-applied strategy became a row.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM, so a
# UTF-8 dash in a comment becomes three junk bytes and the parser dies on a line
# that looks perfectly fine.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'

# Fail fast rather than carry a hardcoded key. The agent key already sits in
# 11 committed smoke scripts and 162 commits of history (backlog #4 rotates them
# all in one change); this script is not going to become the twelfth.
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

function Api($method, $path, $tmo = 60) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: C1 plan_sleeves  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "C1.0  am I even talking to the new container?"
# =========================================================================== #
# Two debugging rounds have been lost to a smoke run against a stale container.
# /health has reported the deployed commit since Phase B2 precisely so this
# stopped being an eyeball check on the Railway dashboard.
if ($SkipShaCheck) {
    Skip "SHA check skipped (-SkipShaCheck) - everything below assumes you confirmed the deploy"
} else {
    if (-not $Sha) {
        try { $Sha = (git rev-parse --short HEAD).Trim() } catch { $Sha = '' }
    }
    $h = Api GET '/health'
    if ($null -eq $h) {
        Bad "/health unreachable - the container is not serving. STOP; nothing below means anything."
        Write-Host "        'upstream error' = it did not boot. Read the traceback:" -ForegroundColor DarkGray
        Write-Host "        .\scripts\get-error-log.ps1" -ForegroundColor DarkGray
        exit 1
    }
    $live = "$($h.commit)"
    if (-not $live -or $live -eq 'unknown') {
        Bad "/health reports no commit - RAILWAY_GIT_COMMIT_SHA is unset on this deploy"
    } elseif (-not $Sha) {
        Skip "no local SHA to compare against; deployed commit is $live"
    } elseif ($live.StartsWith($Sha) -or $Sha.StartsWith($live)) {
        Ok "deployed commit is $live, which is the one being smoked"
    } else {
        Bad "deployed commit is $live but you are smoking $Sha - the deploy has not rolled yet"
        Write-Host "        Everything below would be testing the OLD container. Stopping." -ForegroundColor DarkRed
        exit 1
    }
}

# =========================================================================== #
Sec "C1.1  the endpoint exists and has the shape C2..C5 will build on"
# =========================================================================== #
$s = Api GET '/api/v1/plan/sleeves'
if ($null -eq $s) {
    Bad "/api/v1/plan/sleeves unreachable - a 404 here means C1 has not deployed"
    Write-Host "`nC1 SMOKE: cannot continue." -ForegroundColor Red
    exit 1
}
Ok "/api/v1/plan/sleeves responds"

foreach ($k in @('sleeves', 'allocated_pct', 'core_pct', 'core_is_implicit', 'legacy')) {
    if ($null -eq $s.PSObject.Properties[$k]) { Bad "response is missing '$k'" }
}
if ($s.core_is_implicit -eq $true) {
    Ok "core_is_implicit is true - the core is the remainder, said out loud rather than inferred"
} else {
    Bad "core_is_implicit is not true - the schema decision changed without this script noticing"
}

# =========================================================================== #
Sec "C1.2  the arithmetic - the core is exactly what the sleeves left"
# =========================================================================== #
$alloc = [double]$s.allocated_pct
$core = [double]$s.core_pct
$sum = [Math]::Round($alloc + $core, 4)
if ($alloc -gt 100) {
    Bad "sleeves claim $alloc% of the book - over-allocated, which nothing should have been able to write"
} elseif ([Math]::Abs($sum - 100) -lt 0.01) {
    Ok "$alloc% in sleeves + $core% core = 100%"
} else {
    Bad "allocated ($alloc) + core ($core) = $sum, not 100"
}

# =========================================================================== #
Sec "C1.3  THE ONE THING ONLY PRODUCTION CAN ANSWER: did the backfill run?"
# =========================================================================== #
# The suite proves the backfill works against an empty SQLite file. Whether it
# turned the strategy actually applied on this deploy into a row is a fact about
# the live database, and there is no way to learn it from here except to look.
$legacyId = "$($s.legacy.strategy)"
$legacyPct = $s.legacy.strategy_sleeve_pct
$rows = @($s.sleeves)

if (-not $legacyId) {
    Skip "no strategy applied on this book, so there was nothing to backfill"
    if ($rows.Count -eq 0) { Ok "and no sleeve rows were invented out of nothing" }
    else { Bad "$($rows.Count) sleeve row(s) exist with no applied strategy behind them" }
} elseif ($rows.Count -eq 0) {
    # Not a failure by itself: the one-shot marker may have been spent on a boot
    # that happened BEFORE this strategy was applied. That window is expected
    # and closes in C2 - but it has to be recognised, not silently passed.
    Skip "'$legacyId' is applied but has no sleeve row"
    Write-Host "        Expected IF this strategy was applied after the C1 deploy booted:" -ForegroundColor DarkGray
    Write-Host "        the backfill is a one-shot and was already spent. C2 closes this." -ForegroundColor DarkGray
    Write-Host "        NOT expected if it was applied before - then the backfill did not run." -ForegroundColor DarkGray
} elseif ($rows.Count -eq 1) {
    $r = $rows[0]
    if ("$($r.strategy_id)" -eq $legacyId) {
        Ok "backfilled: '$legacyId' is a sleeve row, matching the legacy column"
    } else {
        Bad "the sleeve row is '$($r.strategy_id)' but the applied strategy is '$legacyId'"
    }
    if ($null -eq $legacyPct) {
        Ok "no stored sleeve size (pre-0012 plan); backfilled at $($r.sleeve_pct)%"
        if ([double]$r.sleeve_pct -ge 100) {
            Bad "...at 100% - a whole book in one sleeve is the fallback this must never take"
        }
    } elseif ([Math]::Abs([double]$r.sleeve_pct - [double]$legacyPct) -lt 0.01) {
        Ok "at $($r.sleeve_pct)%, the same size the old column holds"
    } else {
        Bad "sleeve row says $($r.sleeve_pct)% but plans.strategy_sleeve_pct says $legacyPct%"
    }
} else {
    Bad "$($rows.Count) sleeve rows exist - C1 can only ever create one"
}

foreach ($r in $rows) {
    if ($r.is_core -eq $true) {
        Bad "'$($r.strategy_id)' has is_core set - that column is reserved and must stay unwritten in C1"
    }
}
if ($rows.Count -gt 0 -and -not ($rows | Where-Object { $_.is_core -eq $true })) {
    Ok "is_core is false on every row - still reserved, as designed"
}

# =========================================================================== #
Sec "C1.4  the GET writes nothing - not even a self-healing row"
# =========================================================================== #
# A GET that quietly created state would make "does this book have a sleeve?"
# depend on whether anyone happened to open the page. Same reason peek_user
# exists separately from evaluate_user.
$before = @($s.sleeves).Count
$stable = $true
for ($i = 0; $i -lt 3; $i++) {
    $again = Api GET '/api/v1/plan/sleeves'
    if ($null -eq $again -or @($again.sleeves).Count -ne $before) { $stable = $false }
}
if ($stable) { Ok "four reads, still $before row(s) - the endpoint is read-only in production too" }
else { Bad "the row count moved across repeated GETs - something on the read path is writing" }

# =========================================================================== #
Sec "C1.5  INERT - the old path is exactly where it was"
# =========================================================================== #
# This is the whole claim of the phase. If apply_strategy had started routing
# through the new table, this is what would have quietly changed.
$p = Api GET '/api/v1/plan'
if ($null -eq $p) {
    Bad "/api/v1/plan unreachable - cannot confirm the old path is untouched"
} else {
    if ("$($p.strategy)" -eq $legacyId) {
        Ok "/plan still reports strategy '$legacyId' - the old column is still authoritative"
    } else {
        Bad "/plan says '$($p.strategy)' but /plan/sleeves' legacy block says '$legacyId'"
    }
    if (($null -eq $p.strategy_sleeve_pct) -and ($null -eq $legacyPct)) {
        Ok "no stored sleeve size, consistently, on both routes"
    } elseif ([Math]::Abs([double]$p.strategy_sleeve_pct - [double]$legacyPct) -lt 0.01) {
        Ok "/plan still reports $($p.strategy_sleeve_pct)% - unchanged by the deploy"
    } else {
        Bad "/plan says $($p.strategy_sleeve_pct)% but the legacy block says $legacyPct%"
    }
}

# The rules the sleeve cap armed must still be there. C1 touches no rule code,
# so a change here would mean the deploy did something nobody asked it to.
$rules = Api GET '/api/v1/rules'
if ($null -eq $rules) {
    Skip "/api/v1/rules unreachable - cannot confirm the armed caps survived"
} else {
    $caps = @(@($rules.rules) | Where-Object { $_.rule_type -eq 'max_weight' -and $_.active })
    if ($caps.Count -gt 0) { Ok "$($caps.Count) active max_weight cap(s) still armed" }
    else { Skip "no active max_weight caps to check" }
}

# =========================================================================== #
Write-Host "`n----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "C1 SMOKE: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) {
    Write-Host "A SKIP is not a pass - read the note under each one." -ForegroundColor Yellow
}
Write-Host "Still phone-only: nothing. C1 renders no UI, which is the point." -ForegroundColor DarkGray
if ($fail) { exit 1 }
