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
Sec "C1.3  THE ONE THING ONLY PRODUCTION CAN ANSWER: is the old copy gone?"
# =========================================================================== #
# This section used to ask "did the backfill run?". It cannot any more, and that
# is the point: migration 0019 deleted both one-shot backfills along with the
# columns they read. They had been spent on every live database for weeks - the
# logs said "0 sleeve(s) created" on every boot.
#
# The question a live deploy alone can answer has changed shape, not gone away.
# The DDL list in main.py is what actually runs against production (alembic does
# not), so "the columns are dropped" is a claim about THIS database and nothing
# in the test suite can prove it. Ask.
$rows = @($s.sleeves)
$legacyBlock = $s.PSObject.Properties.Name -contains 'legacy'
if ($legacyBlock) {
    Bad "/plan/sleeves still carries a 'legacy' block - this deploy predates 0019"
} else {
    Ok "no legacy block on /plan/sleeves"
}

if ($rows.Count -eq 0) {
    Skip "no sleeves on this book, so there is no size to cross-check"
} else {
    Ok "$($rows.Count) sleeve row(s): $((@($rows | ForEach-Object { "$($_.strategy_id) $($_.sleeve_pct)%" })) -join ', ')"
}

# is_core is no longer reserved - C6 writes it, and the core row is how a static
# family manages the book. What must stay true is that there is AT MOST ONE.
$cores = @($rows | Where-Object { $_.is_core -eq $true })
if ($cores.Count -gt 1) {
    Bad "$($cores.Count) rows have is_core set - get_core would answer oldest-first and ignore your newer choice"
} else {
    Ok "at most one core row, as the model requires"
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
Sec "C1.5  ONE ANSWER - the old path is not there to disagree"
# =========================================================================== #
# This section used to prove the OPPOSITE: that C1 was inert and the old columns
# were still authoritative. That was correct for one release and wrong for the
# next twelve days, during which plans.strategy said 'btm_swing_dip @ 15.0%' on
# a book whose plan_sleeves rows said 38.4% + 25.6%. Three readers were still
# consulting it as a fallback, each one added to keep the protection "never
# weaker than C1"; by the end they made it weaker.
$p = Api GET '/api/v1/plan'
if ($null -eq $p) {
    Bad "/api/v1/plan unreachable - cannot confirm the old path is gone"
} else {
    $stale = @($p.PSObject.Properties.Name | Where-Object { $_ -in @('strategy', 'strategy_sleeve_pct') })
    if ($stale.Count -eq 0) {
        Ok "/plan reports no strategy pointer - /plan/sleeves is the only answer"
    } else {
        Bad "/plan still returns: $($stale -join ', ') - 0019 did not reach this deploy"
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
