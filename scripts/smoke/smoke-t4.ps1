# SMOKE - T4: the card is actually being served, and it renders what was measured.
#
#   .\scripts\smoke\smoke-t4.ps1                 # T4 checks, then chains smoke-t3
#   .\scripts\smoke\smoke-t4.ps1 -SkipChain      # T4 only
#   .\scripts\smoke\smoke-t4.ps1 -SkipShaCheck
#
# READ-ONLY. A UI phase cannot be smoked by clicking, so this checks the two
# things that have actually gone wrong with frontend deploys in this repo:
#
#   1. The shell that is SERVED is the one that was built. The stale-shell bug
#      (cache.add fetching through the browser HTTP cache) shipped a version bump
#      that re-cached the previous index.html under the new key -- the app looked
#      updated and was not. So: fetch the deployed shell and look for the card in
#      it, and confirm the service worker version moved.
#   2. The card may not claim a figure it did not compute. The card renders the
#      solver payload verbatim, so the check is that the payload is deterministic
#      and complete: two identical solves must agree to the decimal, and the
#      reported point must be flagged as measured in full rather than swept.
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

function Text($path) {
    try {
        # cache:reload equivalent - a smoke that reads a cached shell proves nothing.
        return (Invoke-WebRequest -Uri "$BaseUrl$path" -Headers @{ 'Cache-Control' = 'no-cache' } `
                -TimeoutSec 90 -UseBasicParsing).Content
    } catch { return $null }
}

Write-Host "Smoke: T4 the card  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "T4.0  am I talking to the new container?"
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
Sec "T4.1  the SERVED shell contains the card"
# =========================================================================== #
$html = Text '/app/index.html'
if ($null -eq $html) { Bad "could not fetch /app/index.html" }
else {
    $need = @('targetCard()', 'id="tgtExcess"', 'id="tgtCeiling"', 'solveTarget()',
              'sleevePanel()+targetCard()', 'id="ch_today"', 'loadTodayPerf()',
              'performance?range=')
    $missing = $need | Where-Object { $html -notlike "*$_*" }
    if (-not $missing) { Ok "the deployed shell carries the card and it is wired into the render" }
    else { Bad "the served shell is missing: $($missing -join ', ') - stale shell, or the deploy did not take" }

    # A slider implies every position on it is attainable. Assert on what is
    # SERVED, not on the local file - the local file is not what users get.
    $i = $html.IndexOf('// ---- T4: the target card')
    $j = if ($i -ge 0) { $html.IndexOf('function sleevePanel(){', $i) } else { -1 }
    if ($i -ge 0 -and $j -gt $i) {
        if ($html.Substring($i, $j - $i) -match 'type="range"') { Bad "the served card contains a range input" }
        else { Ok "no slider on the served card" }
    } else { Skip "could not isolate the card block in the served shell" }

    if ($html -match '</html>\s*$') { Ok "the served shell is whole (ends in </html>)" }
    else { Bad "the served shell does not end in </html> - truncated in transit or on disk" }
}

# =========================================================================== #
Sec "T4.2  the service worker version actually moved"
# =========================================================================== #
$sw = Text '/app/sw.js'
if ($null -eq $sw) { Bad "could not fetch /app/sw.js" }
elseif ($sw -match "const VERSION = 'iw-v(\d+)'") {
    $v = [int]$Matches[1]
    if ($v -ge 21) { Ok "service worker is iw-v$v" }
    else { Bad "service worker is still iw-v$v - installed apps keep the old shell" }
} else { Bad "could not read the service worker version" }

# =========================================================================== #
Sec "T4.3  the card cannot claim a figure it did not compute"
# =========================================================================== #
# The card renders the payload verbatim, so completeness and determinism of the
# payload IS the card-claims check. A figure that moves between two identical
# solves is not something anyone can act on.
$q = "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$Ceiling"
$a = Api GET $q 240
$b = Api GET $q 240
if ($null -eq $a -or $null -eq $b) { Bad "/plan/target did not respond twice" }
elseif ($a.outcome -ne $b.outcome) { Bad "two identical solves disagreed on the outcome" }
else {
    Ok "two identical solves agree: $($a.outcome)"
    if ($a.measured -and $b.measured) {
        $same = ($a.measured.cagr_pct -eq $b.measured.cagr_pct) -and
                ($a.measured.max_drawdown_pct -eq $b.measured.max_drawdown_pct) -and
                ($a.measured.excess_cagr_pct -eq $b.measured.excess_cagr_pct)
        if ($same) { Ok "the measured figures are identical to the decimal" }
        else { Bad "the measured figures moved between two identical solves" }

        if ($a.measured.detailed) { Ok "the reported point was measured in full, not swept" }
        else { Bad "the card would render fields the sweep never computed" }

        if ($a.cost -and $a.cost.projection) {
            if ($a.cost.projection.real.median_ils -eq $b.cost.projection.real.median_ils) {
                Ok "the projection is seeded - it does not move between refreshes"
            } else { Bad "the projection moved between two identical calls" }
        } else { Skip "no projection to check determinism against" }
    } else { Skip "no measured block (outcome $($a.outcome))" }

    # Every figure the card renders as measured must arrive with its provenance,
    # or the card is asserting rather than reporting.
    $prov = @()
    if (-not $a.benchmark) { $prov += 'benchmark' }
    if ($a.measured -and -not $a.measured.observations) { $prov += 'observations' }
    if (-not $a.solver_version) { $prov += 'solver_version' }
    if ($a.measured -and -not $a.measured.blend_engine) { $prov += 'blend_engine' }
    if (-not $prov) { Ok "the payload carries its provenance (benchmark, window, versions)" }
    else { Bad "missing provenance: $($prov -join ', ')" }

    if ($null -eq $a.execution_plan) { Ok "still read-only from the card's own endpoint" }
    else { Bad "execution_plan is populated" }
}

# =========================================================================== #
Sec "T4.4  the Today chart's ranges are measured, not sliced"
# =========================================================================== #
$prev = $null
foreach ($r in @('1W','1M','1Q','1Y','MAX')) {
    $p = Api POST "/api/v1/portfolio/performance?range=$r" 240
    if ($null -eq $p) { Bad "range $r did not respond"; continue }
    if (-not $p.ok) { Skip "range $r : $($p.reason)"; continue }
    if ($p.range -ne $r) { Bad "asked for $r, got back '$($p.range)'"; continue }
    # Re-based to ITS OWN start: that is what makes the y-axis "change over this
    # range" rather than change since some earlier day.
    if ($p.portfolio_index[0] -ne 100) { Bad "$r is not re-based to its own start" ; continue }
    if ($prev -ne $null -and $p.observations -lt $prev) {
        Bad "$r measured FEWER sessions than the shorter range before it"
    } else {
        Ok "$r : $($p.observations) sessions, $($p.window.start) to $($p.window.end)"
    }
    $prev = $p.observations
}
$bogus = Api POST '/api/v1/portfolio/performance?range=5Y' 90
if ($bogus -and $bogus.ok -eq $false) { Ok "an unknown range abstains instead of quietly drawing a year" }
elseif ($bogus) { Bad "range=5Y returned ok - it fell through to the default window" }
else { Bad "the unknown-range case did not respond" }

# =========================================================================== #
if (-not $SkipChain) {
    Sec "chaining smoke-t3 (T0..T3)"
    $prev = Join-Path $PSScriptRoot 'smoke-t3.ps1'
    if (Test-Path $prev) {
        & $prev -SkipShaCheck -Excess $Excess -Ceiling $Ceiling
        if ($LASTEXITCODE -ne 0) { Bad "smoke-t3 failed" } else { Ok "smoke-t3 passed" }
    } else { Skip "smoke-t3.ps1 not found" }
} else { Skip "chain skipped (-SkipChain) - this is NOT a pass" }

# =========================================================================== #
Write-Host "`n================ T4 ================" -ForegroundColor White
Write-Host ("  {0} pass / {1} fail / {2} skip" -f $pass, $fail, $skip) -ForegroundColor `
    $(if ($fail) { 'Red' } elseif ($skip) { 'Yellow' } else { 'Green' })
Write-Host "`nThen, on the phone: Plan tab -> the amber 'What would it take?' card." -ForegroundColor DarkGray
Write-Host "If it is not there, the installed PWA is holding the old shell - close and reopen once." -ForegroundColor DarkGray
if ($fail) { exit 1 }
exit 0
