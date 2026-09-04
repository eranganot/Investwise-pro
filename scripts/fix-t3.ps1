# FIX - T3 follow-up: the tax drag the blend never computed, and two around it.
#
#   .\scripts\fix-t3.ps1              # verify only
#   .\scripts\fix-t3.ps1 -Ship        # verify, then commit + push if green
#
# Do NOT re-run ship-t3.ps1 for this. T3 is already committed (211682a) and that
# script's file list does not include app/engines/blend.py -- where the fix
# actually is. Committing without it would ship a solver reading tax fields from
# a blend that does not produce them, and smoke-t3 would fail on the same check.
#
# Found by smoke-t3 T3: "no tax drag reported". Root cause: bt.run() adds
# gross_cagr_pct / tax_drag_pct / cgt_rate_pct AFTER _metrics returns, and
# measure_blend calls _metrics directly -- so the blend path never had them at
# all. A null renders identically to a strategy that pays no tax.

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

# --- 1. The regression the smoke caught, first and alone --------------------
Step "the blend reports its tax drag, matching the strategy path" {
    & $Py -m pytest tests/test_blend.py -q --no-header -p no:randomly -k "tax"
}

# --- 2. Everything downstream of the blend ---------------------------------
Step "T1 + T2 + T3 tests" {
    & $Py -m pytest tests/test_blend.py tests/test_target_solver.py `
        tests/test_target_cost.py -q --no-header -p no:randomly
}

Step "no skips in the T1..T3 files" {
    $out = & $Py -m pytest tests/test_blend.py tests/test_target_solver.py `
        tests/test_target_cost.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") { throw "$($Matches[1]) skipped -- a skip is not a pass" }
    $global:LASTEXITCODE = 0
}

# --- 3. The identity property, again ---------------------------------------
# blend.py changed. A blend of one component at full weight must still reproduce
# bt.run exactly -- including, now, its gross and tax figures.
Step "identity: blend of one still == the strategy itself" {
    & $Py -m pytest tests/test_blend.py -q --no-header -p no:randomly `
        -k "single_component_at_full_weight or tax_drag_matches"
}

Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ T3 FIX ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-52} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

# --- ship -------------------------------------------------------------------
# Named files only. The tree also shows frontend/node_modules churn, which is
# EOL noise -- adding by path keeps it out of this commit.
Write-Host "`n--- committing the T3 fix" -ForegroundColor Cyan
$files = @(
    "app/engines/blend.py",
    "app/services/target_solver.py",
    "tests/test_blend.py",
    "scripts/smoke/smoke-t3.ps1"
)
git add $files
git diff --cached --name-only

$msg = @'
fix(t3): the blend never computed its tax drag, and the solver double-counted cash

Three defects. The first was caught live by smoke-t3; the other two were
found auditing the same code path and are proven in code, not observed on
the live book.

1. NO TAX DRAG ON THE BLEND PATH.
bt.run() adds gross_cagr_pct, tax_drag_pct and cgt_rate_pct AFTER _metrics
returns. measure_blend calls _simulate and _metrics directly, so those
three fields never existed on a blend at all -- cost_of read None and
rendered nothing, which looks exactly like a strategy that pays no tax.
Fixed the way run() does it: re-simulate the same blended targets at
cgt_rate 0 and difference. A test now asserts the blend and strategy paths
report IDENTICAL gross and drag, so they cannot drift again. Under
detail=False the fields come back None, never zero -- not computed and
measured at zero are different claims.

WHY SILENT: a null tax figure renders as blank, and blank reads as "no tax
to report" rather than "never measured". Nothing raised, nothing logged.

2. CASH DOUBLE-COUNTED IN NAV.
list_positions returns a real CASH position row (ticker CASH, market TASE)
and credit_cash / repair_cash_row set its current_price to 1
unconditionally. solve_for summed every priced position AND added
get_cash() on top. NAV now comes from strategy_service._snapshot -- the
app's own single source, the same one the sleeve path sizes against. Two
implementations of NAV is two numbers that can disagree on one screen, and
app-bug-triage already names that shape for this repo.

3. CASH WOULD HAVE ENTERED THE CORE BASKET.
Worse than a missing ticker: is_cash_position's own docstring records that
CASH is also a real listing (Pathward Financial, NASDAQ), so the fetch
would have SUCCEEDED and the core would have been backtested holding a US
bank stock where the shekels are. No abstention, no error, a plausible
number. Now excluded via the app's own predicate rather than a ticker
string, so a rename cannot reintroduce it. Cash is already modelled: the
blend's weights sum to under 1 and _simulate leaves the remainder
uninvested.

CONFIDENCE: (1) PROVEN - observed failing on the deployed app. (2) and (3)
mechanism PROVEN in code, impact on the live book INFERRED - no CASH row
was observed in positions. The new smoke checks settle it on the next run.

Also: an unpriced holding no longer vanishes from NAV. It reports
degraded:["price"] naming the tickers, because it changes NAV and every
shekel figure with it. BLEND_ENGINE b1 -> b2, same rule the backtest
ENGINE_VERSION follows and for the same reason it was missed at a2.

smoke-t3 gains three checks: the solver's NAV must match /portfolio within
1% (the drawdown block is in shekels, so it pins the NAV that was used), no
CASH among the blend's tickers, and unpriced holdings fail rather than
quietly understate.
'@

$mf = Join-Path $env:TEMP ("iw_t3fix_msg_{0}.txt" -f (Get-Random))
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
Write-Host "  .\scripts\smoke\smoke-t3.ps1" -ForegroundColor DarkGray
Write-Host "  Expect the tax line to PASS, and the two new checks (NAV match, no CASH) to answer" -ForegroundColor DarkGray
Write-Host "  whether cash was ever actually in your book." -ForegroundColor DarkGray
