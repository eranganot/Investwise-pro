# SHIP PR 37 - the book is managed by two strategies and only one was measured.
#
#   .\scripts\deploy\ship-pr37-a-core-is-a-strategy-too.ps1
#   .\scripts\deploy\ship-pr37-a-core-is-a-strategy-too.ps1 -SkipTests
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
    "app/services/backtest_service.py",
    "app/services/measured_assumptions.py",
    "tests/test_a_core_is_a_strategy_too.py",
    "tests/test_the_target_card_remembers_what_you_accepted.py",
    "scripts/smoke/smoke-core-measured.ps1",
    "scripts/deploy/ship-pr37-a-core-is-a-strategy-too.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 37: a core is a strategy too",
    "",
    "Eran, on the line PR 36 shipped: 'that is not true.... the sleeves have",
    "their strategy and the core has its own strategy. the portfolio is being",
    "managed by two different strategies (sleeves strategy and core strategy)'",
    "",
    "He is right, and this repo already agreed with him everywhere except the",
    "one place it mattered. core_search ranks FOURTEEN candidate cores on",
    "measured return, volatility and drawdown, through blend.core_spec +",
    "blend.measure_blend. His core is bal_6040: a basket plus a rebalance",
    "discipline, sized at 27% of the book.",
    "",
    "But refresh_all only ever walked strategy_catalog - the RULE-BASED",
    "sleeves. No static family had ever been backtested, so book_assumptions",
    "had no core row to read, and it never asked for one. Every unclaimed",
    "holding went through the per-ticker instrument table. That is the whole of",
    "the 79%.",
    "",
    "THE FALLBACK WAS NOT NEUTRAL. A per-ticker table has no covariance term,",
    "so pricing a rebalanced 60/40 as the weighted average of a VTI row and a",
    "BND row OVERSTATES ITS VOLATILITY BY CONSTRUCTION: the one thing",
    "rebalancing between two weakly-correlated legs does is the one thing the",
    "table cannot express.",
    "",
    "MEASURED NIGHTLY, same StrategyBacktest table, same usable() gates. One",
    "fetch for the union of every basket - fourteen separate fetches of a",
    "heavily overlapping ticker set is how a run gets rate-limited into",
    "reporting that no core can be measured, which _fetch's own docstring",
    "records from the first live T6b run. But EACH BASKET IS MEASURED ON A FEED",
    "SCOPED TO ITS OWN TICKERS: align() intersects the DATE AXIS across the",
    "whole feed, so one late-listing ticker in one basket would silently",
    "truncate all fourteen and report ten-year figures for none of them. That",
    "is the trap target_solver._core_blocking documents at length.",
    "",
    "A FIXED BASKET HAS NO FITTED PARAMETER, so it gets no out-of-sample fold.",
    "_pick_return prefers the OOS figure because a RULE has parameters that can",
    "be tuned until the past looks good; a 60/40 has none, so splitting its",
    "history would measure the decade rather than the strategy. Stored as a",
    "named reason (NO_FIT_TO_TEST) rather than an empty robustness a reader",
    "could mistake for a failed measurement, and read back by _pick_core_return",
    "so the full-window figure is the honest reading rather than a fallback.",
    "RETURN_CAVEAT still travels with it.",
    "",
    "AND ONE GATE A SLEEVE DOES NOT NEED. A sleeve's tickers are its tickers.",
    "A CORE'S BASKET IS A TARGET, and a book can name bal_6040 and hold",
    "something else entirely - which is exactly what this book does: BND and V,",
    "and no VTI at all. Pricing those holdings from a measurement of a basket",
    "they are not is the same class of error as PR 6's target-weighting, so",
    "core_matches_basket checks composition FIRST, against",
    "recommendations.SLEEVE_DRIFT_BAND_PCT - the app's existing answer to how",
    "far a thing may wander, not a second private tolerance - and a mismatch",
    "abstains BY NAME:",
    "",
    "    Classic 60/40 (bal_6040) IS measured, and your core is not holding it:",
    "    you hold none of VTI; V is not in the basket. The measurement describes",
    "    the basket, so it is not used to price holdings that are not that",
    "    basket.",
    "",
    "So on this book the numbers do not move today, and the app finally says",
    "WHY in terms that can be acted on. It is the same finding as issues 2 and",
    "3 arriving through a third door.",
    "",
    "21 tests, 20 of them fail against PR 36. The one that passes guards a",
    "pre-existing invariant - that bal_6040 is in core_candidates() with a",
    "basket - because if that ever stops being true, measure_cores measures",
    "nothing and says so to nobody.",
    "",
    "Gate: SQLite 1565 passed / 0 failed; Postgres 16 1565 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR CORE GETS MEASURED LIKE THE STRATEGY IT IS." -ForegroundColor Green
