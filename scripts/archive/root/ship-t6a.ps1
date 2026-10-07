# SHIP - T6a: size each sleeve on its own axis, and rank it out-of-sample.
#
#   .\scripts\ship-t6a.ps1              # verify only
#   .\scripts\ship-t6a.ps1 -Ship        # verify, then commit + push if green
#
# T6a is the FIRST QUARTER of the plan's T6. It does the per-sleeve split search.
# It does NOT add strategies you do not hold, vary the core, or discover the
# sleeve count -- those are T6b, and this script does not pretend otherwise.
#
# THE RISK IN THIS PHASE IS NOT THE ARITHMETIC. Picking the best of ~55 splits
# scored on one history is in-sample optimisation: search hard enough and
# something always wins. The output ends at a green Accept button, where a
# curve-fit looks exactly like a finding. So the plan's three guards are asserted
# here BY NAME, and the button is disabled unless they pass:
#
#   - rank on backtest_service.OOS_SPLIT, never on the sample the winner
#     was chosen from
#   - show the fit/test gap on every ranked row
#   - print how many splits were searched, next to the winner
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

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
        $script:results[$name] = "PASS"; Write-Host "    PASS" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL"
        Write-Host "    FAIL: $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

Step "T6a.0  ruff check app tests (the gate CI runs)" { & $Py -m ruff check app tests }

# --- the three guards, by name ----------------------------------------------
# --- T6a.0b  the checks that would have caught the 500 --------------------
# The split endpoint 500'd in 0.1s on the first deploy -- before any simulation.
# `_book_for_solve` had been re-written FROM MEMORY while the working original
# sat forty lines above it in the same file, and got three names wrong:
#   strategy_catalog.spec_for   does not exist (it is .get)
#   is_cash_position            lives in intake_service, not strategy_service
#   cash_floor_pct              is not async, and takes (objective, plan)
# No test resolved any of those names, so nothing failed until production did.
Step "T6a.0b  the dependency signatures actually resolve" {
    & $Py -m pytest "tests/test_target_apply.py::test_the_apply_module_calls_its_dependencies_with_the_right_signatures" -q
}

Step "T6a.0b  a missing cash floor refuses instead of assuming zero" {
    # The same wrong signature was live in SHIPPED Phase A code, inside a bare
    # `except: floor = 0.0`. It raised on every call and silently fell back to a
    # ZERO cash floor -- so the Accept button would have let the sleeves take
    # 100% of the book while the user's floor said otherwise, with no error
    # anywhere. A constraint that quietly becomes zero has stopped existing.
    & $Py -m pytest "tests/test_target_apply.py::test_a_missing_cash_floor_refuses_instead_of_assuming_zero" -q
}

Step "T6a.0b  there is ONE book loader, not two" {
    # The root cause was duplication, so the check is against duplication --
    # not against the three particular names, which is the shape of the bug
    # rather than its cause. _book_for_solve's docstring CLAIMED it had been
    # "factored out rather than copied" while being a copy; this makes the
    # claim testable.
    $s = Get-Content app\services\target_solver.py -Raw
    $loaders = ([regex]::Matches($s, 'strategy_catalog\.get\(')).Count
    if ($loaders -ne 1) { throw "$loaders catalog lookups - the book loader has been duplicated again" }
    if ($s -notmatch '(?s)async def solve_for\(.{0,900}?_book_for_solve\(session, user\)') {
        throw "solve_for no longer calls the shared loader - the two can drift again"
    }
    if ($s -match 'spec_for\(r\.strategy_id\)') { throw "spec_for is back, and it does not exist" }
    $global:LASTEXITCODE = 0
}

Step "T6a.1  the winner is chosen out-of-sample, not in-sample" {
    & $Py -m pytest "tests/test_split_solver.py::test_the_winner_is_chosen_out_of_sample_not_in_sample" -q
}
Step "T6a.1  falling back to the full sample is declared, never silent" {
    & $Py -m pytest "tests/test_split_solver.py::test_falling_back_to_the_full_sample_is_declared_not_silent" -q
}
Step "T6a.1  every ranked row carries its fit/test gap" {
    & $Py -m pytest "tests/test_split_solver.py::test_every_ranked_row_carries_its_fit_test_gap" -q
}
Step "T6a.1  a winner that decays worse than typical is called out" {
    & $Py -m pytest "tests/test_split_solver.py::test_a_winner_that_decays_worse_than_typical_is_called_out" -q
}
Step "T6a.1  an optimum inside the noise floor is reported as noise" {
    & $Py -m pytest "tests/test_split_solver.py::test_an_optimum_indistinguishable_from_the_median_is_reported_as_noise" -q
}
Step "T6a.1  a grid too large is coarsened, never truncated" {
    # Truncating searches a CORNER and reports the winner as though the whole
    # space was tried. That is the claim `coverage` exists to prevent.
    & $Py -m pytest "tests/test_split_solver.py::test_a_grid_too_large_is_coarsened_not_truncated" -q
}
Step "T6a.1  the result is never called a global optimum" {
    & $Py -m pytest "tests/test_split_solver.py::test_the_result_is_never_called_a_global_optimum" -q
}
Step "T6a.1  the whole split suite" { & $Py -m pytest tests/test_split_solver.py -q }
Step "T6a.1  nothing else broke" { & $Py -m pytest tests -q -x }

