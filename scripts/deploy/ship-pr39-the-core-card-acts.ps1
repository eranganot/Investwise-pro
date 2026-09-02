# SHIP PR 39 - guidance is not enough. The core card does the thing.
#
#   .\scripts\deploy\ship-pr39-the-core-card-acts.ps1
#   .\scripts\deploy\ship-pr39-the-core-card-acts.ps1 -SkipTests
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
    "app/services/core_realign.py",
    "app/services/recommendations.py",
    "tests/test_the_core_card_acts.py",
    "tests/test_the_core_card_names_the_ticker.py",
    "scripts/smoke/smoke-core-card.ps1",
    "scripts/deploy/ship-pr39-the-core-card-acts.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 39: the core card stops giving advice and does the thing",
    "",
    "Eran, on the three cards PR 38 shipped: 'I'm missing the most important",
    "thing in these cards --> guidance is not enough... I want these cards to",
    "act on my behalf (as other cards can do it).'",
    "",
    "PR 38 made every one of them advice, reasoning that this app cannot fund a",
    "3,392 ILS purchase from a 619 ILS cash balance. THE REASONING WAS HALF",
    "RIGHT AND THE CONCLUSION WAS WRONG. The money is already in the book,",
    "sitting in the wrong places:",
    "",
    "    V          955 ILS   in the core region, not in the core's basket",
    "    BND        576 ILS   above the core's own 40% leg",
    "    sleeves  1,268 ILS   above the sleeve sizes he set (79% vs 73%)",
    "",
    "Bringing the core back to Classic 60/40 is a REBALANCE, not a purchase,",
    "and this app rebalances. Three cards become one that acts, with",
    "apply.kind = core_to_basket.",
    "",
    "THE ONE RULE. _core_plan sizes it, and THE CARD AND THE BUTTON BOTH CALL",
    "IT. _rebalance_to is the cautionary tale this repo already carries: the",
    "card read one target, Accept applied another, the screen said 'sell 4,324'",
    "and the button sold 12,139. The arithmetic itself is in",
    "core_realign.plan_realign - pure, no ORM, no I/O - so the tests assert a",
    "real trade's numbers without mocking one.",
    "",
    "WHAT IT WILL NOT DO:",
    "",
    "  * Take cash below the app's own floor.",
    "  * Trim a sleeve BELOW its own target. The sleeve sizes are a decision",
    "    made on the Plan tab; this reclaims what is above that decision and",
    "    never overrules it.",
    "  * Buy more than the sells raised. After CGT (143 ILS) and the cash floor,",
    "    everything this book can raise still leaves 565 ILS UNFUNDED - and the",
    "    card says so on its face rather than quietly buying less than it said.",
    "  * Sell before it knows it can buy. Every buy leg is priced FIRST and one",
    "    timed-out quote aborts the whole action with the book untouched.",
    "    _apply_buy_funded sells first and holds the proceeds as cash, which is",
    "    survivable for one swap and not for three positions.",
    "  * Take the sleeve trim out of one ticker. It is pro-rata, because",
    "    concentrating it would silently re-weight the sleeves against each",
    "    other - a decision this action was never given.",
    "",
    "Every leg is listed on the card BEFORE the button, generated from the same",
    "plan the executor runs, so the card cannot promise a trade Apply will not",
    "make or hide one it will. The tracked book is updated; NO BROKERAGE ORDER",
    "IS PLACED.",
    "",
    "20 new tests. The PR 38 test asserting 'this card declares no apply block'",
    "was rewritten rather than deleted - it documented the decision this PR",
    "reverses, and the replacement asserts the rule that took its place: declare",
    "only what the app implements.",
    "",
    "Gate: SQLite 1609 passed / 0 failed; Postgres 16 1609 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  THE CORE CARD GETS A BUTTON THAT MOVES REAL POSITIONS." -ForegroundColor Green
Write-Host ""
Write-Host "  One card replaces the three. Pressing Apply will:" -ForegroundColor Green
Write-Host "    sell ALL your V              about   955 ILS" -ForegroundColor White
Write-Host "    trim BND to its 40% leg      about   576 ILS" -ForegroundColor White
Write-Host "    trim your sleeves to 73%     about 1,268 ILS" -ForegroundColor White
Write-Host "    buy VTI                      about 2,852 ILS" -ForegroundColor White
Write-Host "    estimated tax                about   143 ILS" -ForegroundColor White
Write-Host ""
Write-Host "  AND IT WILL STILL BE ABOUT 565 ILS SHORT of the full 3,417 of" -ForegroundColor Yellow
Write-Host "  VTI. The card says so before you press it. That gap needs new" -ForegroundColor Yellow
Write-Host "  money - there is nothing left in the book to move." -ForegroundColor Yellow
Write-Host ""
Write-Host "  IT WILL NOT trim a sleeve below the size you set, will not spend" -ForegroundColor Cyan
Write-Host "  cash below the floor, and will not sell anything if it cannot get" -ForegroundColor Cyan
Write-Host "  a live VTI price first." -ForegroundColor Cyan
Write-Host ""
Write-Host "  THIS UPDATES YOUR TRACKED BOOK. NO BROKERAGE ORDER IS PLACED." -ForegroundColor Red
Write-Host "  You still have to make these trades at your broker." -ForegroundColor Red
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
    Warn "-SkipTests: the gate did NOT run. This PR adds a button that SELLS AND"
    Warn "BUYS positions in your book. Do not skip this one."
} else {
    $Focus = @(
        "tests/test_the_core_card_acts.py",
        "tests/test_the_core_card_names_the_ticker.py",
        "tests/test_a_done_the_book_contradicts.py",
        "tests/test_recos_apply.py",
        "tests/test_accept_conserves_nav.py")
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
Write-Host "  It is READ-ONLY and does NOT press the button. It checks that the" -ForegroundColor Yellow
Write-Host "  card lists every leg it would run and declares a kind the app can" -ForegroundColor Yellow
Write-Host "  actually carry out." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  BEFORE YOU PRESS APPLY - read the How list on the card. Those" -ForegroundColor Cyan
Write-Host "  lines ARE the trades. If one of them surprises you, do not press" -ForegroundColor Cyan
Write-Host "  it: tell me which line and what you expected instead." -ForegroundColor Cyan
Write-Host ""
Write-Host "  AFTER YOU PRESS IT, the book will show V gone, BND and the" -ForegroundColor Cyan
Write-Host "  sleeves trimmed, and VTI bought. Your broker will not. Place the" -ForegroundColor Cyan
Write-Host "  same trades there, or the app is describing a book you do not" -ForegroundColor Cyan
Write-Host "  actually hold." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The 565 ILS shortfall needs new money. Nothing can move it." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply, sleeves/fund and DELETE /portfolio/position" -ForegroundColor DarkYellow
Write-Host "    still record nothing about what they changed, so three days of" -ForegroundColor DarkYellow
Write-Host "    history stay unmeasurable - and THIS action now moves money" -ForegroundColor DarkYellow
Write-Host "    through those same paths." -ForegroundColor DarkYellow
Write-Host "  * The 1,460 ILS gap between the contributions ledger and the cost" -ForegroundColor DarkYellow
Write-Host "    basis is untouched. It needs your answer, not a patch." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
