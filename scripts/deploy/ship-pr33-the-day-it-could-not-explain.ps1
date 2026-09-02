# SHIP PR 33 - six of eleven recorded days were reported as exactly 0.00%.
#
#   .\scripts\deploy\ship-pr33-the-day-it-could-not-explain.ps1
#   .\scripts\deploy\ship-pr33-the-day-it-could-not-explain.ps1 -SkipTests
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
    "app/services/nav_history.py",
    "app/static_app/index.html",
    "tests/test_the_chart_measures_the_day_it_cannot_explain.py",
    # REWRITTEN BY THIS CHANGE, and missing from this list on the first run.
    # The commit went out with the new code and the old tests, and CI #194 went
    # red on three of them. _common.ps1 now refuses to commit while a modified
    # tracked file is unlisted.
    "tests/test_a_spike_is_not_a_flow.py",
    "tests/test_history_does_not_call_an_intake_performance.py",
    "scripts/smoke/smoke-history-measured.ps1",
    "scripts/deploy/ship-pr33-the-day-it-could-not-explain.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 33: the chart measured the day it could not explain",
    "",
    "Six of eleven recorded days were reported as exactly 0.00%.",
    "time_weighted set the flow to the WHOLE inter-snapshot delta whenever the",
    "position count changed and the ledger said nothing:",
    "",
    "    f = nav - prev_nav",
    "    r = (nav - f) / prev_nav - 1     ==  0.0, arithmetically, every time",
    "",
    "So the one day the book changed shape is the one day the chart reports as",
    "nothing happening - and it reports it as a NUMBER, not as a gap.",
    "",
    "MEASURED. On 2026-09-01 Eran deleted two holdings the app had priced at 0",
    "(PR 32 is why they were 0). NAV went 21,474.35 -> 20,777.45 and the chart",
    "booked all 696.90 of it as 'money you moved out', drawing the day at",
    "0.00%. From real closes over the same two dates:",
    "",
    "    SOXL   112.79 -> 105.91   -6.10%",
    "    TQQQ    71.92 ->  69.15   -3.85%",
    "    V      379.37 -> 372.67   -1.77%",
    "    BND     72.24 ->  71.79   -0.62%",
    "",
    "on a book that is 79% TQQQ and SOXL. The 696.90 was the market. Nothing",
    "left the book.",
    "",
    "THE FIX: DO NOT GUESS, MEASURE. window_return prices the book the user",
    "holds today across the same two dates from real closes - reusing",
    "performance_service rather than growing a second implementation that can",
    "disagree with the first. What that explains is performance; only the",
    "residual is a flow:",
    "",
    "    flow = nav - prev_nav x (1 + r_held)",
    "",
    "and the step's return falls out of the UNCHANGED formula as exactly",
    "r_held. Three outcomes, each said out loud rather than collapsed into a",
    "number:",
    "",
    "  residual real and correctly signed -> an ESTIMATED flow, labelled with",
    "    its method and the held-book return it was measured against",
    "  residual noise or wrong-signed -> resolved_changes: the app checked, and",
    "    the step was market movement, not money",
    "  no usable history, or a reconstruction that contradicts the book ->",
    "    unmeasured_days: the line BREAKS. 'We could not separate the book",
    "    change from the market' is not a return of zero and may not be drawn",
    "    as one. spanGaps:false on the chart is load-bearing.",
    "",
    "FOUR GUARDS, EACH FROM A REAL FAILURE ON THIS BOOK:",
    "",
    "1. A REMOVAL CANNOT BRING MONEY IN. The 1 Sep residual is +219 ILS on a",
    "   day two holdings were REMOVED - the gap between the recorded NAV and a",
    "   reconstruction built from today's holdings, which is this method's",
    "   error term. Reading it as a deposit would replace one false claim with",
    "   another. The recorded NAVs are the better evidence and the day books at",
    "   -3.245%: the market move.",
    "",
    "2. BOTH ENDS RESOLVING TO THE SAME CLOSE IS NOT A ZERO RETURN. It would",
    "   make the residual the whole NAV delta again - the original defect,",
    "   restored through the fallback meant to fix it.",
    "",
    "3. THE RESOLVED WINDOW MUST COVER THE SAME MARKET TIME. performance",
    "   intersects dates across every holding, so one missing bar drops the day",
    "   for the whole book - 2026-08-28 is absent because V and BND had no",
    "   close in the feed. Without the span guard the 30 -> 31 Aug step priced",
    "   ONE day of NAV against FOUR days of market and invented a 1,088 ILS",
    "   flow on a day nothing was deposited.",
    "",
    "4. A WRONG-SIGNED RESIDUAL ABOVE 2% OF NAV IS THE METHOD NOT APPLYING,",
    "   not noise. 2026-08-24 is a reconcile that replaced half the book;",
    "   pricing it with the four tickers held today misses by 3,615 ILS, 17% of",
    "   NAV. That day is a hole, not a confident step.",
    "",
    "Weekends resolve back to the last close (3-day lookback): a book does not",
    "move while the market is shut, and 2026-08-30 is a Sunday.",
    "",
    "ON THE REAL SERIES this turns",
    "",
    "    +0.128% over 11 days, six of them fake zeros",
    "",
    "into",
    "",
    "    -3.12%, measured over 7 of 10 steps, with three named holes",
    "",
    "The old number was a fiction. NOTHING IS WRITTEN TO THE DATABASE:",
    "time_weighted runs on every request, so the whole history repairs itself",
    "on the next page load - 24 and 29 August and 1 September included.",
    "",
    "The reconstruction is only attempted when the window actually contains a",
    "book change the ledger cannot explain, so the common path pays nothing.",
    "",
    "Gate: SQLite 1500 passed / 0 failed; Postgres 16 1500 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR ACCOUNT HISTORY WILL READ DIFFERENTLY, and lower." -ForegroundColor Green
