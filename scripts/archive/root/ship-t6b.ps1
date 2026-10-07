# SHIP - T6b (part 1 of 2): the discovered sleeve count.
#
#   .\scripts\ship-t6b.ps1              # verify only
#   .\scripts\ship-t6b.ps1 -Ship        # verify, then commit + push if green
#
# WHAT THIS SHIPS, STATED PLAINLY: the SEARCH ENGINE ONLY.
# `app/services/candidate_search.py` is a pure, fully tested module that nothing
# calls yet. No route, no card, no button. Shipping it now means the judgement
# in it is committed, reviewed and CI-gated before the wiring is written on top
# of it -- which is the opposite of what happened in T6a, where the wiring went
# out first and 500'd in production because a loader had never been executed.
#
# T6b.2 (next) is the wiring: measure(ids) against measure_blend on the OOS
# split, varying the core across strategies.CATALOG, the route, and the card.
#
# WHAT THE ENGINE IS FOR. The plan: "Discover the sleeve count, don't cap it...
# report where it stopped and by how much the last one helped. The count is then
# a measurement rather than a number someone picked. Keep a hard runtime ceiling
# as a backstop, and log it if the backstop is what stopped the search -- a limit
# that binds silently reads as a finding."
#
# That last clause is the whole design. "3 sleeves because the 4th was worth
# +0.18%/yr" and "3 sleeves because max_sleeves=3" are the same output and
# opposite claims, so every stop carries `is_a_limit`.
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

Step "T6b.0  ruff check app tests (the gate CI runs)" { & $Py -m ruff check app tests }

# --- the count is a measurement ---------------------------------------------
Step "T6b.1  the count stops where the gain stops" {
    & $Py -m pytest "tests/test_candidate_search.py::test_the_count_stops_where_the_gain_stops" -q
}
Step "T6b.1  a limit that binds says so rather than reading as a finding" {
    # THE test. Same output, opposite meaning.
    & $Py -m pytest "tests/test_candidate_search.py::test_a_limit_that_binds_says_so_rather_than_reading_as_a_finding" -q
}
Step "T6b.1  the rejected candidate and its gain are named" {
    # "It stopped at 2" is a number. "The third was worth +0.05%/yr" is a reason,
    # and it is what tells the user whether to raise the floor.
    & $Py -m pytest "tests/test_candidate_search.py::test_the_rejected_candidate_and_its_gain_are_named" -q
}
Step "T6b.1  a ranked list forbids auto-apply IN THE PAYLOAD" {
    # The plan: "No auto-apply, ever, from a ranked list." Carried by the API so
    # the UI has to actively ignore it, rather than by the UI where it can be
    # forgotten.
    & $Py -m pytest "tests/test_candidate_search.py::test_a_ranked_list_forbids_auto_apply_in_the_payload" -q
}
Step "T6b.1  no security can enter the search" {
    # investing-discipline section 5. The search sees strategy IDS only, so a
    # ticker cannot enter by construction -- this makes that catchable.
    & $Py -m pytest "tests/test_candidate_search.py::test_no_security_can_enter_the_search" -q
}
Step "T6b.1  the payload admits greedy can miss a pair" {
    & $Py -m pytest "tests/test_candidate_search.py::test_the_payload_admits_greedy_can_miss_a_pair" -q
}
Step "T6b.1  a set is never simulated twice" {
    # Greedy revisits subsets constantly and each is a ten-year simulation.
    & $Py -m pytest "tests/test_candidate_search.py::test_a_set_is_never_measured_twice" -q
}
Step "T6b.1  the whole candidate suite" { & $Py -m pytest tests/test_candidate_search.py -q }
Step "T6b.1  nothing else broke" { & $Py -m pytest tests -q -x }

# --- it is pure, and it is honest about being unwired ------------------------
Step "T6b.2  the engine is pure -- no session, no ORM, no price feed" {
    # It must stay testable without a database: the judgement in this module is
    # what decides how many sleeves a user runs, so it has to be the most tested
    # thing in the phase, not the least.
    $s = Get-Content app\services\candidate_search.py -Raw
    foreach ($bad in @('AsyncSession', 'session', 'measure_blend', 'import app.')) {
        if ($s -match [regex]::Escape($bad)) { throw "candidate_search reaches for $bad - it must stay pure" }
    }
    if ($s -notmatch 'def grow\(measure,') { throw "grow() no longer takes a measure callable" }
    $global:LASTEXITCODE = 0
}

