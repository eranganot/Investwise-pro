# SHIP PR 20 - a price refresh stops redenominating your holdings.
#
#   .\scripts\deploy\ship-pr20-declared-currency-wins.ps1
#   .\scripts\deploy\ship-pr20-declared-currency-wins.ps1 -SkipTests
#
# THIS IS THE FIX FOR CI RUN #178 (and #169). main is RED without it.
#
# If PR 19 has been applied but not yet pushed, this script pushes BOTH commits
# in one CI run - which is what you want here, because main is red and one green
# run is better than two rolls of an intermittent dice.
#
# No migration. No engine version bump. IF ANY HOLDING WAS ALREADY
# REDENOMINATED, YOUR NAV WILL FALL on the next refresh - read section 0.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job; it is the ONLY job that ever caught this. See CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/pricing_service.py",
    "app/api/routes/intake.py",
    "app/providers/builtin.py",
    "app/providers/registry.py",
    "tests/test_p0_safety.py",
    "tests/test_cash_pricing_guard.py",
    "tests/test_minor_unit_currencies.py",
    "tests/test_price_refresh_keeps_the_declared_currency.py",
    "scripts/smoke/smoke-minor-units.ps1",
    "scripts/deploy/ship-pr20-declared-currency-wins.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 20: a price refresh redenominated a holding, and that was the CI leak",
    "",
    "CI run #178 went red on main at 9e2dd08, test-postgres only. It is CI run",
    "#169's failure with the IDENTICAL assertion - 192360.0 <= 41000.0 + 1.0 -",
    "two sightings that were never reproduced from the number alone. The row dump",
    "a previous session added to that assertion is what located it:",
    "",
    "  BBB qty=10.0      basis=100.0 invested=3710.0",
    "  AAA qty=107.0     basis=100.0 invested=39697.0",
    "  CASH qty=148953.0 basis=1.0   invested=148953.0",
    "",
    "Both holdings are seeded on TASE at 100 ILS, so BBB's invested should read",
    "1,000. It reads 3,710 - exactly 1,000 x 3.71, which is",
    "BuiltinFXProvider._BASE[('USD','ILS')], the hardcoded rate the test",
    "providers use. The real market rate is ~2.98; 3.71 is a fixture constant,",
    "and the multiplier in production would be ~2.97. Roughly triple either way.",
    "",
    "ROOT CAUSE. The provider decides the market from the TICKER, not from the",
    "holding. BuiltinMarketDataProvider.get_quote is literally",
    "",
    "    market = 'TASE' if ticker.upper().startswith('TA') else 'NYSE'",
    "",
    "(builtin.py:37) and returns that market's currency. pricing_service stamped",
    "price_currency straight from the quote, so the PROVIDER'S GUESS OVERRULED",
    "THE MARKET THE USER DECLARED in the holdings form.",
    "",
    "MEASURED on that exact book, one refresh: NAV 41,000 -> 235,687 and invested",
    "41,000 -> 152,110, both exactly x3.71. The CI dump's cash figure",
    "reconstructs to the shekel - 293 shares at the builtin's synthetic 149.37,",
    "times 3.71, less 25% CGT on the gain = 148,953.0, which is what CI printed.",
    "",
    "WHY IT WAS INTERMITTENT: it needs a refresh to land INSIDE the test. The",
    "scheduler primes one on startup and Postgres runs are slow enough for the",
    "timing to differ. The arithmetic was always deterministic - only the timing",
    "was not, which is why reasoning from the number alone kept failing and",
    "printing the rows solved it immediately.",
    "",
    "WHY IT WAS SILENT: no exception, and NAV and invested move TOGETHER, so",
    "gain % stays plausible. A book that has quietly quadrupled looks like a book",
    "that has done well.",
    "",
    "THIS IS LIVE IN PRODUCTION, not a test artefact. Any Tel Aviv or London",
    "holding entered under a ticker the provider reads as American is multiplied",
    "by the FX rate on the next refresh. Same family as PR 18's agorot bug: a",
    "provider's opinion about currency overriding the book's own.",
    "",
    "THE FIX. A quote whose currency contradicts the declared market is not a",
    "quote about that holding, so the refresh refuses it - and refuses the PRICE",
    "too, not just the currency stamp. There is no arithmetic that recovers the",
    "right number from a quote about a different instrument, which is the same",
    "rule already applied to a stale quote, for the same reason.",
    "",
    "  * the position's market is the user's DECLARATION; fx.MARKET_CCY gives the",
    "    currency it implies. A disagreement is reported, never applied.",
    "  * a market not in MARKET_CCY ('OTHER') has no declaration to contradict,",
    "    so the quote is trusted - better than nothing.",
    "  * rows ALREADY redenominated are repaired: the declared currency is",
    "    restored, removing the x3.71, and the row is flagged price_unverified,",
    "    because its price was written by a provider answering about something",
    "    else. The app says which part of the total it does not stand behind",
    "    rather than picking a number.",
    "  * /api/v1/portfolio carries price_currency_conflict and price_unverified;",
    "    the refresh reports currency_conflicts and repaired_currency_rows.",
    "",
    "THIS IS ALMOST CERTAINLY THE +739.21 ILS rebalance_to_objective LEAK AS",
    "WELL: a card built while the rows read ILS and accepted after a refresh",
    "flipped them to USD moves NAV between the two reads, with _rebalance_to's",
    "own guard passing because it measures a single consistent snapshot. NOT",
    "asserted as proven - that sighting was never reproduced and no BREACH line",
    "(PR 17) has been captured. It is the leading candidate, and PR 17's",
    "reporting is what would confirm it.",
    "",
    "SECOND FINDING, FROM ERAN: the FX default is a frozen table. He spotted the",
    "3.71 immediately - the market was 2.98 on 2026-08-28. That number is",
    "BuiltinFXProvider._BASE[('USD','ILS')], written once and never revisited.",
    "Correct for a test fixture; a ~24% valuation error on a real book. And",
    "config.py:105 DEFAULTS fx_provider to 'builtin', so a deployment that does",
    "not set FX_PROVIDER converts every foreign holding at that table with",
    "nothing on screen to say so. Production is fine - checked against the live",
    "app on 2026-08-30, which reports an implied USD/ILS of 2.9728 - but a fresh",
    "environment or a lost variable would inherit the frozen table.",
    "registry.fx_provider() now warns once per process, naming the hardcoded",
    "rate. The constant is left alone: changing it to 2.98 only moves the day it",
    "goes stale, and tests must not depend on a live rate.",
    "",
    "TWO TEST FIXTURES WERE INCOHERENT, and are fixed rather than the check",
    "relaxed. test_p0_safety.py built holdings on TASE and quoted them in USD -",
    "harmless only while nothing compared the two. Those tests are about",
    "FRESHNESS, so the quote fixture is made coherent; the currency behaviour is",
    "pinned by its own file. test_cash_pricing_guard.py's _Row stub had no",
    "market at all, modelling a row that cannot exist (market is NOT NULL on",
    "Position). The refresh reads the field defensively now - a reprice must not",
    "500 the app because one row is odd.",
    "",
    "Gate: SQLite 1355 passed / 4 skipped; Postgres 1351 passed / 12 skipped;",
    "ruff check app tests clean. All four new tests fail on the old code.",
    "",
    "Not financial advice."
)

