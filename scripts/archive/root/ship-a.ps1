# SHIP - Phase A + N2.
#
#   .\scripts\ship-a.ps1              # verify only
#   .\scripts\ship-a.ps1 -Ship        # verify, then commit + push if green
#
# TWO CHANGES, ONE PUSH:
#
#   N2  The Today card stops drawing the backfilled reconstruction. It was
#       correct and it was labelled, and it was still answering a question that
#       had not been asked -- a drawn curve outranks the caption under it.
#
#   A   The first WRITE in the T line. T0-T5 were read-only without exception
#       because a return target one tap from a book change is the C5 slider bug
#       with higher stakes. What makes relaxing it safe is asserted below:
#         - it writes plan_sleeves ONLY; no order, no quantity, no price
#         - confirm=true is required
#         - a plan solved against a book that has since moved is REFUSED
#         - decreases are written before increases, so no intermediate state
#           can breach the ceiling and leave the book half-applied
#         - every application records what it replaced, so undo is a read
#
# NEW IN THIS SCRIPT: `ruff check app tests`. Its absence is why CI went red on
# #148 and #149 while ship-n and ship-t5 both reported green -- pytest and
# py_compile are not linters, and the gate that was failing was one no ship
# script had ever invoked. It runs FIRST here, because it is the cheapest.
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
        $script:results[$name] = "PASS"
        Write-Host "    PASS" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL"
        Write-Host "    FAIL: $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

# --- A.0  the gate CI actually runs -----------------------------------------
Step "A.0  ruff check app tests (the gate ship scripts never ran)" {
    # Exactly what .github/workflows/ci.yml runs. Same command, same scope.
    & $Py -m ruff check app tests
}

# --- A.1  the ordering rule -------------------------------------------------
Step "A.1  a raise before a drop would half-apply (THE test)" {
    # (40,40) -> (70,20). Raising first passes through 110%, sleeve_service
    # refuses THAT call, and the book is left at (40,20) -- neither plan, no
    # error the user asked for. Each individual call behaved correctly, which is
    # why nothing else in the repo catches this.
    & $Py -m pytest "tests/test_target_apply.py::test_a_raise_before_a_drop_would_half_apply" -q
}

Step "A.1  a plan solved against a different book is refused" {
    & $Py -m pytest "tests/test_target_apply.py::test_a_plan_solved_against_a_different_book_is_detected" -q
}

Step "A.1  a plan that does not fit is refused WHOLE" {
    & $Py -m pytest "tests/test_target_apply.py::test_a_plan_that_does_not_fit_is_refused_whole" -q
}

Step "A.1  nothing in the planner produces an order" {
    & $Py -m pytest "tests/test_target_apply.py::test_nothing_in_the_planner_produces_an_order" -q
}

Step "A.1  the whole apply suite" {
    & $Py -m pytest tests/test_target_apply.py -q
}

Step "A.1  nothing else broke" {
    & $Py -m pytest tests -q -x
}

# --- A.2  the write path is gated -------------------------------------------
Step "A.2  confirm=true is required, and a refusal rolls back" {
    $src = Get-Content app\api\routes\plan.py -Raw
    if ($src -notmatch 'plan/target/apply') { throw "the apply route is missing" }
    if ($src -notmatch 'confirm: bool = False') {
        throw "confirm does not default to False - a bare POST would write"
    }
    # A mid-plan refusal can leave flushed rows in the session. Only an explicit
    # rollback makes "nothing was written" true rather than merely intended.
    if ($src -notmatch '(?s)plan/target/apply.{0,2600}?await session\.rollback\(\)') {
        throw "the apply route does not roll back on refusal"
    }
    $global:LASTEXITCODE = 0
}

Step "A.2  the planner never reaches a broker" {
    # An IMPORT-GRAPH assertion, run as a pytest so CI enforces it too.
    #
    # The first version of this step was a PowerShell text scan for "order",
    # "broker", "quantity". It failed -- on this module's own docstrings, which
    # say "places an order" several times in sentences denying that it does. A
    # check that fires on its own safety notes is a check someone deletes for
    # being noisy, and then nothing is protecting the boundary at all.
    & $Py -m pytest "tests/test_target_apply.py::test_the_apply_module_cannot_reach_a_broker" -q
}

Step "A.2  the ordering rule is provable without a database" {
    & $Py -m pytest "tests/test_target_apply.py::test_the_planner_writes_nothing_by_itself" -q
}

Step "A.2  the routes are registered on the app" {
    & $Py -c "from app.main import app; p={r.path for r in app.routes}; need=['/api/v1/plan/target/apply','/api/v1/plan/target/undo','/api/v1/plan/target/applications']; miss=[x for x in need if x not in p]; assert not miss, 'not routed: '+str(miss); print('   routed:', len(need))"
}

