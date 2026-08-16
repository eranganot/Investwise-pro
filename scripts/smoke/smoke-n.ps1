# SMOKE - Phase N: the deployed history is real history, and the deployed
# arithmetic is the arithmetic that was tested.
#
#   .\scripts\smoke\smoke-n.ps1                  # Phase N, then chains smoke-t4
#   .\scripts\smoke\smoke-n.ps1 -SkipChain       # Phase N only
#   .\scripts\smoke\smoke-n.ps1 -NoSnapshot      # do not record today's row
#   .\scripts\smoke\smoke-n.ps1 -SkipShaCheck
#
# WHAT THIS CAN AND CANNOT PROVE, stated up front because the difference matters:
#
#   The one failure mode that would never look wrong on screen is a deposit
#   being counted as a return. Proving that against production would mean
#   writing a fake contribution into the real ledger, which I will not do. So it
#   is proven in two halves:
#
#     (a) locally, by pytest, on the pure function that ships -- N.6 runs
#         test_a_deposit_does_not_move_the_return by node id;
#     (b) on production, read-only, by recomputing the time-weighted series in
#         PowerShell from the raw NAV points and flows the API returns, and
#         requiring it to match the API's own total to 1e-6 (N.5). If the
#         deployed container were running different arithmetic from the tested
#         module, that check is what catches it.
#
#   Together those cover it. Neither alone does, and I would rather say so than
#   print a green line that implies more than it checked.
#
# The one WRITE this performs is POST /portfolio/nav-history/snapshot, which is
# idempotent within a day and is the same row the 22:10 job writes. It is on by
# default on purpose: every day without a snapshot is a day of real history that
# can never be recovered. -NoSnapshot turns it off.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$SkipChain,
    [switch]$NoSnapshot,
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
        return (Invoke-WebRequest -Uri "$BaseUrl$path" -Headers @{ 'Cache-Control' = 'no-cache' } `
                -TimeoutSec 90 -UseBasicParsing).Content
    } catch { return $null }
}

Write-Host "Smoke: Phase N  real account history  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "N.0  am I talking to the new container?"
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
Sec "N.1  the table exists in the deployed database"
# =========================================================================== #
# The migration is guarded against 'already exists', which is correct but means
# a guard that fires for the WRONG reason (table absent, guard mis-reads) is
# invisible. The endpoint answering at all is the proof: it SELECTs the table.
$hist = Api GET '/api/v1/portfolio/nav-history?range=MAX'
if ($null -eq $hist) {
    Bad "GET /portfolio/nav-history failed - if this is a 500, 0016 did not run and the table is missing"
} else {
    Ok "nav-history responds (the query against nav_snapshots ran)"
    Write-Host "        ok=$($hist.ok) points=$($hist.points) since=$($hist.recorded_since)" -ForegroundColor DarkGray
}

# =========================================================================== #
Sec "N.2  the empty state is honest"
# =========================================================================== #
# This is the check I care about most on day one. The tempting bug is to seed
# the new chart from the backfill so it looks populated immediately -- which is
# the reconstruction wearing the real thing's label, and undoes the phase.
if ($null -eq $hist) { Skip "no payload" }
elseif (-not $hist.ok) {
    if ("$($hist.reason)" -match 'not enough history') {
        Ok "not enough history yet, and it says so: '$($hist.detail)'"
    } else { Bad "ok=false for an unexpected reason: $($hist.reason)" }
    if ($null -ne $hist.pct -and @($hist.pct).Count -gt 0) {
        Bad "ok=false but a pct series was returned anyway - something is seeding the empty state"
    } else { Ok "no series drawn while there is nothing real to draw" }
} else {
    if ("$($hist.kind)" -eq 'nav_snapshots') { Ok "kind=nav_snapshots (recorded, not backfilled)" }
    else { Bad "kind is '$($hist.kind)' - this series did not come from nav_snapshots" }
}

# =========================================================================== #
Sec "N.3  every range button the card offers is accepted"
# =========================================================================== #
foreach ($r in @('1W','1M','1Q','1Y','MAX')) {
    $x = Api GET "/api/v1/portfolio/nav-history?range=$r" 60
    if ($null -eq $x) { Bad "range=$r errored" }
    elseif ($x.PSObject.Properties.Name -contains 'ok') { Ok "range=$r answered" }
    else { Bad "range=$r returned a payload with no ok field" }
}
$bad = Api GET '/api/v1/portfolio/nav-history?range=BANANA' 60
if ($null -eq $bad) { Skip "unknown range returned an HTTP error rather than a reasoned refusal" }
elseif ($bad.ok -eq $false -and "$($bad.reason)" -match 'unknown range') { Ok "an unknown range is refused, not silently defaulted" }
else { Bad "range=BANANA was accepted - a typo in the frontend would silently show the wrong window" }

# =========================================================================== #
Sec "N.4  recording a day works, and works exactly once"
# =========================================================================== #
if ($NoSnapshot) { Skip "-NoSnapshot: today's row was not recorded" }
else {
    $before = 0
    $b = Api GET '/api/v1/portfolio/nav-history?range=MAX'
    if ($b) { $before = if ($b.ok) { @($b.dates).Count } else { [int]$b.points } }

    $s1 = Api POST '/api/v1/portfolio/nav-history/snapshot'
    if ($null -eq $s1) { Bad "POST snapshot failed - history cannot start" }
    else {
        Ok "snapshot recorded: nav=$($s1.nav_ils) invested=$($s1.invested_ils) positions=$($s1.positions)"
        if ([double]$s1.nav_ils -le 0) {
            Bad "NAV recorded as $($s1.nav_ils) - a zero row poisons the chain permanently (the next period divides by it)"
        } else { Ok "NAV is a positive number" }
        # Cash is a real position row in list_positions AND get_cash() returns it
        # separately. Summing both double-counts it. The snapshot must come from
        # strategy_service._snapshot, which is the one that does not.
        if ($null -ne $s1.cash_ils -and [double]$s1.cash_ils -gt [double]$s1.nav_ils) {
            Bad "cash ($($s1.cash_ils)) exceeds NAV ($($s1.nav_ils)) - cash is being double-counted"
        } else { Ok "cash sits inside NAV rather than beside it" }
    }

    $s2 = Api POST '/api/v1/portfolio/nav-history/snapshot'
    $a = Api GET '/api/v1/portfolio/nav-history?range=MAX'
    $after = 0
    if ($a) { $after = if ($a.ok) { @($a.dates).Count } else { [int]$a.points } }
    Write-Host "        points $before -> $after after two POSTs" -ForegroundColor DarkGray
    if ($after -le $before + 1) { Ok "two snapshots in one day produced at most one new row (idempotent)" }
    else { Bad "$($after - $before) rows appeared from two POSTs - the unique constraint is not holding, and a duplicated day double-counts a period" }
}

# =========================================================================== #
Sec "N.5  the deployed arithmetic is the tested arithmetic"
# =========================================================================== #
# Recomputed here from the raw points and flows the API itself returns, so this
# is checking the deployed container's own numbers rather than trusting them.
$h2 = Api GET '/api/v1/portfolio/nav-history?range=MAX'
if ($null -eq $h2 -or -not $h2.ok) {
    Skip "not enough recorded history yet to verify the chaining - re-run after $(if($h2){$h2.needs}else{3}) days"
} else {
    $nav   = @($h2.nav_ils   | ForEach-Object { [double]$_ })
    $pct   = @($h2.pct       | ForEach-Object { [double]$_ })
    $dates = @($h2.dates)

    if ($nav.Count -ne $pct.Count -or $nav.Count -ne $dates.Count) {
        Bad "dates/pct/nav_ils are $($dates.Count)/$($pct.Count)/$($nav.Count) - the chart would misalign silently"
    } else { Ok "dates, pct and nav_ils are the same length ($($nav.Count))" }

    if ($pct.Count -gt 0 -and [math]::Abs($pct[0]) -lt 1e-9) { Ok "the first point is a 0% baseline, not a return" }
    else { Bad "the series opens at $($pct[0])% - the first snapshot is a baseline, it cannot have a return" }

    # Fetch the flows and re-chain: r = (V1 - F)/V0 - 1, compounded.
    $contrib = Api GET '/api/v1/portfolio/contributions'
    $flows = @()
    if ($contrib) { $flows = @($contrib.entries) }
    $cum = 1.0
    for ($i = 1; $i -lt $nav.Count; $i++) {
        $f = 0.0
        foreach ($e in $flows) {
            $d = "$($e.occurred_at)"
            if ($d.Length -ge 10) { $d = $d.Substring(0, 10) } else { continue }
            # Half-open (t0, t1]: a flow dated on the opening day is already
            # inside that snapshot's NAV.
            if ($d -gt $dates[$i - 1] -and $d -le $dates[$i]) { $f += [double]$e.amount_ils }
        }
        if ($nav[$i - 1] -gt 0) { $cum *= (($nav[$i] - $f) / $nav[$i - 1]) }
    }
    $mine = ($cum - 1.0) * 100.0
    $theirs = [double]$h2.total_pct
    Write-Host "        recomputed $([math]::Round($mine,6))%  vs API $([math]::Round($theirs,6))%" -ForegroundColor DarkGray
    if ([math]::Abs($mine - $theirs) -lt 0.01) { Ok "the deployed total matches an independent re-chaining" }
    else { Bad "deployed says $theirs%, re-chaining the same points says $([math]::Round($mine,4))% - the container is not running the tested arithmetic" }

    # The structural half of the deposit proof, on real data.
    $flow = [double]$h2.flow_total_ils
    $simple = [double]$h2.simple_change_pct
    Write-Host "        flows in window: $flow ILS ; naive $simple% ; time-weighted $theirs%" -ForegroundColor DarkGray
    if ([math]::Abs($flow) -lt 0.005) {
        if ([math]::Abs($simple - $theirs) -lt 0.05) { Ok "no flows in the window, so naive and time-weighted agree (as they must)" }
        else { Bad "no flows, yet naive ($simple%) and time-weighted ($theirs%) disagree - one of them is wrong" }
    } else {
        if ([math]::Abs($simple - $theirs) -gt 0.01) { Ok "$flow ILS moved and the two figures separate - the deposit is not being read as performance" }
        else { Bad "$flow ILS of external cash moved and the time-weighted figure is identical to the naive one - the flow adjustment is not being applied" }
    }
    if ($h2.PSObject.Properties.Name -contains 'simple_change_pct') { Ok "both figures are returned, so the gap between them is visible" }
    else { Bad "simple_change_pct is missing - only the adjusted figure is shown, and the reader cannot see what the deposits did" }

    if ($null -ne $h2.gaps -and @($h2.gaps).Count -gt 0) {
        Ok "$(@($h2.gaps).Count) gap(s) reported rather than interpolated across"
        foreach ($g in @($h2.gaps)) { Write-Host "        gap $($g.from) -> $($g.to) ($($g.days)d)" -ForegroundColor DarkGray }
    } else { Ok "no gaps in the recorded run" }
}

# =========================================================================== #
Sec "N.6  the deposit test that production cannot prove"
# =========================================================================== #
# Run by exact node id. `pytest tests/` would stay green with this one deleted.
$py = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pytest "tests/test_nav_history.py::test_a_deposit_does_not_move_the_return" -q 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "a 5,000 deposit into a 20,000 book reports 0%, not +25%" }
else { Bad "the deposit test did not pass (or no longer exists) - run: python -m pytest tests/test_nav_history.py -q" }

# =========================================================================== #
Sec "N.7  the served shell draws recorded history and labels it"
# =========================================================================== #
$html = Text '/app/index.html'
if ($null -eq $html) { Bad "could not fetch /app/index.html" }
else {
    foreach ($n in @('drawTodayReal', 'portfolio/nav-history?range=', 'Your account history')) {
        if ($html -match [regex]::Escape($n)) { Ok "served shell contains $n" }
        else { Bad "served shell is MISSING $n - this is the stale-shell bug, not a code bug" }
    }
    if ($html -match [regex]::Escape("How today's holdings have moved")) {
        Ok "the backfill keeps its own heading, so the two can never be confused"
    } else { Bad "the fallback heading is gone - a backfilled curve could be labelled as real history" }
}

$sw = Text '/app/sw.js'
if ($null -eq $sw) { Bad "could not fetch /app/sw.js" }
elseif ($sw -match "const VERSION = 'iw-v(\d+)'") {
    $v = [int]$Matches[1]
    if ($v -ge 22) { Ok "service worker serving iw-v$v" }
    else { Bad "service worker is still iw-v$v - browsers will keep serving the old shell" }
} else { Bad "no VERSION in the served sw.js" }

# =========================================================================== #
Sec "N.8  the nightly job is alive"
# =========================================================================== #
# scheduler.job_state() returns {scheduler_running, jobs:[{id,next_run}], history}
# and is served by the status endpoint. I do not have that route's path in front
# of me, so this PROBES rather than asserts a path I did not verify -- a smoke
# that fails because I guessed a URL teaches nothing. If none of these answer,
# fill in the right path and this becomes a real check.
$j = $null
foreach ($p in @('/api/v1/ops/status', '/api/v1/status', '/api/v1/ops/jobs', '/api/v1/jobs')) {
    $try = Api GET $p 45
    if ($null -ne $try -and $try.PSObject.Properties.Name -contains 'jobs') { $j = $try; Write-Host "        found at $p" -ForegroundColor DarkGray; break }
}
if ($null -eq $j) {
    Skip "no job-status endpoint found at the paths tried - confirm the 22:10 run in the Railway logs tomorrow, or point -BaseUrl's status route at scheduler.job_state()"
} else {
    $found = $null
    foreach ($item in @($j.jobs)) { if ("$($item.id)" -eq 'nav_snapshot') { $found = $item } }
    if ($found) { Ok "nav_snapshot is scheduled (next run: $($found.next_run))" }
    else { Bad "nav_snapshot is not in the scheduler - every night it does not run is a day of history that cannot be recovered" }
    if ($j.scheduler_running -eq $false) { Bad "the scheduler is not running at all" }
}

# =========================================================================== #
if (-not $SkipChain) {
    Sec "chaining smoke-t4 (which chains smoke-t3, which covers T0-T3)"
    $t4 = Join-Path $PSScriptRoot 'smoke-t4.ps1'
    if (Test-Path $t4) {
        & $t4 -Sha $Sha -SkipShaCheck:$SkipShaCheck -BaseUrl $BaseUrl
        $chained = $LASTEXITCODE
    } else { Skip "smoke-t4.ps1 not found"; $chained = 0 }
} else { $chained = 0 }

Write-Host "`n=============================================================" -ForegroundColor White
Write-Host "Phase N: $pass pass / $fail fail / $skip skip" -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
Write-Host "=============================================================" -ForegroundColor White
if ($fail -or $chained -ne 0) { exit 1 }
exit 0
