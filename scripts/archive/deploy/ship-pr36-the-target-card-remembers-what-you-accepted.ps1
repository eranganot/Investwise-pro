# SHIP PR 36 - the card that owns two boxes forgot which numbers you put in them.
#
#   .\scripts\deploy\ship-pr36-the-target-card-remembers-what-you-accepted.ps1
#   .\scripts\deploy\ship-pr36-the-target-card-remembers-what-you-accepted.ps1 -SkipTests
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
    "app/api/routes/plan.py",
    "app/services/target_apply.py",
    "app/services/measured_assumptions.py",
    "app/static_app/index.html",
    "tests/test_the_target_card_remembers_what_you_accepted.py",
    "scripts/smoke/smoke-target-accepted.ps1",
    "scripts/deploy/ship-pr36-the-target-card-remembers-what-you-accepted.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 36: the target card remembers what you accepted",
    "",
    "Eran: 'I want these fields (beat the benchmark & without exceeding) to be",
    "the default of what I have approved.'",
    "",
    "He had accepted 12 / 49 and the card kept opening on a measurement. Both",
    "boxes were seeded in _seedTarget: the ceiling from",
    "performance(range=MAX).max_drawdown_pct, the target from",
    "max(0, ceil(excess_cagr)). That was the right design while nothing",
    "remembered an answer - a made-up default is the card guessing on the",
    "user's behalf, which is what this card exists to stop.",
    "",
    "But an ACCEPTED target is neither a guess nor a measurement. It is the",
    "question he decided to ask and then acted on, and IT HAS BEEN RECORDED ON",
    "EVERY APPLY SINCE PHASE A: tgtAccept sends context.excess_pct and",
    "context.ceiling_pct, and _record writes them verbatim onto the",
    "PlanApplication row. Nothing had ever read them back.",
    "",
    "So this is a READ PATH AND NO NEW STORAGE. last_accepted() takes the",
    "newest application row; GET /api/v1/plan/target/accepted returns it;",
    "_seedTarget asks for it before it measures anything.",
    "",
    "TWO RULES, both about not putting words in the user's mouth:",
    "",
    "1. AN UNDO WITHDRAWS THE ACCEPTANCE. Undo restores the sizes the apply",
    "   replaced, so the target that produced them stops describing the book.",
    "   Seeding from it afterwards would put a decision he reversed back in the",
    "   box. The newest row decides, in both directions - an apply after an undo",
    "   stands again.",
    "",
    "2. HALF A SEED IS WORSE THAN NONE. A row that lost one of the two figures",
    "   returns null rather than a pair, because one remembered number beside",
    "   one measured one, with nothing saying which is which, is the confusion",
    "   this card is for.",
    "",
    "The measured pair is still fetched, still quoted in the note above the",
    "boxes, and one tap away ('use what the book measures now'). Silently",
    "replacing a remembered decision with a measurement is the mirror of the",
    "bug, so the note says WHICH of the two you are looking at every time.",
    "",
    "ISSUE 7 RIDES ALONG - 'why only 78.8% of my book measured? why not 100%?'",
    "",
    "The old headline printed the percentage and then said 'the rest uses the",
    "instrument table' without ever naming what the rest WAS, which invites the",
    "reader to supply the missing half himself. So the line names both halves.",
    "",
    "THE FIRST DRAFT OF THAT LINE WAS FALSE and Eran caught it. It said the",
    "remainder was 'your core and cash, which run no strategy to measure'. His",
    "reply: 'that is not true.... the sleeves have their strategy and the core",
    "has its own strategy - the portfolio is being managed by two different",
    "strategies.'",
    "",
    "He is right, and this repo agrees with him. core_search ranks FOURTEEN",
    "candidate cores on measured return, volatility and drawdown, through",
    "blend.core_spec + blend.measure_blend. His core is bal_6040: a basket plus",
    "a rebalance discipline, sized at 27% of the book. A core is a strategy.",
    "",
    "The real reason his core is unmeasured is that book_assumptions NEVER ASKS",
    "FOR THE CORE ROW. It walks the sleeves, then dumps every unclaimed position",
    "into 'unallocated' and prices it ticker by ticker - so even a core held",
    "exactly to its basket would be unmeasured. And that substitution is not",
    "neutral: a per-ticker table has no covariance term, so pricing a rebalanced",
    "60/40 as the weighted average of a VTI row and a BND row OVERSTATES THE",
    "CORE'S VOLATILITY BY CONSTRUCTION.",
    "",
    "This PR does not close that gap - it stops the app explaining it away as a",
    "property of the core. The headline now says 'this app does not yet measure",
    "a core the way it measures a sleeve. That is a gap here, not a verdict on",
    "your core.' A test asserts the phrase 'no strategy' can never come back.",
    "PR 37 does the measuring.",
    "",
    "14 tests, 13 of them fail against PR 35. The one that passes guards the",
    "fully-static-book branch of the headline, deliberately untouched.",
    "",
    "Gate: SQLite 1544 passed / 0 failed; Postgres 16 1544 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  THE TARGET CARD OPENS ON THE TARGET YOU ACCEPTED." -ForegroundColor Green
