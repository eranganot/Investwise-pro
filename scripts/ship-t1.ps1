# SHIP - Phase T1: the blend engine.
#
#   .\scripts\ship-t1.ps1              # verify only
#   .\scripts\ship-t1.ps1 -Ship        # verify, then commit + push if green
#
# T1 is pure: app/engines/blend.py does no I/O, touches no session, and writes
# nothing. There is no migration and no money path, so this script is shorter
# than ship-t0.ps1 by design -- the risk is in the arithmetic, and the tests are
# where that gets caught.
#
# Rules carried over:
#   * A SKIP is never a PASS.
#   * Commit with -F and a LITERAL here-string. -m with an expandable
#     here-string is what broke ship-t0: PowerShell re-parses embedded double
#     quotes when marshalling to a native exe, so a quoted phrase containing a
#     space splits into several arguments and git reads the extras as pathspecs.

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

# --- 1. The identity property, alone and first ------------------------------
# If a blend of ONE component at full weight does not reproduce that strategy's
# own metrics exactly, the blend path and the strategy path are two different
# measurements and every comparison the solver makes is meaningless. Nothing
# else in T1 matters until this passes.
Step "identity: blend of one == the strategy itself" {
    & $Py -m pytest tests/test_blend.py -q --no-header -p no:randomly `
        -k "single_component_at_full_weight or two_identical_components"
}

Step "T1 tests" {
    & $Py -m pytest tests/test_blend.py -q --no-header -p no:randomly
}

Step "no skips in the T1 file" {
    $out = & $Py -m pytest tests/test_blend.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") { throw "$($Matches[1]) skipped -- a skip is not a pass" }
    $global:LASTEXITCODE = 0
}

# --- 2. blend.py imports private names from strategy_backtest ---------------
# _simulate, _metrics and _MIN_OBSERVATIONS deliberately, because reimplementing
# them is how the two paths drift apart. That coupling is the point, and it also
# means a change to either module can break the other silently.
Step "the engine it borrows from is still green" {
    & $Py -m pytest -q --no-header -k "backtest or strategy_backtest or blend"
}

# --- 3. Full suite + lint ---------------------------------------------------
Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ PHASE T1 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-44} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing T1" -ForegroundColor Cyan
$files = @("app/engines/blend.py", "tests/test_blend.py")
git add $files
git diff --cached --name-only

$msg = @'
feat(t1): measure a core-plus-sleeves book by simulating it, not by averaging it

app/engines/blend.py. Pure -- no I/O, no session, no writes.

The obvious implementation is wrong. Blending the headline numbers
(0.7*core_cagr + 0.3*sleeve_cagr) is a weighted average of two CAGRs,
which is not the CAGR of the blended book: it ignores rebalancing and the
order returns arrive in. For drawdown it is worse. Averaging two
max-drawdowns assumes the two never bottom on the same day, and a
trend-filtered TQQQ sleeve and an equity core bottom on exactly the same
days. The averaged figure understates the real drawdown by the largest
margin precisely when it matters most -- and the solver treats drawdown as
a HARD constraint, so an understated drawdown admits blends that should be
rejected.

Instead: blend the daily target vectors and run the existing _simulate
once. blended[t] = sum_i w_i * component_targets_i[t]. The true
correlation between core and sleeve falls out of the simulation, because
it is the same book on the same days. No correlation parameter is
invented, because none is needed.

Rules the blend enforces:
- Components share ONE date axis. A component whose history starts later
  is a hard error, not a silent left-pad; a sleeve that did not exist for
  three years would otherwise look like a sleeve that was in cash.
- Weights may sum to under 1.0 and the remainder is cash, explicitly, so
  the objective cash floor is modelled rather than redistributed.
- Two components wanting the same ticker SUM into one position -- the book
  holds one position per ticker, the same rule _arm_sleeve_caps uses.

Four fields a blend needs and a single strategy does not:
- components: each id, weight, and its OWN standalone cagr and drawdown
  measured over the same window.
- diversification_delta_pct: blended drawdown minus the weighted average
  of the components. Near zero is the expected answer for a leveraged
  sleeve on an equity core; printing it proves that rather than asserting.
- cash_drag_pct: what the unallocated remainder costs in CAGR.
- excess_at_equal_risk_pct: the leverage-proof scoreboard. Raw excess is
  trivially bought with leverage -- anyone beats SPY by holding 1.3x SPY
  -- so a solver maximizing it returns the most leveraged admissible blend
  every time. Scaling the blend to the benchmark drawdown removes that.
  Solved by bisection, not the closed form, because drawdown is not linear
  in exposure. equal_risk_leverage and equal_risk_drawdown_pct are
  reported so the reader can see what it assumed and where it landed.

Abstains rather than guessing: WEIGHTS_EXCEED_BOOK, COMPONENT_ABSTAINED
(naming the component), COMPONENT_MISALIGNED, INSUFFICIENT_HISTORY,
NO_OVERLAP, NO_COMPONENTS.

tests/test_blend.py asserts on the measured series. The load-bearing one
is the identity test: a blend of one component at full weight reproduces
bt.run exactly on cagr, drawdown, total return, volatility, observations
and excess. If that ever fails, the blend path and the strategy path have
become two different measurements.
'@

$mf = Join-Path $env:TEMP ("iw_t1_msg_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. CI is the gate." -ForegroundColor Green
Write-Host @'

Nothing user-facing changed: blend.py has no caller yet. That arrives in
T2, where the solver sweeps sleeve weights through measure_blend and
returns one of five verdicts.
'@ -ForegroundColor DarkGray
