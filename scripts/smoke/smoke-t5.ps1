# SMOKE - T5: the deployed card gives an instruction, and the instruction is
# consistent with the figures printed above it.
#
#   .\scripts\smoke\smoke-t5.ps1                 # T5, then chains smoke-n
#   .\scripts\smoke\smoke-t5.ps1 -SkipChain      # T5 only
#   .\scripts\smoke\smoke-t5.ps1 -SkipShaCheck
#
# READ-ONLY. Nothing here writes.
#
# The check that earns its place is T5.3: it re-derives the recommendation from
# the solve's own numbers and requires the deployed text to agree. A card that
# prints "raise the ceiling to 32%" beside a measured 41% fall is worse than a
# card with no instruction -- it is confidently wrong, and nothing else in this
# repo would catch it.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$SkipChain,
    [double]$Excess = 0.0,
    [double]$Ceiling = 50.0,
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

function Api($method, $path, $tmo = 240) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}
function Text($path) {
    try { return (Invoke-WebRequest -Uri "$BaseUrl$path" -Headers @{ 'Cache-Control' = 'no-cache' } -TimeoutSec 90 -UseBasicParsing).Content }
    catch { return $null }
}

Write-Host "Smoke: T5 the instruction  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "T5.0  am I talking to the new container?"
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
Sec "T5.1  the two windows now agree"
# =========================================================================== #
# The bug T5 fixes: the ceiling was seeded from 252 days and judged against
# ~1729 sessions. Both numbers are fetched here and compared directly, because
# a window mismatch is invisible on the card and explains the whole symptom.
$perf = Api POST '/api/v1/portfolio/performance?range=MAX'
$solve = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$Ceiling"

if ($null -eq $perf -or -not $perf.ok) { Bad "performance?range=MAX did not return a measurement" }
elseif ($null -eq $solve) { Bad "the solve failed" }
else {
    $seedObs = [int]$perf.observations
    $solveObs = if ($solve.measured) { [int]$solve.measured.observations } else { 0 }
    Write-Host "        seed window $seedObs sessions ; solve window $solveObs sessions" -ForegroundColor DarkGray
    if ($solveObs -eq 0) { Skip "the solve reported no measurement to compare against" }
    elseif ([math]::Abs($seedObs - $solveObs) -le [math]::Max(60, $solveObs * 0.1)) {
        Ok "the seed and the solve measure the same window (within 10%)"
    } else {
        Bad "seed measures $seedObs sessions, the solver measures $solveObs - a ceiling seeded from one and judged against the other comes back breached for a reason that has nothing to do with the book"
    }
    # 252 is the old default. If the seed is still landing there, range=MAX is
    # not reaching the server whatever index.html says.
    if ($seedObs -le 260 -and $solveObs -gt 400) { Bad "the seed window is still ~252 sessions - range=MAX is not being applied" }
    else { Ok "the seed is not stuck on the 252-day default" }
}

# =========================================================================== #
Sec "T5.2  every solve comes back with an instruction"
# =========================================================================== #
if ($null -eq $solve) { Skip "no solve" }
else {
    $r = $solve.recommendation
    if ($null -eq $r) { Bad "the solve returned no recommendation - this is the whole of T5" }
    else {
        Write-Host "        outcome  : $($solve.outcome)" -ForegroundColor DarkGray
        Write-Host "        headline : $($r.headline)" -ForegroundColor Gray
        Write-Host "        because  : $($r.because)" -ForegroundColor DarkGray
        if ("$($r.headline)".Trim()) { Ok "there is a headline" } else { Bad "the headline is blank" }
        if ("$($r.because)".Trim())  { Ok "there is a reason behind it" } else { Bad "the reason is blank" }
        if (@('ok','warn','bad') -contains "$($r.severity)") { Ok "severity is $($r.severity)" }
        else { Bad "severity is '$($r.severity)'" }
        foreach ($a in @($r.actions)) { Write-Host "        action   : [$($a.kind)] $($a.label)" -ForegroundColor DarkGray }
    }
}

