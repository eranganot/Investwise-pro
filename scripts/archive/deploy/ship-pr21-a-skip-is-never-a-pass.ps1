# SHIP PR 21 - the suite stops reporting things it never checked.
#
#   .\scripts\deploy\ship-pr21-a-skip-is-never-a-pass.ps1
#   .\scripts\deploy\ship-pr21-a-skip-is-never-a-pass.ps1 -SkipTests
#
# TESTS AND FIXTURES ONLY. No app behaviour changes, so there is no smoke script
# and nothing on the deployed app to look at. The gate IS the deliverable.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job: this PR makes four tests run there for the first time.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "tests/conftest.py",
    "tests/test_backtest_store_unavailable.py",
    "tests/test_card_claims.py",
    "tests/test_accept_conserves_nav.py",
    "scripts/deploy/ship-pr21-a-skip-is-never-a-pass.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 21: 'N skipped' was scenery, and one of the numbers was a real hole",
    "",
    "Eran, reading a green gate: 'why do we have those skipped tests on the",
    "flow? if they can be skipped -> remove them, if they can't be skipped ->",
    "why they are not running as expected?'",
    "",
    "The suite reported 4 skipped on SQLite and 12 on Postgres, and nobody had",
    "read either number in a long time. Both were run down. There were exactly",
    "two causes and they are not the same kind of thing.",
    "",
    "1. EIGHT POSTGRES SKIPS WERE A REAL COVERAGE HOLE, in the direction that",
    "matters. test_backtest_store_unavailable.py sabotages strategy_backtests to",
    "prove an OPTIONAL side table cannot take down the app - a reproduction of a",
    "real production incident: migration 0011 added last_error / last_error_at,",
    "the deploy landed before 'alembic upgrade head' ran, every SELECT hit an",
    "undefined column, and /strategies, /strategies/backtests and the",
    "recommendations pipeline went with it.",
    "",
    "It did the sabotage with sqlite3.connect() and skipped on anything else, so",
    "those four tests NEVER RAN ON POSTGRES - which is what production is.",
    "Verifying a production incident only on the backend production does not use",
    "is close to not verifying it, and it was reported as eight skips nobody",
    "read. Nothing about the sabotage needed SQLite; only the connection did.",
    "_PRE_0011 is valid DDL on both backends. It now runs through a throwaway",
    "NullPool async engine in its own loop - the reason conftest.own_session",
    "exists - and passes 4/4 on Postgres and 4/4 on SQLite.",
    "",
    "2. FOUR SKIPS ON BOTH WERE NOISE, and noise is where a real skip hides.",
    "test_no_card_funds_itself_by_selling_a_sleeve was parametrised over all five",
    "adversarial books and skipped the four with no sleeves. It was doing the",
    "right thing - it ran and passed on equities_overweight, the book built for",
    "it. But a test announcing four times per run that it does not apply is how",
    "'4 skipped' became scenery. It is now parametrised over SLEEVE_BOOKS, and an",
    "empty sleeve set is a FAILURE rather than a skip: the book declares its",
    "sleeves, so if sleeve_targets stops resolving them the test would otherwise",
    "pass vacuously forever.",
    "",
    "3. THE CHECK THAT STOPS THE NEXT ONE. The smoke scripts have said 'a SKIP is",
    "never a PASS' for months; the suite did not. conftest.py now fails the run",
    "on ANY skip whose reason is not registered in _ALLOWED_SKIPS, printing the",
    "node id and the reason. That list is currently EMPTY: after the two fixes",
    "above, both backends run with ZERO skips. Registering one is a decision",
    "someone makes and writes a reason for, which is the whole difference from a",
    "count that drifts. Verified: a deliberately unregistered skip exits 1.",
    "",
    "4. A FIXTURE THAT BELIEVED IT PINNED PRICES, AND DID NOT.",
    "test_accept_conserves_nav.py::pinned_prices patched registry.guarded_quote",
    "and nothing else. But pricing_service and strategy_service each do",
    "'from app.providers.registry import guarded_quote' at import time, so they",
    "bind the function once and a patch on the registry never reaches them.",
    "MEASURED: pricing_service.guarded_quote is _quote -> False. Both of those",
    "modules WRITE prices (refresh_all_positions, buy_into_book), so the fixture",
    "left two writers quoting the real synthetic provider on a book it was",
    "holding still. A test that thinks it controls its inputs and does not is the",
    "same failure as one that skips.",
    "",
    "5. THE ONE PLACE THE DIAGNOSTIC WAS NEEDED WAS THE ONE PLACE IT WAS OFF.",
    "test_accept_conserves_nav.py called logging.disable(logging.CRITICAL), which",
    "silences the NAV CONSERVATION BREACH line PR 17 added for exactly this",
    "failure, naming every position that moved. Two CI sightings were diagnosed",
    "from a bare number as a result. It now disables only WARNING and below, and",
    "the assertion carries the API NAV, PR 17's in-session drift, the apply spec,",
    "every position that changed, and the full emitted card list.",
    "",
    "THE +739.2 ILS LEAK IS NOT FIXED BY THIS, AND PR 20 DID NOT FIX IT EITHER.",
    "CI #180 failed on the SQLITE job with +739.22 on the same card, with PR 20",
    "applied. Ruled out so far, by observation: _rebalance_to's own writes (its",
    "guard compares total NAV over all positions to 1.0 ILS with the formula",
    "/portfolio uses); build_recommendations mutating inside the accept bracket",
    "(six consecutive GETs moved NAV by exactly 0.0000); card ORDER (emitted,",
    "reversed, rebalance-last - worst 0.12); and NOW PR 20's currency flip, which",
    "is applied in #180 and did not stop it. That candidate was named 'leading'",
    "and deliberately not claimed as proven. It was wrong.",
    "",
    "ROOT CAUSE: UNKNOWN. CONFIDENCE: nothing. This PR ships the instrumentation",
    "that makes the next occurrence self-explaining and NO SPECULATIVE FIX. If",
    "the in-session drift is ~0 while the API NAV moved, the change did not come",
    "from the handler at all, and that one line halves the candidates.",
    "",
    "Gate: SQLite 1355 passed / 0 skipped; Postgres 1355 passed / 0 skipped; ruff check app tests",
    "clean. ZERO SKIPS ON BOTH BACKENDS, for the first time.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host "  Nothing on the deployed app. Tests and fixtures only." -ForegroundColor Yellow