# --- A.3  the migration ------------------------------------------------------
Step "A.3  0017 is guarded and chains from 0016" {
    $m = Get-Content alembic\versions\0017_plan_applications.py -Raw
    if ($m -notmatch 'get_table_names\(\)') { throw "0017 is not guarded against create_all" }
    if ($m -notmatch 'down_revision\s*=\s*"0016_nav_snapshots"') { throw "0017 does not revise 0016" }
    $global:LASTEXITCODE = 0
}

Step "A.3  alembic has exactly one head, and it is 0017" {
    $heads = @(& $Py -m alembic heads 2>&1 | Where-Object { $_ -match '\(head\)' })
    Write-Host "        $($heads -join ' | ')" -ForegroundColor DarkGray
    if ($heads.Count -ne 1) { throw "$($heads.Count) heads - expected 1" }
    if ("$heads" -notmatch '0017') { throw "head is not 0017: $heads" }
    $global:LASTEXITCODE = 0
}

# --- N2  the Today card stops reconstructing --------------------------------
Step "N2  the Today card no longer fetches the backfill" {
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -match [regex]::Escape('portfolio/performance?range=${r}')) {
        throw "the Today chart still fetches the backfill - N2 did not land"
    }
    foreach ($n in @('startRecording', 'days recorded', 'cannot be backfilled')) {
        if ($h -notmatch [regex]::Escape($n)) { throw "index.html is missing $n" }
    }
    $global:LASTEXITCODE = 0
}

Step "N2  the Performance tab keeps its reconstruction" {
    # N2 removes the backfill from ONE card, not from the app. loadPerf() is
    # where a reconstruction is the actual subject, and the solver seed needs
    # the ten-year window. Deleting either would be over-applying the decision.
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch 'function loadPerf') { throw "loadPerf is gone" }
    if ($h -notmatch [regex]::Escape('portfolio/performance?range=MAX')) {
        throw "the solver seed lost its ten-year window"
    }
    $global:LASTEXITCODE = 0
}

# --- A.4  the button --------------------------------------------------------
Step "A.4  the card carries an Accept button wired to the apply route" {
    $h = Get-Content app\static_app\index.html -Raw
    foreach ($n in @('tgtAccept', 'tgtUndo', 'plan/target/apply?confirm=true',
                     'plan/target/undo?confirm=true')) {
        if ($h -notmatch [regex]::Escape($n)) { throw "index.html is missing $n" }
    }
    # The confirm text must say what it does NOT do, in words. A user unsure
    # what "apply" means assumes the more alarming reading, and would be right.
    if ($h -notmatch [regex]::Escape('No brokerage order is placed')) {
        throw "the confirm dialog does not state that no order is placed"
    }
    $global:LASTEXITCODE = 0
}

Step "A.4  the resizes are sent verbatim, from_pct included" {
    # Re-deriving from_pct client-side would throw away the staleness check --
    # the server could no longer tell "accept this" from "overwrite with this".
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch [regex]::Escape('resizes:we.resizes')) {
        throw "the frontend rebuilds the resize list instead of echoing the solve's"
    }
    $global:LASTEXITCODE = 0
}

# --- A.5  the file is whole --------------------------------------------------
Step "A.5  index.html is whole" {
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2450) { throw "only $lines lines - a truncated write looks exactly like this" }
    $tail = (Get-Content app\static_app\index.html -Tail 1).Trim()
    if ($tail -ne '</html>') { throw "file does not end in </html>, it ends in '$tail'" }
    $global:LASTEXITCODE = 0
}

Step "A.5  the inline script parses" {
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>')
    if ($m.Count -eq 0) { throw "no inline <script> found" }
    $js = ($m | ForEach-Object { $_.Groups[1].Value }) -join "`n;`n"
    $tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-a-check.js")
    [IO.File]::WriteAllText($tmp, $js, (New-Object Text.UTF8Encoding($false)))
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Write-Host "        node not found - SKIPPED" -ForegroundColor Yellow; $global:LASTEXITCODE = 0; return }
    & node --check $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