# =========================================================================== #
Sec "T5.3  the instruction agrees with the figures above it"
# =========================================================================== #
if ($null -eq $solve -or $null -eq $solve.recommendation) { Skip "no recommendation to check" }
else {
    $r = $solve.recommendation
    $acts = @($r.actions)
    $ceilAct = $acts | Where-Object { $_.kind -eq 'set_ceiling' } | Select-Object -First 1

    if ("$($solve.outcome)" -eq 'DRAWDOWN_BOUND') {
        # The number that must be cleared: if the core alone breaches, it is the
        # zero-sleeve drawdown; otherwise it is what the target would require.
        $breaches = $solve.floor -and $solve.floor.breaches_ceiling
        $mustClear = if ($breaches) { [double]$solve.floor.max_drawdown_pct }
                     else { [double]$solve.binding_constraint.would_require_pct }
        if ($null -eq $ceilAct) { Bad "DRAWDOWN_BOUND with no set_ceiling action - the one lever that unblocks it is not offered" }
        elseif ([double]$ceilAct.value -ge $mustClear) {
            Ok "the ceiling offered ($($ceilAct.value)%) clears the $mustClear% it has to"
        } else {
            Bad "it offers a $($ceilAct.value)% ceiling against a $mustClear% measured fall - re-solving at that ceiling would come straight back as DRAWDOWN_BOUND"
        }

        if ($breaches) {
            # THE case on the card today. Sleeve advice here points at a control
            # that provably cannot move the outcome.
            $blob = "$($r.headline) $($r.because)"
            if ($acts | Where-Object { $_.kind -eq 'set_sleeves' }) {
                Bad "the core breaches the ceiling with no sleeve at all, and it still offers a sleeve resize"
            } else { Ok "no sleeve action offered - correct, the core is what binds" }
            if ($blob -match 'sleeves are not the problem') { Ok "it says plainly that the sleeves are not the problem" }
            else { Bad "it does not name the core as the constraint: '$($r.headline)'" }
        }

        if ($ceilAct) {
            $d = "$($ceilAct.detail)"
            if ($d -match 'permission, not a return') { Ok "the raised ceiling is described as a permission, not a return" }
            else { Bad "the ceiling action does not say a ceiling is a permission - this is the line that stops it reading as free return" }
        }
    }
    elseif ("$($solve.outcome)" -eq 'REACHED') {
        $sz = $acts | Where-Object { $_.kind -eq 'set_sleeves' } | Select-Object -First 1
        if ($null -eq $sz) { Bad "REACHED with no size to act on" }
        elseif ([double]$sz.value -eq [double]$solve.display_total_sleeve_pct) {
            Ok "the size instructed ($($sz.value)%) is the size the card displays"
        } else {
            Bad "it says $($sz.value)% while the card shows $($solve.display_total_sleeve_pct)% - two numbers for one decision"
        }
        if ("$($sz.detail)" -match 'never places an order') { Ok "the action states the app places no order" }
        else { Bad "the size action does not carry the no-order statement" }
    }
    else { Skip "outcome is $($solve.outcome) - the ceiling/size cross-checks do not apply" }

    # Applies to every outcome: the equal-risk finding outranks the solve.
    $eq = if ($solve.measured) { $solve.measured.excess_at_equal_risk_pct } else { $null }
    if ($null -eq $eq) { Skip "no equal-risk figure in this solve" }
    elseif ([double]$eq -lt 0) {
        if ("$($r.equal_risk_warning)".Trim()) { Ok "behind at equal risk ($eq%/yr) and the card says so above the instruction" }
        else { Bad "excess at equal risk is $eq%/yr and no warning was raised - that is the finding that outranks the whole solve" }
    } else {
        if ("$($r.equal_risk_warning)".Trim()) { Bad "equal-risk excess is $eq%/yr (not negative) yet a warning fired" }
        else { Ok "equal-risk excess is $eq%/yr, no warning - correct" }
    }
}