Write-Host ""
Write-Host "  What changes is what a green run MEANS:" -ForegroundColor Yellow
Write-Host "   * four tests that never ran on Postgres now do" -ForegroundColor Yellow
Write-Host "   * both backends report ZERO skips" -ForegroundColor Yellow
Write-Host "   * an unregistered skip now FAILS the run instead of scrolling past" -ForegroundColor Yellow
Write-Host ""
Write-Host "  It does NOT fix the +739.2 ILS leak. That is still unexplained," -ForegroundColor Yellow
Write-Host "  and CI can still go red on it. What it does is make the next" -ForegroundColor Yellow
Write-Host "  occurrence print everything needed to diagnose it." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. On a PR whose whole subject is what"
    Warn "a green gate means, that is a strange flag to pass."
} else {
    Invoke-Suite -Focus @(
        "tests/test_backtest_store_unavailable.py",
        "tests/test_card_claims.py",
        "tests/test_accept_conserves_nav.py")
    Write-Host ""
    Write-Host "  READ THE SKIP COUNT ON THE FULL RUN ABOVE. It must be 0." -ForegroundColor Yellow
    Write-Host "  Any skip now prints its node id and reason and fails the run." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Then run test-postgres. This PR makes four tests run there for" -ForegroundColor Yellow
    Write-Host "  the first time, so it is the run that proves the point." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, with 0 skipped? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "4. Push, and let CI gate it"
Push-AndWatch

Step "5. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * The CI summary line for BOTH test jobs should now read" -ForegroundColor Cyan
Write-Host "    '... passed' with NO 'skipped' at all." -ForegroundColor Cyan
Write-Host "  * test-postgres should report 4 more passing tests than before -" -ForegroundColor Cyan
Write-Host "    those are the ones that used to skip." -ForegroundColor Cyan
Write-Host "  * No smoke script: nothing about the deployed app changed." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED and CI can still go red on" -ForegroundColor DarkYellow
Write-Host "    it. PR 20 did not fix it - that is now ruled out by observation." -ForegroundColor DarkYellow
Write-Host "    When it next fires, the failure prints the API NAV, the" -ForegroundColor DarkYellow
Write-Host "    in-session drift, the apply spec, every position that moved and" -ForegroundColor DarkYellow
Write-Host "    the cards emitted. SEND ME THAT BLOCK - it is the observation" -ForegroundColor DarkYellow
Write-Host "    the diagnosis has been missing." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "    PR 22." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 23. After that the bucket is empty." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one. PR 24." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