Write-Host ""
Write-Host "  You accepted beat-12% / ceiling-49%. The card will now open on" -ForegroundColor Green
Write-Host "  12 and 49, with a line above the boxes saying they are YOUR" -ForegroundColor Green
Write-Host "  target and not a measurement." -ForegroundColor Green
Write-Host ""
Write-Host "  If you UNDO that apply, the card goes back to measuring. A target" -ForegroundColor Cyan
Write-Host "  you reversed is not a target you hold." -ForegroundColor Cyan
Write-Host ""
Write-Host "  The assumptions line stops printing a bare 78.8%. It names the" -ForegroundColor Cyan
Write-Host "  other 21.2% as your core and cash, and says the app not measuring" -ForegroundColor Cyan
Write-Host "  a core is a GAP HERE - not the core having no strategy. PR 37" -ForegroundColor Cyan
Write-Host "  does the measuring." -ForegroundColor Cyan
Write-Host ""
Write-Host "  NOTHING IS BOUGHT, SOLD, RESIZED OR REPRICED. The new endpoint" -ForegroundColor Yellow
Write-Host "  is a GET and stores nothing." -ForegroundColor Yellow
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
    Warn "-SkipTests: the gate did NOT run. This PR changes what the card puts"
    Warn "in front of you before you accept a resize. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_the_target_card_remembers_what_you_accepted.py",
        "tests/test_target_apply.py",
        "tests/test_target_recommend.py",
        "tests/test_the_numbers_come_from_measurement.py",
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
Write-Host ("    .\scripts\smoke\smoke-target-accepted.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 3 checks the endpoint against the application log. If the" -ForegroundColor Yellow
Write-Host "  two disagree, the read path is inventing and the smoke fails." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - open Plan, then the target card:" -ForegroundColor Cyan
Write-Host "  * The two boxes hold what you accepted, not a measurement." -ForegroundColor Cyan
Write-Host "  * The line above them says so, and offers the measured pair." -ForegroundColor Cyan
Write-Host "  * Undo the apply and reload: the boxes measure again." -ForegroundColor Cyan
Write-Host "  * The assumptions line names your core and cash by name, and" -ForegroundColor Cyan
Write-Host "    does NOT say your core has no strategy. It has one." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * YOUR CORE IS STILL NOT MEASURED. book_assumptions never asks" -ForegroundColor DarkYellow
Write-Host "    for the core row, so bal_6040 is priced ticker by ticker and" -ForegroundColor DarkYellow
Write-Host "    its volatility is overstated. PR 37." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply, sleeves/fund and DELETE /portfolio/position" -ForegroundColor DarkYellow
Write-Host "    still record nothing about what they changed, so three days of" -ForegroundColor DarkYellow
Write-Host "    history stay unmeasurable." -ForegroundColor DarkYellow
Write-Host "  * The 1,460 ILS gap between the contributions ledger and the cost" -ForegroundColor DarkYellow
Write-Host "    basis is untouched. It needs your answer, not a patch." -ForegroundColor DarkYellow
Write-Host "  * Five snapshot nights, 18-23 Aug, were never taken and cannot be" -ForegroundColor DarkYellow
Write-Host "    recovered." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