# =========================================================================== #
Sec "T5.4  a re-solve at the offered ceiling actually gets somewhere"
# =========================================================================== #
# The one-tap is only worth a button if pressing it changes the answer. This is
# the read-only equivalent of pressing it.
if ($null -eq $solve -or "$($solve.outcome)" -ne 'DRAWDOWN_BOUND') { Skip "not drawdown-bound; nothing to re-solve" }
else {
    $ceilAct = @($solve.recommendation.actions) | Where-Object { $_.kind -eq 'set_ceiling' } | Select-Object -First 1
    if ($null -eq $ceilAct) { Skip "no ceiling offered" }
    else {
        $again = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$($ceilAct.value)"
        if ($null -eq $again) { Bad "the re-solve failed" }
        else {
            Write-Host "        re-solved at $($ceilAct.value)% -> $($again.outcome)" -ForegroundColor DarkGray
            if ("$($again.outcome)" -eq 'DRAWDOWN_BOUND' -and $again.floor -and $again.floor.breaches_ceiling) {
                Bad "re-solving at the ceiling it recommended STILL breaches - the recommended value is below what the core does"
            } else { Ok "the recommended ceiling moves the answer off DRAWDOWN_BOUND-by-floor" }
            if ($again.recommendation) { Ok "the re-solve also carries an instruction" }
            else { Bad "the re-solve lost its recommendation" }
        }
    }
}

# =========================================================================== #
Sec "T5.5  the SERVED shell renders it, and stays read-only"
# =========================================================================== #
$html = Text '/app/index.html'
if ($null -eq $html) { Bad "could not fetch /app/index.html" }
else {
    foreach ($n in @('_tgtRecommendation', 'tgtApply', 'WHAT TO DO', 'equal_risk_warning', 'range=MAX')) {
        if ($html -match [regex]::Escape($n)) { Ok "served shell contains $n" }
        else { Bad "served shell is MISSING $n - stale shell, not a code bug" }
    }
    if ($html -match '(?s)function tgtApply\(kind,value\)\{(.*?)\n\}') {
        $body = $Matches[1]
        if ($body -match 'method:"(POST|PUT|DELETE|PATCH)"') { Bad "the served tgtApply issues a write - Phase T is read-only" }
        else { Ok "the served one-tap writes nothing" }
        if ($body -match 'set_sleeves') { Bad "the served tgtApply handles set_sleeves - that is Phase A" }
        else { Ok "set_sleeves stays inert in the served shell" }
    } else { Bad "cannot find tgtApply in the served shell" }
}

$sw = Text '/app/sw.js'
if ($null -eq $sw) { Bad "could not fetch /app/sw.js" }
elseif ($sw -match "const VERSION = 'iw-v(\d+)'") {
    $v = [int]$Matches[1]
    if ($v -ge 23) { Ok "service worker serving iw-v$v" }
    else { Bad "service worker is still iw-v$v - browsers keep serving the old shell" }
} else { Bad "no VERSION in the served sw.js" }

# =========================================================================== #
if (-not $SkipChain) {
    Sec "chaining smoke-n (which chains t4 -> t3 -> T0-T3)"
    $n = Join-Path $PSScriptRoot 'smoke-n.ps1'
    if (Test-Path $n) {
        & $n -Sha $Sha -SkipShaCheck:$SkipShaCheck -BaseUrl $BaseUrl
        $chained = $LASTEXITCODE
    } else { Skip "smoke-n.ps1 not found"; $chained = 0 }
} else { $chained = 0 }

Write-Host "`n=============================================================" -ForegroundColor White
Write-Host "T5: $pass pass / $fail fail / $skip skip" -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
Write-Host "=============================================================" -ForegroundColor White
if ($fail -or $chained -ne 0) { exit 1 }
exit 0