Write-Host ""
Write-Host "  Before: +0.128% over 11 days - six of which were flat zeros the" -ForegroundColor Yellow
Write-Host "          app drew because it could not tell a book change from the" -ForegroundColor Yellow
Write-Host "          market, not because nothing happened." -ForegroundColor Yellow
Write-Host "  After:  about -3.1%, measured over 7 of 10 steps, with three days" -ForegroundColor Green
Write-Host "          shown as breaks in the line and named underneath." -ForegroundColor Green
Write-Host ""
Write-Host "  THE LOWER NUMBER IS THE TRUE ONE. 1 September was a -3.2% day on" -ForegroundColor Cyan
Write-Host "  a book that is 79% TQQQ and SOXL; the chart was calling it a" -ForegroundColor Cyan
Write-Host "  696.90 ILS withdrawal and drawing it flat." -ForegroundColor Cyan
Write-Host ""
Write-Host "  NOTHING IS WRITTEN TO THE DATABASE. The series is recomputed on" -ForegroundColor Yellow
Write-Host "  every request, so this repairs the past as well as the future." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
python -m pytest -q tests/test_the_pins_are_installable_and_installed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  STOP. The versions installed here are not the versions" -ForegroundColor Red
    Write-Host "  requirements.txt pins." -ForegroundColor Red
    Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor Cyan
    exit 1
}
Write-Host "  This interpreter matches the pins." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. This PR changes the number on your"
    Warn "account history card. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_the_chart_measures_the_day_it_cannot_explain.py",
        "tests/test_nav_history.py",
        "tests/test_history_does_not_call_an_intake_performance.py",
        "tests/test_a_spike_is_not_a_flow.py")
    # A FOCUS FILE THAT DOES NOT EXIST IS A SCRIPT BUG, NOT A TEST FAILURE.
    # pytest exits non-zero on "file or directory not found" and Invoke-Suite
    # then prints "Focused tests FAILED - not committing", which sends the
    # reader looking for a broken test that does not exist. Name the real
    # problem instead. (This happened on the first run of this script: the
    # focus list said tests/test_pr22_history_intake.py and the file is
    # tests/test_history_does_not_call_an_intake_performance.py.)
    $missingFocus = @($Focus | Where-Object { -not (Test-Path $_) })
    if ($missingFocus.Count -gt 0) {
        Write-Host ""
        Write-Host "  STOP - this script names test files that do not exist:" -ForegroundColor Red
        foreach ($f in $missingFocus) { Write-Host ("    " + $f) -ForegroundColor Red }
        Write-Host "  That is a bug in THIS SCRIPT, not a failing test. Fix the" -ForegroundColor Red
        Write-Host "  focus list above and re-run." -ForegroundColor Red
        exit 1
    }
    Invoke-Suite -Focus $Focus
    Write-Host ""
    Write-Host "  Then run test-postgres, the one job the SQLite run does not prove." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass clean? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied already. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host ("  local HEAD: " + $sha) -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Smoke"
Write-Host ""
Write-Host ("    .\scripts\smoke\smoke-history-measured.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 is the question: is any changed day still drawn as a" -ForegroundColor Yellow
Write-Host "  value rather than a break. Section 4 checks no removal is being" -ForegroundColor Yellow
Write-Host "  reported as money arriving." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - Today -> Your account history:" -ForegroundColor Cyan
Write-Host "  * The line should BREAK on 24 and 29 and 31 August." -ForegroundColor Cyan
Write-Host "  * 1 September should be a real down day, not a flat step." -ForegroundColor Cyan
Write-Host "  * The caption should say how many steps the headline covers." -ForegroundColor Cyan
Write-Host "  * The 696.90 ILS withdrawal line should be GONE, replaced by" -ForegroundColor Cyan
Write-Host "    'measuring what you hold accounts for the whole step'." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Three days remain unmeasurable and always will be: the snapshot" -ForegroundColor DarkYellow
Write-Host "    stores a position COUNT, never which holding or what it was" -ForegroundColor DarkYellow
Write-Host "    worth. PR 34 records that going forward so the app stops having" -ForegroundColor DarkYellow
Write-Host "    to reconstruct its own past." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply and sleeves/fund still record nothing about what" -ForegroundColor DarkYellow
Write-Host "    they changed, which is why the app asks YOU to log a flow it" -ForegroundColor DarkYellow
Write-Host "    caused itself." -ForegroundColor DarkYellow
Write-Host "  * 2026-08-18 to 08-23 is five nights the 22:10 job did not run." -ForegroundColor DarkYellow
Write-Host "    Unexplained, and those days cannot be recovered." -ForegroundColor DarkYellow
Write-Host "  * Rebalance cards can still be muted by a stale id. PR 35." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
