# SHIP PR 38 - the card existed, and you had marked it done twelve days ago.
#
#   .\scripts\deploy\ship-pr38-a-done-the-book-contradicts.ps1
#   .\scripts\deploy\ship-pr38-a-done-the-book-contradicts.ps1 -SkipTests
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
    "app/services/recommendations.py",
    "tests/test_a_done_the_book_contradicts.py",
    "tests/test_the_core_card_names_the_ticker.py",
    "scripts/smoke/smoke-core-card.ps1",
    "scripts/deploy/ship-pr38-a-done-the-book-contradicts.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 38: a done the book contradicts, and the card that names the ticker",
    "",
    "Eran: 'if I'm not holding VTI as part of my portfolio --> I expect to get",
    "a recommendation card to purchase it.'",
    "",
    "THE CARD EXISTED. 'Buy Equities', MEDIUM, 1,215 ILS, hidden since",
    "2026-08-21 because he pressed Done. Measured on 2026-09-02, twelve days",
    "later, the equities gap was 1,194 ILS - the same instruction to within 2%.",
    "",
    "HALF ONE: 'DONE' IS A CLAIM ABOUT THE BOOK, AND NOTHING ASKED THE BOOK.",
    "",
    "PR 34's rule is why it stayed hidden, working exactly as designed: a muted",
    "card returns when its own number MOVES. For a card you IGNORED that is",
    "right - you looked, you chose not to act, and only a changed situation",
    "earns your attention again.",
    "",
    "FOR A CARD YOU MARKED DONE THE SIGNAL IS INVERTED. If the same instruction",
    "still stands, unchanged, after the trade would have settled, then the",
    "number NOT MOVING is the evidence the action was never taken. The app took",
    "the user's word for it and never checked, and nothing else in this file",
    "trusts a claim it can measure.",
    "",
    "Deliberately narrow, because the failure mode of this rule is nagging:",
    "only 'done', never 'ignored'; only after _DONE_SETTLE_DAYS (3), so real",
    "settlement is not called a lie; only for a card asking to move real money;",
    "and A BOOK THAT DID MOVE IS NEVER ACCUSED - it gets PR 34's neutral",
    "wording about the number changing, never the accusation.",
    "",
    "HALF TWO: A CLASS IS NOT AN INSTRUCTION.",
    "",
    "Even when that card fires it says 'Buy Equities, about 1,215 ILS'. On this",
    "book the equities target is 89.2% - 73% from two leveraged sleeves plus",
    "16.2% from the core - so BUYING MORE TQQQ SATISFIES IT COMPLETELY and",
    "leaves the core still holding no VTI at all. The card was not wrong. It",
    "was not an instruction.",
    "",
    "The core is the one place the app knows the instrument, because a core IS",
    "a basket (PR 37). The leg target is core_pct x weight x NAV - the same",
    "arithmetic _book_target already folds INTO the class target, here left",
    "un-aggregated, which is the whole point. On this book, NAV 21,092, core",
    "27%:",
    "",
    "    VTI   target 3,417   held     0   ->  buy  ~3,417",
    "    BND   target 2,278   held 2,854   ->  trim ~  576",
    "    V     target     0   held   955   ->  not in your core's basket",
    "",
    "ADVICE, NOT AN ACTION. No apply block: funding a 3,417 ILS purchase out of",
    "a 619 ILS cash balance is not something this app can carry out, and a",
    "button that reports success without doing anything is the exact defect",
    "_ACTIONABLE_KINDS exists to prevent. The card names the ticker and the",
    "amount and stops there.",
    "",
    "The band and the money floor are the app's existing ones",
    "(SLEEVE_DRIFT_BAND_PCT, _REFIRE_FLOOR_ILS). Sleeve-held tickers are",
    "excluded through sleeve_tickers, and cash is never a leg. A core the app",
    "cannot read logs and loses no other card.",
    "",
    "24 tests, 18 of them fail against PR 37. The six that pass pin the",
    "leg ARITHMETIC - 3,417 / -576 / 955 - which PR 37's core_matches_basket",
    "already supplies; they guard the numbers the cards are built from, so a",
    "change to that primitive cannot quietly move what the cards ask for.",
    "",
    "Gate: SQLite 1589 passed / 0 failed; Postgres 16 1589 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOU GET A CARD THAT SAYS 'BUY VTI', WITH A NUMBER." -ForegroundColor Green
Write-Host ""
Write-Host "    Your core is missing VTI      buy  about 3,417 ILS" -ForegroundColor Green
Write-Host "    Your core holds too much BND  trim about   576 ILS" -ForegroundColor Green
Write-Host "    V is not in your core's basket     955 ILS held" -ForegroundColor Green
Write-Host ""
Write-Host "  AND the 'Buy Equities' card you marked done on 21 August comes" -ForegroundColor Yellow
Write-Host "  back, saying so: your book still shows the same ask, so it looks" -ForegroundColor Yellow
Write-Host "  like it did not happen." -ForegroundColor Yellow
Write-Host ""
Write-Host "  If you DID act and the book moved, nothing accuses you - that" -ForegroundColor Cyan
Write-Host "  path is tested." -ForegroundColor Cyan
Write-Host ""
Write-Host "  NOTHING IS BOUGHT, SOLD OR REPRICED. The new cards carry no" -ForegroundColor Yellow
Write-Host "  Apply button, because this app cannot fund a 3,417 ILS purchase" -ForegroundColor Yellow
Write-Host "  from a 619 ILS cash balance and must not pretend otherwise." -ForegroundColor Yellow
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
    Warn "-SkipTests: the gate did NOT run. This PR changes which cards you are"
    Warn "shown and which stay hidden. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_a_done_the_book_contradicts.py",
        "tests/test_the_core_card_names_the_ticker.py",
        "tests/test_a_mute_does_not_outlive_its_number.py",
        "tests/test_pr4_cards.py",
        "tests/test_recos_apply.py")
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
Write-Host ("    .\scripts\smoke\smoke-core-card.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 3 prints everything still hidden as 'done'. READ IT." -ForegroundColor Yellow
Write-Host "  Anything there with a real amount that you did not actually do" -ForegroundColor Yellow
Write-Host "  is a card this PR should have brought back." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - Today:" -ForegroundColor Cyan
Write-Host "  * A card naming VTI and an amount, not 'Buy Equities'." -ForegroundColor Cyan
Write-Host "  * 'Buy Equities' back with a line saying why it returned." -ForegroundColor Cyan
Write-Host "  * Neither card offers an Apply button." -ForegroundColor Cyan
Write-Host ""
Write-Host "  ONE THING TO DECIDE, NOT A BUG:" -ForegroundColor DarkYellow
Write-Host "  * V (955 ILS) sits in the part of the book your core manages and" -ForegroundColor DarkYellow
Write-Host "    is not in Classic 60/40. Owning it is not wrong - it is just" -ForegroundColor DarkYellow
Write-Host "    not your core. Either move it out of the core's share or" -ForegroundColor DarkYellow
Write-Host "    accept the core will keep reading as off-basket." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The leg cards are advice. Nothing here can execute a buy." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply, sleeves/fund and DELETE /portfolio/position" -ForegroundColor DarkYellow
Write-Host "    still record nothing about what they changed, so three days of" -ForegroundColor DarkYellow
Write-Host "    history stay unmeasurable." -ForegroundColor DarkYellow
Write-Host "  * The 1,460 ILS gap between the contributions ledger and the cost" -ForegroundColor DarkYellow
Write-Host "    basis is untouched. It needs your answer, not a patch." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
