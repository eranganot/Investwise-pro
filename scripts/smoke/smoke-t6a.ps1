# SMOKE - T6a: the split search, and the doubt it is required to carry.
#
#   .\scripts\smoke\smoke-t6a.ps1                # READ-ONLY. Nothing writes.
#   .\scripts\smoke\smoke-t6a.ps1 -SkipChain
#   .\scripts\smoke\smoke-t6a.ps1 -Ceiling 32
#
# READ-ONLY throughout. The split search writes nothing, and this script never
# presses Accept -- Phase A's own smoke covers the write path.
#
# The checks that earn their place are T6a.2 and T6a.3. Anyone can verify a
# number came back; the question that matters is whether the number came back
# WITH the three things that stop it being read as more than it is: an
# out-of-sample ranking, a fit/test gap, and a count of what was searched.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$SkipChain,
    [double]$Ceiling = 32.0,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set." -ForegroundColor Red; exit 1
}
$ApiHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
$pass = 0; $fail = 0; $skip = 0
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }
function Api($method, $path, $tmo = 600) {
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
function BookPrint {
    $s = Api GET '/api/v1/plan/sleeves' 60
    if ($null -eq $s) { return $null }
    return (@($s.sleeves | Sort-Object strategy_id | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ';')
}

Write-Host "Smoke: T6a the split search  ($BaseUrl)" -ForegroundColor White
Write-Host "READ-ONLY. This never presses Accept." -ForegroundColor Yellow

Sec "T6a.0  am I talking to the new container?"
if ($SkipShaCheck) { Skip "SHA check skipped" }
else {
    if (-not $Sha) { try { $Sha = (git rev-parse --short HEAD).Trim() } catch { $Sha = '' } }
    $h = Api GET '/health' 60
    if ($null -eq $h) { Bad "/health unreachable - stopping"; exit 1 }
    $live = "$($h.commit)"
    if (-not $Sha) { Skip "no local SHA; deployed is $live" }
    elseif ($live.StartsWith($Sha) -or $Sha.StartsWith($live)) { Ok "deployed commit is $live" }
    else { Bad "deployed is $live, you are smoking $Sha"; exit 1 }
}
$book0 = BookPrint
Write-Host "        book before: $book0" -ForegroundColor DarkGray

Sec "T6a.1  the search runs and refuses the half-question"
$noCeil = Api GET '/api/v1/plan/target/split?max_drawdown_pct=0' 60
if ($null -eq $noCeil) { Skip "a zero ceiling returned an HTTP error rather than a reasoned refusal" }
elseif ($noCeil.ok -eq $false -and "$($noCeil.reason)" -eq 'NO_CEILING') { Ok "a zero ceiling is refused, not solved" }
else { Bad "a zero ceiling was accepted - the answer is then the most leveraged split allowed" }

Write-Host "        searching (two simulations per grid point; this is slow)..." -ForegroundColor DarkGray
$sw = [Diagnostics.Stopwatch]::StartNew()
$s = Api GET "/api/v1/plan/target/split?max_drawdown_pct=$Ceiling"
$sw.Stop()
Write-Host "        took $([math]::Round($sw.Elapsed.TotalSeconds,1))s" -ForegroundColor DarkGray
if ($null -eq $s) { Bad "the split search failed"; }
elseif (-not $s.ok) {
    if ("$($s.reason)" -in @('NO_SLEEVES','NO_OOS_WINDOW','nothing admissible')) {
        Skip "the search abstained for a stated reason: $($s.reason) - $($s.detail)"
    } else { Bad "the search failed: $($s.reason) $($s.detail)" }
} else {
    Ok "best split: $(($s.best.split_pct -join ' / '))% -> oos $($s.best.oos_excess_pct)%/yr, dd $($s.best.max_drawdown_pct)%"
}

Sec "T6a.2  the answer is ranked out-of-sample, and says which window"
if ($null -eq $s -or -not $s.ok) { Skip "no result to check" }
else {
    if ("$($s.ranked_on)" -eq 'oos_excess_pct') { Ok "ranked on the out-of-sample figure" }
    else { Bad "ranked on '$($s.ranked_on)' - the winner was chosen on the data it is scored on" }
    if ("$($s.ranked_on_note)" -match 'sample of one') { Ok "the one-bear-market caveat travels with it" }
    else { Bad "the OOS caveat is missing - 'out of sample' is borrowing authority the window has not earned" }
    if ("$($s.oos_split)" -eq '2022-01-01') { Ok "the boundary is backtest_service's OOS_SPLIT" }
    else { Bad "the boundary is '$($s.oos_split)', not the constant that already exists" }
}

Sec "T6a.3  it carries its own doubt"
if ($null -eq $s -or -not $s.ok) { Skip "no result to check" }
else {
    $cov = $s.coverage
    if ($cov.coarse_points -gt 0) { Ok "searched $($cov.coarse_points) splits on a $($cov.step_pct)-point grid ($($cov.measured_points) measured, $($cov.admissible_points) admissible)" }
    else { Bad "no searched-count reported - a winner from 55 candidates is a different claim from a winner from 6" }
    if ("$($cov.method)" -match 'not a proven global optimum') { Ok "the payload declines to call it a global optimum" }
    else { Bad "the payload does not say the result is only the best point REACHED" }
    if ($cov.step_was_coarsened) { Write-Host "        NOTE: the grid was coarsened to fit the budget" -ForegroundColor Yellow }

    $sp = $s.spread
    if ($null -ne $sp.best_minus_median_pct) {
        Ok "spread: best $($sp.best_pct)%, median $($sp.median_pct)%, worst $($sp.worst_pct)%"
        if ($sp.is_noise) { Write-Host "        NOTE: best-minus-median $($sp.best_minus_median_pct) is inside the noise floor - the 'optimum' is a coin landing" -ForegroundColor Yellow }
    } else { Bad "no spread reported - the winner cannot be read without knowing what it beat" }

    $ft = $s.fit_test
    if ($ft.ok) {
        Ok "fit/test: winner gives up $($ft.winner_gap_pct) points between windows, typical candidate $($ft.median_gap_pct)"
        if ($ft.winner_decays_more_than_typical) { Write-Host "        NOTE: $($ft.note)" -ForegroundColor Yellow }
    } else { Bad "no fit/test gap - the plan calls this the most useful row on the screen" }

    $rows = @($s.top)
    if ($rows.Count -gt 0) {
        $missing = @($rows | Where-Object { $null -eq $_.gap_pct })
        if ($missing.Count -eq 0) { Ok "all $($rows.Count) ranked rows carry a fit/test gap" }
        else { Bad "$($missing.Count) ranked row(s) have no gap - the plan requires it on EVERY row" }
        $vals = @($rows | ForEach-Object { [double]$_.oos_excess_pct })
        $sorted = @($vals | Sort-Object -Descending)
        if (($vals -join ',') -eq ($sorted -join ',')) { Ok "the ranked list is ordered best-first out-of-sample" }
        else { Bad "the ranked list is not in out-of-sample order" }
    } else { Bad "no ranked rows returned" }
}

Sec "T6a.4  the winner is verified over the WHOLE window, not just the halves"
if ($null -eq $s -or -not $s.ok) { Skip "no result" }
else {
    # The sweep's ceiling test uses the worse half-window, which understates a
    # fall spanning the split. Without this re-measurement the card could show a
    # split that breaches the ceiling on the real history.
    if ($null -eq $s.verified_in_full) { Bad "the winner was never re-measured over the full window" }
    elseif ($s.verified_in_full) {
        Ok "the winner re-measured inside the $Ceiling% ceiling over the full history"
        if ($s.full_window -and $null -ne $s.full_window.max_drawdown_pct) {
            $fullDd = [double]$s.full_window.max_drawdown_pct
            Write-Host "        full-window drawdown $fullDd% vs sweep's $($s.best.max_drawdown_pct)%" -ForegroundColor DarkGray
            if ($fullDd -ge [double]$s.best.max_drawdown_pct - 0.01) { Ok "the sweep's figure was a lower bound, as declared" }
            else { Bad "the full window measured LESS drawdown than the halves - one of the two is wrong" }
        }
    } else {
        Ok "the winner did NOT verify in full, and the payload says so: $($s.warning)"
    }
}

Sec "T6a.5  the served card shows the search, and gates the button"
$html = Text '/app/index.html'
if ($null -eq $html) { Bad "could not fetch /app/index.html" }
else {
    foreach ($n in @('tgtSplit', '_tgtSplitBody', 'tgtAcceptSplit', 'plan/target/split')) {
        if ($html -match [regex]::Escape($n)) { Ok "served shell contains $n" }
        else { Bad "served shell is MISSING $n" }
    }
    if ($html -match [regex]::Escape('const ok=s.verified_in_full && !(sp.is_noise);')) {
        Ok "the served Accept is gated on verification AND the noise floor"
    } else { Bad "the served split Accept is not gated - the plan forbids auto-apply from a ranked result" }
    if ($html -match '(?s)async function tgtAcceptSplit\(\)\{(.*?)\n\}') {
        if ($Matches[1] -match 'fetch\(') { Bad "the served tgtAcceptSplit fetches directly - Phase A's staleness and confirm guards are bypassed" }
        else { Ok "the served split Accept reuses Phase A rather than opening a second write path" }
    } else { Bad "cannot find tgtAcceptSplit in the served shell" }
}
$sw2 = Text '/app/sw.js'
if ($sw2 -match "const VERSION = 'iw-v(\d+)'") {
    if ([int]$Matches[1] -ge 25) { Ok "service worker serving iw-v$($Matches[1])" }
    else { Bad "service worker is still iw-v$($Matches[1])" }
} else { Bad "no VERSION in the served sw.js" }

Sec "T6a.6  the book is exactly as this run found it"
$bookEnd = BookPrint
if ($bookEnd -eq $book0) { Ok "book unchanged: $bookEnd" }
else { Bad "THIS RUN CHANGED YOUR BOOK. was [$book0] now [$bookEnd]" }

if (-not $SkipChain) {
    Sec "chaining smoke-a (which chains t5 -> n -> t4 -> t3 -> T0-T3)"
    $a = Join-Path $PSScriptRoot 'smoke-a.ps1'
    if (Test-Path $a) { & $a -Sha $Sha -SkipShaCheck:$SkipShaCheck -BaseUrl $BaseUrl; $chained = $LASTEXITCODE }
    else { Skip "smoke-a.ps1 not found"; $chained = 0 }
} else { $chained = 0 }

Write-Host "`n=============================================================" -ForegroundColor White
Write-Host "T6a: $pass pass / $fail fail / $skip skip" -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
Write-Host "=============================================================" -ForegroundColor White
if ($fail -or $chained -ne 0) { exit 1 }
exit 0
