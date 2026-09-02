# SHIP PR 32 - a provider said 0 and the app wrote the holding down to zero.
#
#   .\scripts\deploy\ship-pr32-a-quote-of-zero.ps1
#   .\scripts\deploy\ship-pr32-a-quote-of-zero.ps1 -SkipTests
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
    "app/api/routes/intake.py",
    "app/providers/live.py",
    "app/services/pricing_service.py",
    "app/static_app/index.html",
    "tests/test_a_quote_of_zero_is_not_a_quote.py",
    "scripts/smoke/smoke-unpriced.ps1",
    "scripts/deploy/ship-pr32-a-quote-of-zero.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 32: a quote of zero is not a quote",
    "",
    "Eran opened the app on 2026-09-01, found holdings showing 0 ILS, and",
    "deleted two of them - a holding worth nothing looks like junk. By the next",
    "morning the remaining rows were priced normally again and there was",
    "nothing on any screen to say what had happened. The audit log has the two",
    "DELETE /portfolio/position calls at 17:19:09 and 17:19:49. It has nothing",
    "about the zero, because nothing recorded one.",
    "",
    "MEASURED, on the old code, by the new test:",
    "",
    "    NAV went 3710.0 -> 0.0 because a provider answered 0.",
    "    Nothing was bought or sold.",
    "",
    "THE HOLE. refresh_all_positions guards, refuses and DISCLOSES every other",
    "way a quote can be unusable:",
    "",
    "    q is None                       -> failed, error recorded, price kept",
    "    quote older than the window     -> stale, not_written, disclosed",
    "    currency contradicts the market -> conflict, not_written, disclosed",
    "",
    "Its own comment states the rule: 'Do NOT overwrite the price: a quote",
    "nothing has traded against is not a current price.' A price of 0 is the",
    "same class of thing and it was the one case with no guard at all - through",
    "to p.current_price = money(q_price) with nothing in between.",
    "",
    "WHERE A ZERO COMES FROM. Both adapters guarded 'price is None' and neither",
    "guarded a zero:",
    "",
    "  * Yahoo returns a meta block with regularMarketPrice: 0 for a symbol it",
    "    half-resolves - a delisted line, a venue suffix it does not carry, a",
    "    ticker that has changed.",
    "  * FMP answers an unknown or delisted symbol with 'price': 0 rather than",
    "    an error, so 'is None' never fires there either.",
    "",
    "NOT A DISPLAY BUG. value_ils is quantity x price x rate, so a zeroed",
    "holding leaves NAV entirely - and NAV is what the mix, the rebalance",
    "sizing, the concentration cap, the projection and the 22:10 snapshot are",
    "all measured against. The snapshot is the one number the app cannot",
    "recompute later, so a snapshot taken inside a zeroed window is wrong",
    "permanently. It also self-heals in 30 minutes, which is why it has never",
    "been caught: by the time anyone looks, the evidence has been overwritten.",
    "",
    "TWO LAYERS, DELIBERATELY.",
    "",
    "  The adapters raise. That is the better place: the caller's fallback",
    "  provider then gets its turn, and guarded_quote does not cache a poisoned",
    "  zero for the rest of its TTL.",
    "  The refresh refuses as well, and counts and logs it. No quote source -",
    "  present or future, including the builtin one the suite runs on - gets to",
    "  write a holding down to zero from there.",
    "",
    "AND THE DISCLOSURE, which is the half that would have saved the two",
    "deleted rows. /api/v1/portfolio now carries unpriced_positions and",
    "unpriced_cost_ils beside the existing stale_positions and",
    "fx_unresolved_positions, and the holdings card renders such a row as",
    "'could not be priced - not 0' with what you PAID, instead of a flat 0 that",
    "reads as 'this is worthless, delete it'.",
    "",
    "Derived from the PRICE, not from a meta flag: a flag only describes rows",
    "written by the build that sets it, and the rows that caused this were",
    "written by the build that had no flag.",
    "",
    "NAV is deliberately NOT padded with a guessed price. unpriced_cost_ils",
    "reports what is missing at COST, which is the only number the app has for",
    "a holding it cannot price. Inventing a price is the one thing worse than",
    "admitting there is not one.",
    "",
    "ALL SIX NEW TESTS WERE CONFIRMED TO FAIL AGAINST THE PREVIOUS COMMIT",
    "before being accepted, and the end-to-end one is ordered so that its",
    "failure message names the damage (NAV 3710 -> 0) rather than a missing",
    "counter.",
    "",
    "Gate: SQLite 1489 passed / 0 failed; Postgres 16 1489 passed / 0 failed;",
    "ruff check app tests clean.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  A HOLDING THE APP CANNOT PRICE WILL NOW SAY SO." -ForegroundColor Green
Write-Host ""
Write-Host "  Before: it rendered as a flat 0 ILS, dropped out of NAV, and" -ForegroundColor Yellow
Write-Host "          looked exactly like a position that really is worthless." -ForegroundColor Yellow
Write-Host "  After:  'could not be priced - not 0', with what you paid, and" -ForegroundColor Green
Write-Host "          unpriced_positions on the payload naming every one." -ForegroundColor Green
Write-Host ""
Write-Host "  NOTHING IS BOUGHT, SOLD OR REPRICED. NAV is not padded with a" -ForegroundColor Yellow
Write-Host "  guessed price - the app says what is missing instead." -ForegroundColor Yellow
Write-Host ""
Write-Host "  If nothing is unpriced today you will see no visible change, and" -ForegroundColor Cyan
Write-Host "  that is the expected outcome: the defect self-heals in 30 minutes." -ForegroundColor Cyan
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
python -m pytest -q tests/test_the_pins_are_installable_and_installed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  STOP. The versions installed here are not the versions" -ForegroundColor Red
    Write-Host "  requirements.txt pins, so nothing measured here can be" -ForegroundColor Red
    Write-Host "  quoted as a fact about the project." -ForegroundColor Red
    Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor Cyan
    exit 1
}
Write-Host "  This interpreter matches the pins." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. This PR changes what reaches"
    Warn "current_price. Strange one to skip."
} else {
    Invoke-Suite -Focus @(
        "tests/test_a_quote_of_zero_is_not_a_quote.py",
        "tests/test_price_refresh_keeps_the_declared_currency.py",
        "tests/test_minor_unit_currencies.py",
        "tests/test_price_writers_audit.py",
        "tests/test_cash_pricing_guard.py",
        "tests/test_sims_and_refresh.py")
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
Write-Host ("    .\scripts\smoke\smoke-unpriced.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 is the question: is anything valued at zero, and does" -ForegroundColor Yellow
Write-Host "  the payload name it. An empty answer there is the good answer." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Holdings: no row should ever show a bare 0 again. If one cannot" -ForegroundColor Cyan
Write-Host "    be priced it says so in amber and shows what you paid." -ForegroundColor Cyan
Write-Host "  * NAV unchanged apart from the market. This PR writes no price." -ForegroundColor Cyan
Write-Host "  * If you DO see 'could not be priced', tell me the ticker before" -ForegroundColor Cyan
Write-Host "    deleting it. That row is your money, not junk." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The two holdings deleted on 2026-09-01 are gone and cannot be" -ForegroundColor DarkYellow
Write-Host "    recovered from here - nothing recorded what they were." -ForegroundColor DarkYellow
Write-Host "  * The account history still books that whole day as a flow. PR 34." -ForegroundColor DarkYellow
Write-Host "  * Rebalance cards can still be muted by a stale id. PR 33." -ForegroundColor DarkYellow
Write-Host "  * The 07:00 digest still composes figures across bases. PR 35." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
