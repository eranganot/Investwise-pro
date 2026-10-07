# SMOKE - Phase A + N2, against the deployed app.
#
#   .\scripts\smoke\smoke-a.ps1                    # SAFE. Nothing writes your book.
#   .\scripts\smoke\smoke-a.ps1 -ApplyRoundTrip    # apply, verify, undo, verify
#   .\scripts\smoke\smoke-a.ps1 -SkipChain
#
# READ-ONLY BY DEFAULT, and that default is deliberate. Phase A writes the user's
# plan. A smoke that silently resized his sleeves to prove it could would be the
# exact behaviour this phase is built to make impossible.
#
# What runs without -ApplyRoundTrip still proves the refusals, which is the half
# that matters most: a bare POST is refused, and a plan solved against a book
# that has moved is refused. Both are checked by SENDING them and confirming the
# sleeves are byte-identical afterwards -- a refusal that is only claimed in a
# response body has not been shown to have written nothing.
#
# -ApplyRoundTrip does the full loop and restores. If it fails midway your
# sleeves may be left changed; the run prints the before-state first so you can
# always put it back by hand.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$SkipChain,
    [switch]$ApplyRoundTrip,
    [double]$Excess = 5.0,
    [double]$Ceiling = 32.0,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'

if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor Gray
    exit 1
}
$ApiHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
$JsonHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY; 'Content-Type' = 'application/json' }

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
function Post($path, $bodyObj, $tmo = 240) {
    try {
        $json = ($bodyObj | ConvertTo-Json -Depth 8 -Compress)
        return Invoke-RestMethod -Method POST -Uri "$BaseUrl$path" -Headers $JsonHeaders -Body $json -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        POST $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}
function Text($path) {
    try { return (Invoke-WebRequest -Uri "$BaseUrl$path" -Headers @{ 'Cache-Control' = 'no-cache' } -TimeoutSec 90 -UseBasicParsing).Content }
    catch { return $null }
}
# The sleeve book as one comparable string. "byte-identical" has to be a thing
# the script can actually check, not a thing it asserts.
function BookPrint {
    $s = Api GET '/api/v1/plan/sleeves' 60
    if ($null -eq $s) { return $null }
    return (@($s.sleeves | Sort-Object strategy_id | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ';')
}

Write-Host "Smoke: Phase A (accept) + N2 (no reconstruction)  ($BaseUrl)" -ForegroundColor White
if (-not $ApplyRoundTrip) { Write-Host "READ-ONLY MODE - your book will not be written. Use -ApplyRoundTrip for the full loop." -ForegroundColor Yellow }

# =========================================================================== #
Sec "A.0  am I talking to the new container?"
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

$book0 = BookPrint
if ($null -eq $book0) { Bad "cannot read /plan/sleeves - stopping"; exit 1 }
Write-Host "        book before: $book0" -ForegroundColor DarkGray

# =========================================================================== #
Sec "A.1  the table exists and the audit endpoint answers"
# =========================================================================== #
$hist = Api GET '/api/v1/plan/target/applications'
if ($null -eq $hist) { Bad "GET /plan/target/applications failed - if 500, 0017 did not run" }
else { Ok "applications log responds ($($hist.count) entr(y/ies) recorded)" }

# =========================================================================== #
Sec "A.2  a bare POST does not write"
# =========================================================================== #
# Sent for real, then the book is re-read. A refusal claimed in a response body
# is not a refusal demonstrated.
$solve = Api GET "/api/v1/plan/target?excess_pct=$Excess&max_drawdown_pct=$Ceiling"
if ($null -eq $solve) { Bad "the solve failed - cannot build an apply payload"; }
elseif ($null -eq $solve.would_execute -or $null -eq $solve.would_execute.resizes) {
    Skip "this solve produced no resizes to apply (outcome $($solve.outcome))"
} else {
    $resizes = @($solve.would_execute.resizes)
    Write-Host "        solve: $($solve.outcome), $($resizes.Count) resize(s)" -ForegroundColor DarkGray
    foreach ($r in $resizes) { Write-Host "          $($r.strategy_id): $($r.from_pct)% -> $($r.to_pct)%" -ForegroundColor DarkGray }

    $noConfirm = Post '/api/v1/plan/target/apply' @{ resizes = $resizes }
    if ($null -eq $noConfirm) { Bad "apply-without-confirm errored instead of refusing with a reason" }
    elseif ($noConfirm.ok -eq $false -and "$($noConfirm.reason)" -match 'not confirmed') {
        Ok "a POST without confirm=true is refused"
    } else { Bad "a bare POST returned ok=$($noConfirm.ok) - the write is not gated" }

    $bookNow = BookPrint
    if ($bookNow -eq $book0) { Ok "and the book is unchanged: $bookNow" }
    else { Bad "THE BOOK CHANGED on an unconfirmed POST. was [$book0] now [$bookNow]" }
}

# =========================================================================== #
Sec "A.3  a plan solved against a different book is refused"
# =========================================================================== #
# The quietest failure this phase can have. A from_pct that does not match the
# live row means the recommendation describes a book that no longer exists, so
# applying it is not "accepting" -- it is overwriting with someone else's answer.
if ($null -eq $solve -or $null -eq $solve.would_execute.resizes) { Skip "no resizes to mangle" }
else {
    $stale = @($solve.would_execute.resizes | ForEach-Object {
        @{ strategy_id = $_.strategy_id; from_pct = ([double]$_.from_pct + 7.5); to_pct = $_.to_pct } })
    $r = Post '/api/v1/plan/target/apply?confirm=true' @{ resizes = $stale }
    if ($null -eq $r) { Bad "the stale apply errored instead of refusing with a reason" }
    elseif ($r.ok -eq $false -and "$($r.reason)" -match 'changed since') {
        Ok "a stale plan is refused, and it names the drift"
        foreach ($s in @($r.stale)) { Write-Host "          $($s.strategy_id): solved against $($s.solved_against_pct)%, book holds $($s.book_now_pct)%" -ForegroundColor DarkGray }
    } else { Bad "a stale plan returned ok=$($r.ok) reason=$($r.reason) - the premise is not checked" }

    $bookNow = BookPrint
    if ($bookNow -eq $book0) { Ok "and the book is unchanged" }
    else { Bad "THE BOOK CHANGED on a refused stale apply. was [$book0] now [$bookNow]" }
}

# =========================================================================== #
Sec "A.4  a plan that does not fit is refused, and writes nothing"
# =========================================================================== #
if ($null -eq $solve -or $null -eq $solve.would_execute.resizes) { Skip "no resizes to inflate" }
else {
    $tooBig = @($solve.would_execute.resizes | ForEach-Object {
        @{ strategy_id = $_.strategy_id; from_pct = $_.from_pct; to_pct = 80.0 } })
    $r = Post '/api/v1/plan/target/apply?confirm=true' @{ resizes = $tooBig }
    if ($null -eq $r) { Bad "the over-allocated apply errored instead of refusing" }
    elseif ($r.ok -eq $false) { Ok "an over-allocated plan is refused: $($r.reason)" }
    else { Bad "sleeves totalling 160% were accepted" }

    $bookNow = BookPrint
    if ($bookNow -eq $book0) { Ok "and the book is unchanged" }
    else { Bad "THE BOOK CHANGED on a refused over-allocation. was [$book0] now [$bookNow]" }
}

# =========================================================================== #
Sec "A.5  the full accept -> verify -> undo -> verify loop"
# =========================================================================== #
if (-not $ApplyRoundTrip) {
    Skip "-ApplyRoundTrip not set; the write path was not exercised end to end"
} elseif ($null -eq $solve -or $null -eq $solve.would_execute.resizes) {
    Skip "no resizes to apply"
} else {
    Write-Host "        RESTORE POINT: $book0" -ForegroundColor Yellow
    $resizes = @($solve.would_execute.resizes)
    $applied = Post '/api/v1/plan/target/apply?confirm=true' @{
        resizes = $resizes; context = @{ smoke = $true; excess_pct = $Excess; ceiling_pct = $Ceiling } }
    if ($null -eq $applied -or -not $applied.ok) {
        Bad "the apply failed: $($applied.reason) $($applied.detail)"
    } else {
        Ok "applied: sleeves now $($applied.allocated_pct)%, core $($applied.core_pct)%"
        # What was WRITTEN must equal what was SOLVED. This is the whole point of
        # the phase -- an accept that lands on different numbers than the card
        # showed is worse than no button.
        $bad = 0
        $after = BookPrint
        foreach ($r in $resizes) {
            $want = [math]::Round([double]$r.to_pct, 2)
            $got = $applied.after."$($r.strategy_id)"
            if ($null -eq $got) { $bad++; Write-Host "          $($r.strategy_id) missing from after-state" -ForegroundColor Red }
            elseif ([math]::Abs([double]$got - $want) -gt 0.05) {
                $bad++; Write-Host "          $($r.strategy_id): solved $want%, book holds $got%" -ForegroundColor Red
            }
        }
        if ($bad -eq 0) { Ok "every written size equals the size that was solved" }
        else { Bad "$bad sleeve(s) landed on a size the solve did not produce" }
        if ("$($applied.note)" -match 'No brokerage order') { Ok "the response states no order was placed" }
        else { Bad "the success response does not state that no order was placed" }
        Write-Host "        book after apply: $after" -ForegroundColor DarkGray

        $undone = Post '/api/v1/plan/target/undo?confirm=true' @{}
        if ($null -eq $undone -or -not $undone.ok) { Bad "UNDO FAILED: $($undone.reason). Restore by hand from $book0" }
        else {
            $restored = BookPrint
            if ($restored -eq $book0) { Ok "undo restored the book exactly: $restored" }
            else { Bad "undo left the book at [$restored], not [$book0]" }
        }
        $log = Api GET '/api/v1/plan/target/applications'
        if ($log -and $log.count -ge 2) { Ok "both the apply and the undo are recorded ($($log.count) entries)" }
        else { Bad "the audit log does not carry both operations" }
    }
}

# =========================================================================== #
Sec "N2  the served Today card no longer reconstructs"
# =========================================================================== #
$html = Text '/app/index.html'
if ($null -eq $html) { Bad "could not fetch /app/index.html" }
else {
    if ($html -match [regex]::Escape('portfolio/performance?range=${r}')) {
        Bad "the served Today chart STILL fetches the backfill - stale shell or N2 did not ship"
    } else { Ok "the served Today card does not fetch the backfill" }
    foreach ($n in @('startRecording', 'cannot be backfilled', 'tgtAccept', 'tgtUndo')) {
        if ($html -match [regex]::Escape($n)) { Ok "served shell contains $n" }
        else { Bad "served shell is MISSING $n" }
    }
    if ($html -match [regex]::Escape('No brokerage order is placed')) {
        Ok "the served confirm dialog states that no order is placed"
    } else { Bad "the served confirm dialog does not say it places no order" }
    if ($html -match 'function loadPerf') { Ok "the Performance tab keeps its reconstruction" }
    else { Bad "loadPerf is gone - N2 was over-applied" }
}

$sw = Text '/app/sw.js'
if ($null -eq $sw) { Bad "could not fetch /app/sw.js" }
elseif ($sw -match "const VERSION = 'iw-v(\d+)'") {
    $v = [int]$Matches[1]
    if ($v -ge 24) { Ok "service worker serving iw-v$v" }
    else { Bad "service worker is still iw-v$v - browsers keep serving the old shell" }
} else { Bad "no VERSION in the served sw.js" }

# =========================================================================== #
Sec "A.6  the book is exactly as this run found it"
# =========================================================================== #
$bookEnd = BookPrint
if ($bookEnd -eq $book0) { Ok "book unchanged across the whole run: $bookEnd" }
else { Bad "THIS RUN LEFT YOUR BOOK CHANGED. was [$book0] now [$bookEnd]" }

# =========================================================================== #
if (-not $SkipChain) {
    Sec "chaining smoke-t5 (which chains n -> t4 -> t3 -> T0-T3)"
    $t5 = Join-Path $PSScriptRoot 'smoke-t5.ps1'
    if (Test-Path $t5) {
        & $t5 -Sha $Sha -SkipShaCheck:$SkipShaCheck -BaseUrl $BaseUrl
        $chained = $LASTEXITCODE
    } else { Skip "smoke-t5.ps1 not found"; $chained = 0 }
} else { $chained = 0 }

Write-Host "`n=============================================================" -ForegroundColor White
Write-Host "Phase A + N2: $pass pass / $fail fail / $skip skip" -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
Write-Host "=============================================================" -ForegroundColor White
if ($fail -or $chained -ne 0) { exit 1 }
exit 0