Step "A.5  the service worker version moved" {
    $sw = Get-Content app\static_app\sw.js -Raw
    if ($sw -notmatch "const VERSION = 'iw-v(\d+)'") { throw "cannot find VERSION in sw.js" }
    $now = [int]$Matches[1]
    $headSw = (git show HEAD:app/static_app/sw.js) -join "`n"
    $was = 0
    if ($headSw -match "const VERSION = 'iw-v(\d+)'") { $was = [int]$Matches[1] }
    Write-Host "        sw.js v$was -> v$now" -ForegroundColor DarkGray
    $shellChanged = [bool](git diff --numstat HEAD -- app/static_app/index.html)
    if (-not $shellChanged) {
        Write-Host "        index.html unchanged vs HEAD - nothing cached to invalidate" -ForegroundColor DarkGray
        $global:LASTEXITCODE = 0; return
    }
    if ($now -le $was) { throw "sw.js is still v$now - bump it or the shell is served from cache" }
    $global:LASTEXITCODE = 0
}

# --- summary ----------------------------------------------------------------
Write-Host "`n=============================================================" -ForegroundColor White
$fails = @($results.GetEnumerator() | Where-Object { $_.Value -eq "FAIL" })
foreach ($r in $results.GetEnumerator()) {
    $c = if ($r.Value -eq "PASS") { "Green" } else { "Red" }
    Write-Host ("{0,-6} {1}" -f $r.Value, $r.Key) -ForegroundColor $c
}
Write-Host "=============================================================" -ForegroundColor White
Write-Host "$(($results.Count - $fails.Count)) pass / $($fails.Count) fail" -ForegroundColor $(if ($fails.Count) { "Red" } else { "Green" })

if ($fails.Count) { Write-Host "`nNot shipping." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGreen. Re-run with -Ship to commit and push." -ForegroundColor Yellow; exit 0 }

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing" -ForegroundColor Cyan

git add app/services/target_apply.py app/services/nav_history.py `
        app/models/tables.py app/api/routes/plan.py `
        alembic/versions/0017_plan_applications.py `
        tests/test_target_apply.py `
        app/static_app/index.html app/static_app/sw.js STATUS.md `
        scripts/ship-a.ps1 scripts/smoke/smoke-a.ps1

$staged = @(git diff --cached --name-only)
Write-Host "        staged $($staged.Count) file(s):" -ForegroundColor DarkGray
$staged | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
if ($staged.Count -eq 0) { Write-Host "Nothing staged - already committed?" -ForegroundColor Yellow; exit 0 }

$msg = @'
Phase A: accept the recommendation. Plus N2, and the lint gate.

PHASE A -- the first write in the T line.

T0-T5 were read-only without exception, because a return target one tap
from a book change is the C5 slider bug with higher stakes. The card could
say "set your sleeves to 65%" and then leave the user to type it. This makes
the button real, and everything below is what makes that safe:

  It writes plan_sleeves. Intended PERCENTAGES. No brokerage order is
  placed, nothing is bought or sold, and the confirm dialog says exactly
  that -- not a euphemism for it.

  confirm=true is required. A write endpoint that fires on a bare POST is
  one a stray retry can trigger, and this one changes the plan.

  A plan solved against a book that has since moved is REFUSED. This is the
  quietest way an apply goes wrong: the numbers on the card describe a book
  that changed in another tab, so "accepting" silently overwrites the
  current plan with one computed for a different one. No exception, and
  entirely reasonable-looking sizes. `from_pct` is echoed from the solve
  verbatim so the server can catch it.

  Decreases are written before increases. (40,40) -> (70,20) applied in the
  solve's own order raises the first sleeve to 70% while the second holds
  40%, hits 110%, gets refused by sleeve_service -- and leaves the book at
  (40,20). Neither plan, no error anyone asked for, and every individual
  call behaved correctly, which is why nothing else catches it. The planner
  also walks its own step list and refuses if any intermediate total would
  breach, rather than trusting the rule holds.

  Every application records the state it replaced, so undo is a read of the
  last row rather than the user remembering two numbers.

N2 -- the Today card stops reconstructing.

The backfill was correct and was labelled "This is not your account
history", and it was still the wrong thing on that card. A drawn curve
outranks a caption under it, so the card answered "what would this book
have done" while appearing to answer "what did my money do". It now draws
nothing until nav_snapshots can produce a real series, and says how many
days it has. The reconstruction stays on the Performance tab, where it is
the actual subject, and on the solver seed, which needs the ten-year window.

THE LINT GATE.

ship-a.ps1 runs `ruff check app tests` -- the same command ci.yml runs --
before anything else. Its absence is why CI went red on #148 and #149 while
ship-n and ship-t5 both reported green: pytest and py_compile are not
linters, and the gate that was failing was one no ship script had invoked.
It caught an unused import in this phase's own test file before the push.
'@
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-a-commit.txt")
[IO.File]::WriteAllText($tmp, $msg, (New-Object Text.UTF8Encoding($false)))
git commit -F $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

git push
Write-Host "`nPushed. Wait for the deploy, then:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-a.ps1" -ForegroundColor Gray
