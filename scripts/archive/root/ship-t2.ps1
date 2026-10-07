# SHIP - Phase T2: the constrained solve, and its right to say no.
#
#   .\scripts\ship-t2.ps1              # verify only
#   .\scripts\ship-t2.ps1 -Ship        # verify, then commit + push if green
#
# T2 adds one GET endpoint and one service. It writes nothing -- and the check
# that proves that is the one this script cares about most.
#
# Rules carried over:
#   * A SKIP is never a PASS.
#   * Commit with -F and a LITERAL here-string (see commit-t0.ps1 for why).

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

# --- 1. Read-only, first and loudest ---------------------------------------
# The whole design rests on this. A solver that can move the book is a return
# target one tap from a trade, which is the C5 slider bug with higher stakes.
Step "read-only: no execution_plan on any path" {
    & $Py -m pytest tests/test_target_solver.py -q --no-header -p no:randomly `
        -k "execution_plan or would_execute"
}

# --- 2. The constraint is a constraint, not a comment -----------------------
# A returned weight must RE-MEASURE as satisfying both the target and the
# ceiling, and a tighter ceiling must never permit a larger sleeve.
Step "constraints bind the answer, not the report" {
    & $Py -m pytest tests/test_target_solver.py -q --no-header -p no:randomly `
        -k "ceiling or cap or re_measures or smallest"
}

Step "T2 tests" {
    & $Py -m pytest tests/test_target_solver.py -q --no-header -p no:randomly
}

Step "no skips in the T2 file" {
    $out = & $Py -m pytest tests/test_target_solver.py -q --no-header 2>&1 | Out-String
    if ($out -match "(\d+) skipped") { throw "$($Matches[1]) skipped -- a skip is not a pass" }
    $global:LASTEXITCODE = 0
}

# --- 3. T2 changed blend.py; T1 must still hold -----------------------------
# measure_blend gained detail=False and peak_weight_pct_by_ticker. The identity
# property is the one that must survive: a blend of one component at full weight
# still has to reproduce bt.run exactly.
Step "T1 identity still holds after the blend changes" {
    & $Py -m pytest tests/test_blend.py -q --no-header -p no:randomly
}

# --- 4. Full suite + lint ---------------------------------------------------
Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ PHASE T2 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-46} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing T2" -ForegroundColor Cyan
$files = @(
    "app/services/target_solver.py",
    "app/api/routes/plan.py",
    "app/engines/blend.py",
    "tests/test_target_solver.py"
)
git add $files
git diff --cached --name-only

$msg = @'
feat(t2): the constrained solve, and its right to say no

GET /plan/target?excess_pct=&max_drawdown_pct=, plus
app/services/target_solver.py. Read-only, without exception.

The objective, stated once, because whatever goes in the box is what gets
optimized for:

    maximise   excess CAGR over the benchmark
    subject to blended max drawdown <= D          (hard, user-set)
               concentration cap and cash floor hold
    report     excess at equal risk, alongside

The ceiling constrains the ADMISSIBLE SET; it is not a figure reported
afterwards. Without it the answer is always more leverage -- every sleeve
in this family is leveraged or concentrated, so raw excess is maximised by
the most leveraged blend the caps happen to allow. Both query parameters
are required and neither has a default: a return target with an implied
drawdown tolerance is the half-question this endpoint exists to stop.

Five outcomes, every one a legitimate answer:
- REACHED: an admissible size meets the target.
- REACHED_ABOVE_CAP: only past the concentration cap, naming the cap and
  the ticker that breaches it.
- DRAWDOWN_BOUND: reachable on return, only by breaching the ceiling.
  Reports what the target would require and the best achievable within the
  ceiling -- it separates your strategies are too weak from your risk
  tolerance is what binds, which need opposite responses.
- UNREACHABLE: names WHICH sleeve excess is holding the blend down, with
  that sleeve own measured CAGR against the benchmark. You cannot get there
  is a dead end; this sleeve measured 1.2 against a benchmark doing 9 is a
  work item.
- NOT_MEASURABLE: a component abstained; the reason travels.

Two things found by the tests, both real:
- The concentration cap was judged book-wide, so an already-concentrated
  core vetoed every sleeve size INCLUDING ZERO -- an answer that says
  nothing about the decision being made and hides the sleeve actually
  breaching. It is now judged against the tickers the sleeves can hold
  (blend.claimable_tickers: basket + base + risk_off, since a gate ticker
  is not a position). The core own concentration is reported separately.
- Nothing admissible can mean two opposite things. A new floor block
  reports the book drawdown at zero sleeve and whether it already breaches
  the ceiling, so shrink the sleeve and raise the ceiling stop looking
  identical on the card.

blend.py gains detail=False (skips the per-component and cash-drag
simulations that do not change the blended figures, for the sweep) and
peak_weight_pct_by_ticker. Skipped fields come back None, never zero. The
point actually reported is re-measured with detail on, so the card cannot
render a figure the sweep did not compute.

Swept coarse at 5 points then refined at 1, solved on the 1-point grid and
displayed on the slider 5-point grid, rounded UP -- rounding down would
land under the target the user asked for.

would_execute carries the resizes add_or_resize takes. legs is NULL on
purpose: a leg is a share count at a price, and a price carries a
price_as_of that must be checked before anything is staged. Building
priced legs in a read-only phase would manufacture the stale-quote claim
investing-discipline 1b exists to prevent. Phase A fills them from
plan_funding, in the shape documented in legs_schema.
'@

$mf = Join-Path $env:TEMP ("iw_t2_msg_{0}.txt" -f (Get-Random))
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

After CI is green, try it against your own book:
  GET /api/v1/plan/target?excess_pct=5&max_drawdown_pct=30

Nothing renders it yet -- that is T4. Expect a real answer from the JSON,
and expect it to be one of the three that mean no.
'@ -ForegroundColor DarkGray
