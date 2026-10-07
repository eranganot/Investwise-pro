# FIX - T4 follow-up: the chart says what it is, and stops comparing to SPY.
#
#   .\scripts\fix-t4.ps1              # verify only
#   .\scripts\fix-t4.ps1 -Ship        # verify, then commit + push if green
#
# T4 itself is already shipped (10d8b40, 6edebc5). This is the two refinements
# made after it: the Today chart's heading was claiming to be account history,
# and the benchmark line came out. Do NOT re-run ship-t4.ps1 for this -- its
# commit message describes work that is already committed.

param([switch]$Ship)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$Py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$results = [ordered]@{}
function Step([string]$name, [scriptblock]$body) {
    Write-Host "`n--- $name" -ForegroundColor Cyan
    try {
        & $body
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
        $script:results[$name] = "PASS"
        Write-Host "    PASS" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL"
        Write-Host "    FAIL: $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

Step "the file is whole" {
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2280) { throw "only $lines lines" }
    if ((Get-Content app\static_app\index.html -Tail 1).Trim() -ne '</html>') { throw "no </html>" }
    $global:LASTEXITCODE = 0
}

Step "this is a small edit on top of a shipped T4" {
    $stat = git diff --numstat HEAD -- app/static_app/index.html
    if (-not $stat) { throw "nothing to commit - already done?" }
    $p = ($stat -split "`t"); $added = [int]$p[0]; $removed = [int]$p[1]
    Write-Host "        +$added / -$removed vs HEAD" -ForegroundColor DarkGray
    if ($removed -gt 3 * [math]::Max($added, 1)) { throw "that shape is a rewrite, not an edit" }
    $global:LASTEXITCODE = 0
}

Step "the inline script parses" {
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>')
    if ($m.Count -ne 1) { throw "expected 1 inline script block, found $($m.Count)" }
    $tmp = Join-Path $root "_t4fix_check.js"
    [IO.File]::WriteAllText($tmp, $m[0].Groups[1].Value, (New-Object Text.UTF8Encoding($false)))
    try {
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "node is not on PATH" }
        node --check $tmp
        if ($LASTEXITCODE -ne 0) { throw "node --check failed" }
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
    $global:LASTEXITCODE = 0
}

Step "the benchmark is gone from the Today chart, and still present on Explore" {
    $raw = Get-Content app\static_app\index.html -Raw
    $i = $raw.IndexOf('async function loadTodayPerf()')
    $j = $raw.IndexOf('async function loadToday()', $i)
    if ($i -lt 0 -or $j -lt 0) { throw "could not isolate loadTodayPerf" }
    if ($raw.Substring($i, $j - $i) -match 'benchmark_index') {
        throw "the Today chart still plots the benchmark"
    }
    # It must NOT have been removed everywhere: Explore compares on purpose.
    if ($raw -notmatch 'benchmark_index') { throw "benchmark_index is gone from the whole file - Explore needs it" }
    $global:LASTEXITCODE = 0
}

Step "the heading no longer claims to be account history" {
    $raw = Get-Content app\static_app\index.html -Raw
    if ($raw -match '<b>Your portfolio over time</b>') {
        throw "the heading still says 'Your portfolio over time' - it is a backfill, not your history"
    }
    if ($raw -notmatch 'This is not your account history') {
        throw "the caption no longer carries the disclaimer"
    }
    $global:LASTEXITCODE = 0
}

Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

Write-Host "`n================ T4 FIX ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-56} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

Write-Host "`n--- committing" -ForegroundColor Cyan
$files = @("app/static_app/index.html", "scripts/ship-t4.ps1", "scripts/fix-t4.ps1",
           "BEAT_MARKET_TARGET_SOLVER_PLAN.md")
git add $files
git diff --cached --name-only

$msg = @'
fix(t4): the Today chart stops claiming to be your account history

The HEADING was the false claim, not the caption. "Your portfolio over
time" reads as what your money did. The chart is the holdings you own
TODAY priced back through their own past -- a reconstruction. It now says
"How today's holdings have moved", and the caption leads with "This is not
your account history", notes it will not match your total gain, and says it
does not know about deposits.

That last part matters: a deposit moves the book without being performance,
and this series has no way to tell the difference. Real account history is
Phase N -- a nav_snapshots table, a daily job, and a time-weighted return
computed across the contributions ledger so a bank transfer cannot render
as a good day. It cannot be backfilled: grep for `Transaction(` across app/
outside the models returns ZERO hits, so the trade ledger is empty and no
past NAV was ever recorded. History can only start, which is the argument
for starting it soon.

Also: the benchmark line is out of the Today chart. One line, your book,
nothing to compare it against -- coloured by direction, filled, no legend.
SPY still appears on the Explore performance card and on the target card,
where a comparison is the point. A check asserts both halves of that: gone
from loadTodayPerf, still present elsewhere.

ship-t4.ps1: the truncation guard's "at least 100 lines added" floor went
stale the moment T4 was committed -- a legitimate follow-up of +18/-13
failed it as though the file had been destroyed. Truncation is a REMOVAL
signature, so the floor is replaced by a proportionality check (removed
must not dwarf added). Wholeness is already proven by the line count, the
</html> sentinel and the wiring checks; this step now asserts only what
only it can see.
'@

$mf = Join-Path $env:TEMP ("iw_t4fix_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. After the deploy:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-t4.ps1" -ForegroundColor DarkGray
