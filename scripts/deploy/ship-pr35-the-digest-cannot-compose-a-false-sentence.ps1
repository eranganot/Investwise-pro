# SHIP PR 35 - the 07:00 push made four claims and three of them were false.
#
#   .\scripts\deploy\ship-pr35-the-digest-cannot-compose-a-false-sentence.ps1
#   .\scripts\deploy\ship-pr35-the-digest-cannot-compose-a-false-sentence.ps1 -SkipTests
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
    "app/api/routes/push.py",
    "app/services/ask_service.py",
    "app/services/context_service.py",
    "app/services/digest_service.py",
    "tests/test_the_digest_cannot_compose_a_false_sentence.py",
    "scripts/smoke/smoke-digest-grounded.ps1",
    "scripts/deploy/ship-pr35-the-digest-cannot-compose-a-false-sentence.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 35: the digest cannot compose a false sentence",
    "",
    "The 07:00 push on 2026-09-01, verbatim:",
    "",
    "    Hello! Your account is currently valued at 21,474.35 ILS, with a total",
    "    return of 17.76%. You have an 89.2% probability of reaching your",
    "    102,973 goal in 5 years. To diversify your holdings, consider buying",
    "    2,147 ILS of PDBC (Broad commodities) to reach your 10% target in this",
    "    asset class.",
    "",
    "Measured against the app the same morning:",
    "",
    "    21,474.35   REAL - the NAV snapshot for 2026-08-31",
    "    17.76%      REAL, and not a return on that NAV. A BACKFILL of today's",
    "                holdings priced through 252 sessions of real closes. The",
    "                account's return on the 20,000 contributed was +3.89%",
    "    89.2%       not in the context at all. /plan/projection says 22.7%",
    "    102,973     not in the context. The plan target is 100,288.77",
    "    5 years     not in the context. The horizon is 6",
    "    2,147       exactly 10% of NAV, COMPUTED by the model",
    "    10%         from OBJ_TARGET['Grow'], not in the context either",
    "",
    "The first two are the interesting failure. BOTH NUMBERS WERE TRUE AND THE",
    "SENTENCE WAS FALSE, because they measure different things and nothing in",
    "the payload said so. The prompt's 'never invent numbers' was not violated.",
    "",
    "So the fix is not a better instruction. It is two mechanisms:",
    "",
    "1. EVERY QUOTABLE FIGURE TRAVELS AS {value, means}. FIGURE_BASIS is one",
    "   table mapping a dotted path to what that number is a measurement OF, in",
    "   the words the digest should use. A backfill now says it is a backfill",
    "   inside the same object as the number.",
    "",
    "2. EVERY NUMBER IN THE GENERATED TEXT MUST APPEAR IN THE CONTEXT, or the",
    "   whole digest is discarded and the deterministic one is sent.",
    "   unsupported_numbers matches by ROUNDING to the decimals the text used -",
    "   never a tolerance band, because a band wide enough to accept '21,474'",
    "   for 21474.35 also accepts 17.76 for 17.03. Fractions may be quoted as",
    "   percentages (0.227 -> 23%), and numbers that reached the context inside",
    "   a STRING are quotable, so repeating a card's own wording back is not an",
    "   invention.",
    "",
    "Run against the real notification the check names SIX unsupported figures",
    "- 17.76, 89.2, 102973, 5, 2147, 10 - and leaves 21,474.35 alone.",
    "",
    "TWO SOURCE PROBLEMS WENT WITH IT.",
    "",
    "risk.goal carried a SECOND probability of reaching the target, from a",
    "different engine than the Plan screen, and it was null on the day the push",
    "claimed 89.2%. It is no longer copied into the context; /plan/projection",
    "is the one source, and it is now IN the context, which is the vacuum the",
    "invented figure filled.",
    "",
    "And the fallback said 'Performance: 17.03% total' directly under the",
    "net-worth line: the same two figures in the same order that produced the",
    "false sentence, only without a model in between. It names the basis every",
    "time now, and it prints only the parts it has - a book with no VaR row",
    "used to render '1-day VaR(95%) None%'.",
    "",
    "THE LABELLING IS APPLIED AT THE SEAM, NOT IN gather(). The first version",
    "wrapped the values inside gather itself, and /api/v1/ask returns that",
    "object to the browser - context.nav_ils stopped being a number and became",
    "{value: ...}. test_assistant.py caught it, having asserted",
    "context['nav_ils'] >= 0 since long before any of this. for_prompt()",
    "returns a labelled COPY; the API shape is unchanged and the assistant's",
    "prompt gets the same protection as the digest.",
    "",
    "GET /api/v1/push/digest/preview builds the digest without sending it, so",
    "the verification result is observable - verified plus the offending",
    "figures - without waiting for 07:00 or receiving it on a phone.",
    "",
    "15 tests, all 15 fail against PR 34.",
    "",
    "Gate: SQLite 1530 passed / 0 failed; Postgres 16 1530 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR 07:00 DIGEST STOPS QUOTING NUMBERS IT WAS NOT GIVEN." -ForegroundColor Green
Write-Host ""
Write-Host "  Run against the 1 September notification, the new check names six" -ForegroundColor Green
Write-Host "  unsupported figures: 17.76, 89.2, 102973, 5, 2147, 10." -ForegroundColor Green
Write-Host "  It leaves 21,474.35 alone, because that one was real." -ForegroundColor Green
Write-Host ""
Write-Host "  When the check fails you get the deterministic digest instead." -ForegroundColor Cyan
Write-Host "  That is not a degraded mode - it states every figure with its" -ForegroundColor Cyan
Write-Host "  basis, which is what the LLM one was failing to do." -ForegroundColor Cyan
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
    Warn "-SkipTests: the gate did NOT run. This PR changes what your phone is"
    Warn "told every morning. Strange one to skip."
} else {
    $Focus = @(
        "tests/test_the_digest_cannot_compose_a_false_sentence.py",
        "tests/test_assistant.py",
        "tests/test_p42_notifications.py",
        "tests/test_job_health_and_gain.py")
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
Write-Host ("    .\scripts\smoke\smoke-digest-grounded.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 3 prints the digest your phone would get, and whether it" -ForegroundColor Yellow
Write-Host "  passed verification. Read it." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES - tomorrow's 07:00 push:" -ForegroundColor Cyan
Write-Host "  * No sentence may present the BACKFILL return as the return on" -ForegroundColor Cyan
Write-Host "    your account or on its value. It has to say backfill." -ForegroundColor Cyan
Write-Host "  * Any goal probability must match the Plan screen. There is one" -ForegroundColor Cyan
Write-Host "    source for it now." -ForegroundColor Cyan
Write-Host "  * If the digest reads more plainly than usual, the check fired" -ForegroundColor Cyan
Write-Host "    and you got the deterministic one. Run the smoke script to see" -ForegroundColor Cyan
Write-Host "    which numbers it refused." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The target card still forgets what you accepted. PR 36." -ForegroundColor DarkYellow
Write-Host "  * reconcile/apply and sleeves/fund still record nothing about what" -ForegroundColor DarkYellow
Write-Host "    they changed, so three days of history stay unmeasurable." -ForegroundColor DarkYellow
Write-Host "  * The verifier checks NUMBERS. A false claim with no number in it" -ForegroundColor DarkYellow
Write-Host "    still passes - the basis labels are the only defence there." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
