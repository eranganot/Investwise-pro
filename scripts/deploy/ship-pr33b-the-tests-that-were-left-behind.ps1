# SHIP PR 33b - the tests PR 33 rewrote and PR 33 did not commit.
#
#   .\scripts\deploy\ship-pr33b-the-tests-that-were-left-behind.ps1
#
# CI run #194 went red on both backends with three failures:
#
#   test_a_spike_is_not_a_flow.py::test_a_removal_that_is_NOT_reversed_stays_an_ordinary_flow
#   test_history_does_not_call_an_intake_performance.py::test_the_step_is_named_rather_than_folded_in_silently
#   test_history_does_not_call_an_intake_performance.py::test_a_holding_sold_out_of_is_not_a_loss
#
# Those are the three tests PR 33 rewrote. The rewrites are sitting in the
# working tree, unstaged: ship-pr33's $Files list named only ONE of the three
# test files it changed, and New-Commit is path-limited by design, so the other
# two were left behind. The commit went out with the new code and the old tests.
#
# The local gate could not catch this. It tests the working tree, and the
# working tree is correct. The divergence exists only between the tree and the
# commit, so the check belongs in New-Commit - which this change adds.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags; run BOTH backends.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host ("== " + $m) -ForegroundColor Cyan }
function Warn($m) { Write-Host ("  ! " + $m) -ForegroundColor Yellow }

$Files = @(
    "tests/test_a_spike_is_not_a_flow.py",
    "tests/test_history_does_not_call_an_intake_performance.py",
    "scripts/deploy/_common.ps1",
    "scripts/deploy/ship-pr33-the-day-it-could-not-explain.ps1",
    "scripts/deploy/ship-pr33b-the-tests-that-were-left-behind.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 33b: the tests PR 33 rewrote and PR 33 did not commit",
    "",
    "CI #194 red on both backends, three failures, all of them the tests PR 33",
    "rewrote:",
    "",
    "    test_a_spike_is_not_a_flow::test_a_removal_that_is_NOT_reversed_stays_an_ordinary_flow",
    "    test_history_does_not_call_an_intake_performance::test_the_step_is_named_rather_than_folded_in_silently",
    "    test_history_does_not_call_an_intake_performance::test_a_holding_sold_out_of_is_not_a_loss",
    "",
    "PR 33 changed WHERE an unexplained step lands: with a reconstruction it is",
    "an estimated flow, without one the day is unmeasurable and the line breaks.",
    "Both are 'named'; neither is performance. The three tests above were",
    "rewritten to assert that PROPERTY rather than the old mechanism - and two",
    "of the three files were missing from ship-pr33's $Files list. New-Commit",
    "is path-limited by design (the 'never git add -A' rule), so it committed",
    "the new code and left the rewritten tests in the working tree.",
    "",
    "THE LOCAL GATE COULD NOT HAVE CAUGHT THIS. It ran the new tests against",
    "the new code and went green, correctly, because that is what was on disk.",
    "The divergence existed only between the working tree and the commit.",
    "",
    "So the check goes where the two actually diverge. New-Commit now lists",
    "every MODIFIED TRACKED file that is not in $Files, says plainly that the",
    "gate tested them and CI will not have them, and requires a confirmation",
    "before committing without them. The exclusions are the tracked files that",
    "are dirty for reasons unrelated to any commit: investwise.db (rewritten by",
    "every local pytest run), frontend/node_modules, scripts/railway-error.log,",
    "and New-Commit's own COMMIT_MSG.txt scratch file.",
    "",
    "ship-pr33's $Files is corrected too, so a re-run of it is right.",
    "",
    "No application code changes. The gate is PR 34's, re-run to confirm:",
    "SQLite 1515 passed / 0 failed; Postgres 16 1515 passed / 0 failed; ruff",
    "check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this is"
Write-Host ""
Write-Host "  THIS COMMITS THREE REWRITTEN TESTS AND NOTHING ELSE." -ForegroundColor Green
Write-Host ""
Write-Host "  No application code changes. CI #194 is red because the commit" -ForegroundColor Yellow
Write-Host "  carried PR 33's new code and PR 33's OLD tests." -ForegroundColor Yellow
Write-Host ""
Write-Host "  It also adds the guard that would have caught it: New-Commit now" -ForegroundColor Cyan
Write-Host "  refuses to commit quietly while a modified tracked file is not in" -ForegroundColor Cyan
Write-Host "  the script's file list." -ForegroundColor Cyan
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Are the rewritten tests actually here?"
$expect = @("tests/test_a_spike_is_not_a_flow.py",
            "tests/test_history_does_not_call_an_intake_performance.py")
$dirty = @(git diff --name-only)
$have = @($expect | Where-Object { $dirty -contains $_ })
if ($have.Count -ne $expect.Count) {
    Write-Host ""
    Write-Host "  These are not modified in your working tree:" -ForegroundColor Red
    foreach ($f in $expect) { if ($dirty -notcontains $f) { Write-Host ("    " + $f) -ForegroundColor Red } }
    Write-Host "  Nothing to commit, or the rewrites were lost. Re-apply the PR 33" -ForegroundColor Red
    Write-Host "  patch before running this." -ForegroundColor Red
    exit 1
}
Write-Host "  Both rewritten test files are present and modified." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. This commit exists BECAUSE a gate"
    Warn "result was trusted for a tree that did not match the commit."
} else {
    $Focus = @(
        "tests/test_a_spike_is_not_a_flow.py",
        "tests/test_history_does_not_call_an_intake_performance.py",
        "tests/test_the_chart_measures_the_day_it_cannot_explain.py",
        "tests/test_nav_history.py")
    $missingFocus = @($Focus | Where-Object { -not (Test-Path $_) })
    if ($missingFocus.Count -gt 0) {
        Write-Host ""
        Write-Host "  STOP - this script names test files that do not exist:" -ForegroundColor Red
        foreach ($f in $missingFocus) { Write-Host ("    " + $f) -ForegroundColor Red }
        exit 1
    }
    Invoke-Suite -Focus $Focus
    Write-Host ""
    Write-Host "  Then run test-postgres." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass clean? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host ("  local HEAD: " + $sha) -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * CI should go green on all three jobs. The three failures in" -ForegroundColor Cyan
Write-Host "    #194 were the only ones." -ForegroundColor Cyan
Write-Host "  * Nothing in the app changes. This commit touches tests and" -ForegroundColor Cyan
Write-Host "    deploy scripts only." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