Step "T6b.2  nothing calls it yet, and this script says so" {
    # Honest bookkeeping, checked rather than asserted in a comment. If a caller
    # appears, this step fails and the SHIP MESSAGE below is out of date --
    # which is the point: the commit must not claim "engine only" once it is
    # wired.
    $callers = @(Select-String -Path app\*.py, app\**\*.py -Pattern 'candidate_search' -ErrorAction SilentlyContinue |
                 Where-Object { $_.Path -notmatch 'candidate_search\.py$' })
    if ($callers.Count -gt 0) {
        throw "candidate_search now has $($callers.Count) caller(s) - this is no longer engine-only, update ship-t6b before shipping"
    }
    Write-Host "        engine-only, as the commit message states" -ForegroundColor DarkGray
    $global:LASTEXITCODE = 0
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

# No smoke test for this phase, ON PURPOSE: there is nothing deployed to smoke.
# A smoke that asserts an unwired module imports would be a green line proving
# nothing, and the standing rule is that a phase ends with a smoke that
# validates the DEPLOYMENT. T6b.2 carries that smoke, because T6b.2 is what
# deploys something.
# `git add` FAILING and `git add` finding nothing to do are different events
# that need opposite responses, and the first version of this block rendered
# both as "Nothing staged." -- which reads as "all clear, nothing to commit"
# when it actually meant "staging failed and the phase never shipped".
# git signals failure through $LASTEXITCODE, not a PowerShell exception, so
# ErrorActionPreference=Stop does not catch it.
$global:LASTEXITCODE = 0
git add app/services/candidate_search.py tests/test_candidate_search.py `
        scripts/ship-t6b.ps1 STATUS.md
$addExit = $LASTEXITCODE
if ($addExit -ne 0) {
    Write-Host "`ngit add FAILED (exit $addExit). NOTHING was staged and nothing shipped." -ForegroundColor Red
    if (Test-Path .git\index.lock) {
        $lk = Get-Item .git\index.lock
        Write-Host "  .git\index.lock exists: $($lk.Length) bytes, last written $($lk.LastWriteTime)" -ForegroundColor Yellow
        Write-Host "  A ZERO-byte lock is a stale one -- git creates it empty, writes the new" -ForegroundColor Yellow
        Write-Host "  index into it, then renames it over .git\index. Zero bytes means git was" -ForegroundColor Yellow
        Write-Host "  killed between those two steps." -ForegroundColor Yellow
        Write-Host "  Before deleting it, prove no git is running:" -ForegroundColor Gray
        Write-Host "    Get-Process git -ErrorAction SilentlyContinue" -ForegroundColor Gray
        Write-Host "  If that prints nothing:" -ForegroundColor Gray
        Write-Host "    Remove-Item .git\index.lock" -ForegroundColor Gray
    }
    exit 1
}

$staged = @(git diff --cached --name-only)
Write-Host "        staged $($staged.Count) file(s)" -ForegroundColor DarkGray
$staged | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
if ($staged.Count -eq 0) {
    Write-Host "git add succeeded but staged nothing - these files are already committed." -ForegroundColor Yellow
    exit 0
}

$msg = @'
T6b part 1: discover the sleeve count (engine only, nothing calls it yet)

THIS SHIPS A MODULE WITH NO CALLERS, deliberately. candidate_search.py is
pure and fully tested; the route, the measurement wiring and the card are
T6b.2. The judgement gets committed, reviewed and CI-gated BEFORE the
wiring sits on top of it -- the reverse of T6a, where the wiring shipped
first and 500'd in production because a loader had never once been run.

WHAT IT DOES. The plan: "Discover the sleeve count, don't cap it... report
where it stopped and by how much the last one helped. The count is then a
measurement rather than a number someone picked."

Greedy forward selection: add the best remaining candidate while each
addition beats a 0.25%/yr noise floor. Without a floor, greedy adds sleeves
forever -- every extra degree of freedom improves an in-sample fit by
SOMETHING, and "it improved by 0.03%/yr" is noise sold as a discovery.

THE FIELD THAT MATTERS IS `is_a_limit`.

  "3 sleeves, because the 4th was worth +0.18%/yr"   <- a measurement
  "3 sleeves, because max_sleeves=3"                 <- a number I picked

Same output. Opposite claims. The plan's words are "a limit that binds
silently reads as a finding", so every stop carries the flag, and a stop
caused by a backstop says so and tells the reader to raise it.

NO AUTO-APPLY, EVER, FROM A RANKED LIST. Carried in the PAYLOAD
(`auto_apply_forbidden`), not in the renderer. A flag the UI must read can
be forgotten; a flag the API always sends must be actively ignored. A single
solve has one answer and can carry an Accept button; picking row 1 of a list
searched over one history is selection on one sample.

THE FIREWALL, STRUCTURALLY. investing-discipline section 5 -- the app never
recommends products. The search sees strategy IDS only, never a ticker, so a
security cannot enter by construction. forbidden_tickers() makes that
catchable rather than merely true today.

HONEST ABOUT THE METHOD. Greedy, not exhaustive: exhaustive over 7
strategies and their sizes is millions of simulations. It can miss a pair
that only works together, and coverage.method says so IN THE PAYLOAD, where
the user can read it -- not in a docstring, where they cannot.

No smoke test in this commit: nothing is deployed to smoke. T6b.2 carries it.
'@
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-t6b-commit.txt")
[IO.File]::WriteAllText($tmp, $msg, (New-Object Text.UTF8Encoding($false)))
git commit -F $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue
git push
Write-Host "`nPushed. T6b.2 (the wiring + its smoke) is next." -ForegroundColor Green