# --- it uses the constant that already exists --------------------------------
Step "T6a.2  the OOS boundary is backtest_service's, not a second copy" {
    $s = Get-Content app\services\target_solver.py -Raw
    if ($s -notmatch 'from app\.services\.backtest_service import OOS_SPLIT') {
        throw "target_solver defines its own OOS boundary instead of importing OOS_SPLIT - two answers to 'which window is out of sample' will drift"
    }
    $b = Get-Content app\services\split_solver.py -Raw
    if ($b -notmatch 'sample of one') {
        throw "the OOS caveat does not carry the constant's own comment - 'out of sample' borrows authority the window has not earned"
    }
    $global:LASTEXITCODE = 0
}

Step "T6a.2  the sweep's drawdown is declared a lower bound" {
    # The sweep judges the ceiling on the worse HALF-window, which understates a
    # fall spanning the split. The winner is re-measured in full before it is
    # shown; if that is ever removed, the card claims a figure it never computed.
    $s = Get-Content app\services\target_solver.py -Raw
    if ($s -notmatch 'LOWER bound') { throw "the half-window approximation is not declared" }
    if ($s -notmatch 'verified_in_full') { throw "the winner is not re-measured over the full window" }
    $global:LASTEXITCODE = 0
}

Step "T6a.2  the route is registered and takes no target" {
    & $Py -c "from app.main import app; import inspect; p={r.path for r in app.routes}; assert '/api/v1/plan/target/split' in p, 'not routed'; from app.api.routes.plan import target_split as t; ps=list(inspect.signature(t).parameters); assert 'excess_pct' not in ps, 'the split route takes a target - it has none to miss'; print('   routed;', ps)"
}

# --- the button obeys the plan ----------------------------------------------
Step "T6a.3  no auto-apply: Accept is disabled on an unverified or noise result" {
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch [regex]::Escape('const ok=s.verified_in_full && !(sp.is_noise);')) {
        throw "the split Accept button is not gated on verification and the noise floor"
    }
    if ($h -notmatch 'tgtAcceptSplit') { throw "no split accept path" }
    # It must reuse Phase A, not open a second write path with its own guards.
    if ($h -notmatch '(?s)async function tgtAcceptSplit\(\)\{(.*?)\n\}') { throw "cannot find tgtAcceptSplit" }
    $acc = $Matches[1]
    if ($acc -match 'fetch\(') { throw "tgtAcceptSplit fetches directly - it must reuse tgtAccept, or Phase A's staleness and confirm guards are bypassed" }
    if ($acc -notmatch 'tgtAccept\(\)') { throw "tgtAcceptSplit does not delegate to tgtAccept" }
    $global:LASTEXITCODE = 0
}

Step "T6a.3  the card shows what it searched" {
    $h = Get-Content app\static_app\index.html -Raw
    foreach ($n in @('Searched ', 'coarse_points', 'fit_test', 'ranked_on_note', 'best ${sp.best_pct}')) {
        if ($h -notmatch [regex]::Escape($n)) { throw "the card does not render $n" }
    }
    $global:LASTEXITCODE = 0
}

Step "T6a.3  the search is a button, not something the card runs on load" {
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch [regex]::Escape('onclick="tgtSplit()"')) { throw "no explicit button" }
    if ($h -match 'setTimeout\(tgtSplit') { throw "the split search runs on load - it is minutes of work" }
    $global:LASTEXITCODE = 0
}