Step "0. What this changes about your book"
Write-Host "  If no holding of yours was ever redenominated, this changes no" -ForegroundColor Yellow
Write-Host "  number - it only stops it happening." -ForegroundColor Yellow
Write-Host ""
Write-Host "  If one WAS, it has been valued at 3.71x on every read, and the" -ForegroundColor Yellow
Write-Host "  next price refresh after this deploy restores it. YOUR NAV WILL" -ForegroundColor Yellow
Write-Host "  FALL, and that fall is the error leaving, not a loss." -ForegroundColor Yellow
Write-Host ""
Write-Host "  A repaired row keeps the price the wrong quote wrote and is" -ForegroundColor Yellow
Write-Host "  flagged 'unverified'. Check those against your broker - the app" -ForegroundColor Yellow
Write-Host "  deliberately does not guess a replacement." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Check what you have first:" -ForegroundColor Yellow
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1" -ForegroundColor White
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. main is ALREADY RED; do not stack"
    Warn "an unverified push on top of it."
} else {
    Invoke-Suite -Focus @(
        "tests/test_price_refresh_keeps_the_declared_currency.py",
        "tests/test_cash.py",
        "tests/test_minor_unit_currencies.py",
        "tests/test_price_writers_audit.py")
    Write-Host ""
    Write-Host "  THE POSTGRES JOB IS THE ONE THAT CAUGHT THIS, TWICE, AND IS" -ForegroundColor Yellow
    Write-Host "  THE ONLY JOB THAT EVER HAS. Run test-postgres before answering" -ForegroundColor Yellow
    Write-Host "  yes. Recipe: CLAUDE.md." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green
$ahead = @(git rev-list --count "origin/main..HEAD")
Write-Host "  commits to push: $ahead" -ForegroundColor Green

Step "4. Push, and let CI gate it"
Push-AndWatch

Step "5. Wait for Railway to actually serve it"
$live = ""
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -eq $sha) { break }
    } catch { $live = "(no answer)" }
    Write-Host "  serving $live, waiting for $sha ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if ($live -eq $sha) { Write-Host "  Railway is serving $sha" -ForegroundColor Green }
else { Warn "Still serving '$live' after 8 minutes. Check the Railway build log." }

Step "6. Smoke"
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1 -Sha $sha" -ForegroundColor White
Write-Host "  Then, to run the repair pass (reprices; buys and sells nothing):" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1 -Sha $sha -AllowRefresh" -ForegroundColor White
Write-Host "  And, if PR 19 went out with this push:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-contradictions.ps1 -Sha $sha" -ForegroundColor White

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * CI should be GREEN. It has been red on main since 9e2dd08, and" -ForegroundColor Cyan
Write-Host "    test-postgres is the job that was failing." -ForegroundColor Cyan
Write-Host "  * Holdings: every position's currency should match its market -" -ForegroundColor Cyan
Write-Host "    a Tel Aviv holding in ILS, a London one in GBP." -ForegroundColor Cyan
Write-Host "  * If any row is flagged unverified, its price came from a quote" -ForegroundColor Cyan
Write-Host "    about a different instrument. Check it against your broker." -ForegroundColor Cyan
Write-Host "  * The Railway log names every quote it refused and every row it" -ForegroundColor Cyan
Write-Host "    restored, with both currencies." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The +739.21 ILS leak is NOT declared fixed. This is the leading" -ForegroundColor DarkYellow
Write-Host "    candidate and the mechanism fits exactly, but that sighting was" -ForegroundColor DarkYellow
Write-Host "    never reproduced and no BREACH line has been captured. If it" -ForegroundColor DarkYellow
Write-Host "    never recurs, that is the evidence." -ForegroundColor DarkYellow
Write-Host "  * A repaired row keeps a price the app does not stand behind. It" -ForegroundColor DarkYellow
Write-Host "    says so; it does not fix it." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "    PR 21." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 22. After that the bucket is empty." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one. PR 24." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