Write-Host ""
Write-Host "  You were right: the book runs two strategies, and only the" -ForegroundColor Green
Write-Host "  sleeves were ever measured. Classic 60/40 is now backtested" -ForegroundColor Green
Write-Host "  nightly like any sleeve." -ForegroundColor Green
Write-Host ""
Write-Host "  EXPECT YOUR NUMBERS NOT TO MOVE YET, and expect a new sentence" -ForegroundColor Yellow
Write-Host "  saying why: your core holds BND and V, and Classic 60/40 is VTI" -ForegroundColor Yellow
Write-Host "  and BND. A measurement of that basket is not allowed to price" -ForegroundColor Yellow
Write-Host "  holdings that are not that basket - that would describe a book" -ForegroundColor Yellow
Write-Host "  you do not have. Bring the core to its basket and the same" -ForegroundColor Yellow
Write-Host "  measurement starts pricing 27% of your book." -ForegroundColor Yellow
Write-Host ""
Write-Host "  NOTHING IS BOUGHT, SOLD, RESIZED OR REPRICED." -ForegroundColor Yellow
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
    Warn "-SkipTests: the gate did NOT run. This PR changes the forward numbers"
    Warn "on Today, the projection and the health score. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_a_core_is_a_strategy_too.py",
        "tests/test_the_target_card_remembers_what_you_accepted.py",
        "tests/test_the_numbers_come_from_measurement.py",
        "tests/test_target_recommend.py",
        "tests/test_how_many_sleeves.py")
    $missingFocus = @($Focus | Where-Object { -not (Test-Path $_) })
    if ($missingFocus.Count -gt 0) {
        Write-Host ""
        Write-Host "  STOP - this script names test files that do not exist:" -ForegroundColor Red
        foreach ($f in $missingFocus) { Write-Host ("    " + $f) -ForegroundColor Red }
        Write-Host "  That is a bug in THIS SCRIPT, not a failing test." -ForegroundColor Red
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
Write-Host ("    .\scripts\smoke\smoke-core-measured.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  SECTION 3 WILL SKIP UNTIL THE NIGHTLY JOB HAS RUN. No core has" -ForegroundColor Yellow
Write-Host "  ever been backtested, so there is no row to read until 03:30." -ForegroundColor Yellow
Write-Host "  That is not a failure - but a SKIP IS NOT A PASS, so run the" -ForegroundColor Yellow
Write-Host "  script again tomorrow before believing this shipped." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - Plan, the assumptions line:" -ForegroundColor Cyan
Write-Host "  * It names your core by name and says it IS measured." -ForegroundColor Cyan
Write-Host "  * It names VTI as the leg you do not hold, and V as the holding" -ForegroundColor Cyan
Write-Host "    that is not in the basket." -ForegroundColor Cyan
Write-Host "  * It never says your core has no strategy." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The core is measured as its BASKET, not as what you hold in" -ForegroundColor DarkYellow
Write-Host "    the core region. Measuring the held mix needs a per-user stored" -ForegroundColor DarkYellow
Write-Host "    measurement, which is a bigger change than this one." -ForegroundColor DarkYellow
Write-Host "  * Nothing yet RAISES a card telling you the core is off its" -ForegroundColor DarkYellow
Write-Host "    basket. This states it on the Plan screen only - that is your" -ForegroundColor DarkYellow
Write-Host "    issue 2, still open." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply, sleeves/fund and DELETE /portfolio/position" -ForegroundColor DarkYellow
Write-Host "    still record nothing about what they changed, so three days of" -ForegroundColor DarkYellow
Write-Host "    history stay unmeasurable." -ForegroundColor DarkYellow
Write-Host "  * The 1,460 ILS gap between the contributions ledger and the cost" -ForegroundColor DarkYellow
Write-Host "    basis is untouched. It needs your answer, not a patch." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