Step "T6a.4  index.html is whole and parses" {
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2520) { throw "only $lines lines - truncation looks exactly like this" }
    if ((Get-Content app\static_app\index.html -Tail 1).Trim() -ne '</html>') { throw "does not end in </html>" }
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>')
    $js = ($m | ForEach-Object { $_.Groups[1].Value }) -join "`n;`n"
    $tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-t6a.js")
    [IO.File]::WriteAllText($tmp, $js, (New-Object Text.UTF8Encoding($false)))
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Write-Host "        node not found - SKIPPED" -ForegroundColor Yellow; $global:LASTEXITCODE = 0; return }
    & node --check $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

Write-Host "`n=============================================================" -ForegroundColor White
$fails = @($results.GetEnumerator() | Where-Object { $_.Value -eq "FAIL" })
foreach ($r in $results.GetEnumerator()) {
    Write-Host ("{0,-6} {1}" -f $r.Value, $r.Key) -ForegroundColor $(if ($r.Value -eq "PASS") { "Green" } else { "Red" })
}
Write-Host "=============================================================" -ForegroundColor White
Write-Host "$(($results.Count - $fails.Count)) pass / $($fails.Count) fail" -ForegroundColor $(if ($fails.Count) { "Red" } else { "Green" })
if ($fails.Count) { Write-Host "`nNot shipping." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGreen. Re-run with -Ship to commit and push." -ForegroundColor Yellow; exit 0 }

git add app/services/split_solver.py app/services/target_solver.py `
        app/api/routes/plan.py tests/test_split_solver.py `
        app/static_app/index.html app/static_app/sw.js STATUS.md `
        BEAT_MARKET_TARGET_SOLVER_PLAN.md `
        scripts/ship-t6a.ps1 scripts/smoke/smoke-t6a.ps1
$staged = @(git diff --cached --name-only)
Write-Host "        staged $($staged.Count) file(s)" -ForegroundColor DarkGray
$staged | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
if ($staged.Count -eq 0) { Write-Host "Nothing staged." -ForegroundColor Yellow; exit 0 }

$msg = @'
T6a: size each sleeve on its own axis, ranked out-of-sample

T2 sweeps ONE axis -- total sleeve share, divided at the ratio the book
already runs. Its own docstring named the gap: "Sizing each independently is
a wider search, not this one. That search is T6." This is that search.

T6a IS THE FIRST QUARTER OF PLAN-T6. It does not yet add strategies you do
not hold, vary the core, or discover the sleeve count. Those are T6b and
nothing here pretends otherwise.

The risk in this phase is not the arithmetic. Picking the best of ~55 splits
scored on one history is in-sample optimisation: search hard enough over one
window and something always wins, and that it won says little about whether
it wins again. The output ends at a green Accept button, where a curve-fit
looks exactly like a finding. So:

  RANKED OUT-OF-SAMPLE on backtest_service.OOS_SPLIT -- the constant that
  already exists, imported rather than restated so the two cannot drift --
  and never on the full sample the winner was chosen from. When no
  out-of-sample figure exists it falls back and SAYS it fell back.

  THE CAVEAT TRAVELS. OOS_SPLIT's own comment is "the only real bear market
  these instruments have seen". One bear market is a sample of one, and the
  card says so rather than borrowing the authority of "out of sample".

  FIT/TEST GAP ON EVERY ROW, with the median gap beside the winner's. If
  every candidate decays alike that is the instruments; if the winner decays
  more, it was learned from the fitting window.

  SPREAD. Best against MEDIAN across everything searched. Inside the noise
  floor the "optimum" is a coin landing, and the Accept button is disabled.

  COVERAGE. How many splits, on what grid, whether the grid was coarsened to
  fit a budget, and in the payload -- not a comment -- that the result is the
  best point REACHED, never a proven global optimum. A grid too large is
  coarsened, never truncated: truncating searches a corner and reports the
  winner as though the whole space was tried.

The sweep judges the ceiling on the worse half-window, which is a LOWER
bound -- a fall spanning the split is larger than either half sees. The
winner is re-measured over the whole window before it is shown, the same way
T2's _verdict re-measures its chosen point.

Accept reuses Phase A verbatim: same endpoint, same staleness refusal, same
confirm text. A second apply path would be a second set of guards.

Also settles the plan's last open question. The ten-year window leads the
card; "how am I doing" belongs to Phase N's real account history, and the
250-day backfill headlines nowhere.
'@
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-t6a-commit.txt")
[IO.File]::WriteAllText($tmp, $msg, (New-Object Text.UTF8Encoding($false)))
git commit -F $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue
git push
Write-Host "`nPushed. Then: .\scripts\smoke\smoke-t6a.ps1" -ForegroundColor Green
