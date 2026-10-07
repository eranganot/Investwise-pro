# SHIP PR 34 - nine cards were hidden and two of them were the ones he needed.
#
#   .\scripts\deploy\ship-pr34-a-mute-does-not-outlive-its-number.ps1
#   .\scripts\deploy\ship-pr34-a-mute-does-not-outlive-its-number.ps1 -SkipTests
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
    "app/api/routes/recommendations.py",
    "app/services/recommendations.py",
    "app/static_app/index.html",
    "tests/test_a_mute_does_not_outlive_its_number.py",
    "scripts/smoke/smoke-hidden-cards.ps1",
    "scripts/deploy/ship-pr34-a-mute-does-not-outlive-its-number.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 34: a mute does not outlive its number",
    "",
    "Nine cards were hidden, and two of them were the ones he needed. Read",
    "live from production on 2026-09-02, while Eran was asking why the app had",
    "nothing to say about a book that was off target:",
    "",
    "    /api/v1/mix              rebalance_required: true",
    "                             BUY  Fixed Income  2,243.96",
    "                             SELL Equities      1,626.87",
    "    /api/v1/recommendations  4 cards, all screener verdicts",
    "                             dismissed_count: 4   completed_count: 5",
    "",
    "A card's id is sha1(category|ticker) - no amount, no date - so the card",
    "you file today and the card the same condition generates next week are",
    "THE SAME id, and one click mutes both for a fortnight however far the",
    "number travels in between. _DONE_TTL_DAYS was already cut from 90 to 14",
    "for this reason, and the comment that did it says so: 'making the id carry",
    "its own content is the actual fix and is a separate change.' This is that",
    "change, AND IT DOES NOT TOUCH THE ID.",
    "",
    "WHY NOT A CONTENT-BEARING ID. The id is what Restore, the audit log and",
    "the push dedupe all key on, and quantising an amount into it churns on",
    "every boundary: 1,499 and 1,501 become different cards, and a card that",
    "re-fires on a 0.1% move is as useless as one that never re-fires. So the",
    "MARK remembers the situation instead, and the situation is compared. No",
    "boundary, no new identity, and the comparison is legible in the reason the",
    "card carries back.",
    "",
    "TWO THINGS UN-MUTE A CARD (moved_on):",
    "",
    "  * its SEVERITY went up. 'I dealt with that' was said about a MEDIUM and",
    "    the app now calls it HIGH, which is new information. A DE-escalation",
    "    does not: getting less urgent is not a reason to interrupt someone.",
    "  * its OWN NUMBER moved by more than 25%, floored at 100 ILS so a card",
    "    asking for pocket change does not re-fire on rounding.",
    "",
    "A mark with no situation - every mark made before this ships, and every",
    "card that carries no est_amount - keeps EXACTLY the old behaviour: muted",
    "until its TTL runs out. Nothing is migrated and no existing mute silently",
    "changes meaning. _load_map reads all three stored shapes (a bare list of",
    "ids, {id: iso}, and the new dict) and writes only the newest.",
    "",
    "THE AMOUNT COMES FROM THE CLIENT, because rebuilding the whole Today list",
    "on a button press would put a multi-second sweep on the slowest path in",
    "the app. That trust is bounded BY CONSTRUCTION: moved_on only ever uses it",
    "to bring a card BACK early. A wrong or stale value cannot extend a mute,",
    "hide anything, or move a single number in the book. A junk value is",
    "ignored outright and a test pins that.",
    "",
    "A CARD THAT COMES BACK SAYS WHY IT CAME BACK. Reappearing with no",
    "explanation reads as the app having forgotten you dealt with it - which is",
    "the complaint the mute exists to prevent, arriving through another door.",
    "",
    "THE SECOND HALF WAS MISDIAGNOSED AT FIRST AND THE CORRECTION MATTERS. The",
    "'N suggestions are hidden' line already existed and already rendered next",
    "to live cards - the count was never missing. What was missing is WHICH: it",
    "said '4 other suggestions are ignored' while the two rebalance cards sat",
    "inside that 4 and nothing named them. hidden_cards now carries id, title,",
    "severity, dimension, how it was hidden and when, sorted by severity, and",
    "the screen lists them under both the empty state and the live list.",
    "",
    "15 tests; 14 FAIL AGAINST PR 33, three of them behaviourally through the",
    "route rather than by a missing import. The one that passes is",
    "test_dismissing_without_a_body_still_works - a deliberate no-change guard",
    "for every existing caller that posts no body, so it must pass on both.",
    "",
    "Gate: SQLite 1515 passed / 0 failed; Postgres 16 1515 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  CARDS YOU HID MAY COME BACK, and they will say why." -ForegroundColor Green
Write-Host ""
Write-Host "  A card returns when its own number has moved more than 25%, or" -ForegroundColor Green
Write-Host "  when the app has raised its severity since you filed it. Anything" -ForegroundColor Green
Write-Host "  you hid BEFORE this deploy keeps the old behaviour - it stays" -ForegroundColor Green
Write-Host "  hidden until its window runs out. Nothing is migrated." -ForegroundColor Green
Write-Host ""
Write-Host "  And the hidden list is now NAMED, not just counted. If a rebalance" -ForegroundColor Cyan
Write-Host "  card is being suppressed you will see its title." -ForegroundColor Cyan
Write-Host ""
Write-Host "  NOTHING IS BOUGHT, SOLD OR REPRICED." -ForegroundColor Yellow
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
    Warn "-SkipTests: the gate did NOT run. This PR changes which cards reach"
    Warn "your Today screen. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_a_mute_does_not_outlive_its_number.py",
        "tests/test_accept_honesty.py",
        "tests/test_pr4_cards.py",
        "tests/test_card_claims.py")
    # A FOCUS FILE THAT DOES NOT EXIST IS A SCRIPT BUG, NOT A TEST FAILURE.
    # pytest exits non-zero on "file or directory not found" and Invoke-Suite
    # then prints "Focused tests FAILED - not committing", sending the reader
    # after a broken test that does not exist. This guard was added after
    # exactly that happened on PR 33.
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
Write-Host ("    .\scripts\smoke\smoke-hidden-cards.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 lists every hidden card by name. Section 3 answers the" -ForegroundColor Yellow
Write-Host "  question you actually asked: is the mix card among them." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - Today:" -ForegroundColor Cyan
Write-Host "  * The hidden line should now NAME the cards, not just count them." -ForegroundColor Cyan
Write-Host "  * If you press 'Show ignored' you should find the rebalance pair." -ForegroundColor Cyan
Write-Host "  * Any card that returns carries a blue note saying why." -ForegroundColor Cyan
Write-Host ""
Write-Host "  A NOTE ON YOUR NINE HIDDEN CARDS: they were marked before this" -ForegroundColor DarkYellow
Write-Host "  ships, so they carry no amount and this change cannot un-hide" -ForegroundColor DarkYellow
Write-Host "  them. They age out on their own, or press Show ignored / Show" -ForegroundColor DarkYellow
Write-Host "  completed to bring them back now. From here on a mute records" -ForegroundColor DarkYellow
Write-Host "  what it was about." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The 07:00 digest still composes figures across bases. PR 35." -ForegroundColor DarkYellow
Write-Host "  * The target card still forgets what you accepted. PR 36." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply and sleeves/fund still record nothing about what" -ForegroundColor DarkYellow
Write-Host "    they changed, so three days of history stay unmeasurable." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
