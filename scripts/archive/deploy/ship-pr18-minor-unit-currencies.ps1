# SHIP PR 18 - agorot and pence stop being valued as shekels and pounds.
#
#   .\scripts\deploy\ship-pr18-minor-unit-currencies.ps1
#   .\scripts\deploy\ship-pr18-minor-unit-currencies.ps1 -SkipTests
#
# No migration. No engine version bump. IF YOUR BOOK HOLDS A TEL AVIV OR LONDON
# POSITION, YOUR NAV WILL FALL SHARPLY on the next price refresh - read section
# 0 before running this.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job. See CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/fx.py",
    "app/services/pricing_service.py",
    "app/services/performance_service.py",
    "app/api/routes/intake.py",
    "tests/test_minor_unit_currencies.py",
    "tests/test_performance_fx.py",
    "scripts/agent-key.ps1",
    "scripts/smoke/smoke-rebalance-cash.ps1",
    "scripts/deploy/ship-pr18-minor-unit-currencies.ps1",
    "scripts/smoke/smoke-minor-units.ps1",
    ".gitignore",
    "STATUS.md"
)

$Message = @(
    "PR 18: agorot and pence were valued as shekels and pounds",
    "",
    "Found while asking why no card ever names a Tel Aviv listing. It is a",
    "separate defect from that one, it is a money defect, and it needs no TASE",
    "feature work to reach a real book: the holdings form already offers Tel Aviv",
    "and London.",
    "",
    "Several exchanges quote in a MINOR unit and say so in the CURRENCY CODE",
    "rather than in the price. Verified live against the same Yahoo endpoint",
    "YahooMarketDataProvider calls, on 2026-08-29:",
    "",
    "  TEVA.TA  currency ILA  price  11030.0   ->   110.30 ILS",
    "  POLI.TA  currency ILA  price   7761.0   ->    77.61 ILS",
    "  ESLT.TA  currency ILA  price 211190.0   ->  2111.90 ILS",
    "  VOD.L    currency GBp  price    118.45  ->     1.1845 GBP",
    "  AAPL     currency USD  price    319.70  ->   319.70 USD, no trap",
    "",
    "pricing_service stamped price_currency straight from the quote, so the",
    "position carried 'ILA' and a price a hundred times too big. Nothing",
    "downstream knew that code, and BOTH FX providers answered anyway:",
    "",
    "  * the BUILTIN provider has no ILA pair, so get_rate falls through to",
    "    rate = 1.0 and returns it WITHOUT ERRORING - indistinguishable from a",
    "    legitimate peg;",
    "  * the LIVE provider raises, and fx.fx_rate caught everything and returned",
    "    1.0, which its own docstring called 'failing safe'.",
    "",
    "Either way agorot were valued as shekels. Measured in the new test: a book",
    "of 100 Teva shares worth 11,030 ILS reads as 1,103,000 ILS. NAV, gain %,",
    "the allocation mix, the concentration caps and every card sized against NAV",
    "inherit it, with no exception and no log line.",
    "",
    "THE FIX IS AT THE BOUNDARY, NOT THE THIRTEEN CALL SITES. fx.to_major_units",
    "converts a quoted price into its major unit and pricing_service calls it",
    "once, before stamping. A normalisation each consumer has to remember is one",
    "a new consumer silently opts out of - the same lesson as the funding path.",
    "",
    "CASE MATTERS, and this is the trap inside the trap: 'GBp' (pence)",
    "upper-cases to 'GBP' (pounds), the major currency it is one hundredth of.",
    "Uppercasing before the lookup turns the detection into a no-op for exactly",
    "the currency that most needs it. The raw code is matched first and 'GBP' is",
    "excluded from the case-insensitive pass. There is a test for this alone.",
    "",
    "THE 1.0 FALLBACK WAS NOT SAFE AND IS NO LONGER A DEFAULT. It was reached in",
    "two very different situations and only one is survivable:",
    "",
    "  * the provider wobbled on a currency it knows. Falling back to 1.0 values",
    "    a USD book at 27% of its worth, because USD/ILS is ~3.71. The last rate",
    "    that actually resolved is remembered per pair and served with a warning.",
    "  * the code is one the provider has never heard of, which is how ILA got",
    "    valued at par. That is a wrong input, not a wobble, and it now says so",
    "    at ERROR.",
    "",
    "It still RETURNS rather than raises: thirteen call sites do arithmetic on",
    "the result and turning this into an exception is a change to all of them.",
    "What it no longer does is fail silently. Abstaining properly - a holding the",
    "app declines to value, and says why on the screen - is the follow-up, and",
    "fx.position_value_ils (added in PR 17) is the one seam it needs.",
    "",
    "REPAIR FOR ROWS ALREADY WRITTEN. A position stamped in a minor unit is",
    "renormalised at the TOP of the refresh loop, before the quote, deliberately:",
    "a ticker whose quote fails or is stale keeps the price it already has, so on",
    "exactly the runs where the row is least likely to be rewritten the 100x",
    "value would survive another cycle. Same shape and same reason as",
    "repair_cash_row. The refresh reports minor_unit_quotes and",
    "repaired_minor_unit_rows, and the position payload carries",
    "price_currency_quoted so the normalisation is auditable from outside the",
    "process rather than inferred.",
    "",
    "ALSO IN HERE, because the smoke script needed it: scripts/agent-key.ps1.",
    "There is no create-a-key endpoint - config.py reads one AGENT_API_KEY and",
    "auth.py compares the header to it, so a new key means changing the Railway",
    "variable, which instantly breaks the 34 committed scripts that bake the old",
    "one in. This is the helper that makes rotating it a one-line change instead",
    "of a 34-file change: env var, then a git-ignored scripts/.agent-key, then",
    "the literal already committed - saying out loud when it falls that far.",
    "",
    "Gate: SQLite 1342 passed / 4 skipped; Postgres 1338 passed / 12 skipped;",
    "ruff check app tests clean. All eleven new tests fail on the old code.",
    "",
    "Not financial advice."
)

Step "0. What this changes about your book"
Write-Host "  If you hold NOTHING on Tel Aviv or London, this changes no number." -ForegroundColor Yellow
Write-Host ""
Write-Host "  If you DO, that holding has been valued 100x too high, and the next" -ForegroundColor Yellow
Write-Host "  price refresh after this deploy will correct it. Your NAV will fall" -ForegroundColor Yellow
Write-Host "  sharply and that fall is the ERROR being removed, not a loss." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Check first, so the number does not surprise you:" -ForegroundColor Yellow
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1" -ForegroundColor White
Write-Host "  Section 2 lists every Tel Aviv and London holding with the price" -ForegroundColor Yellow
Write-Host "  and currency currently stored against it." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. CI is then the only thing between"
    Warn "this and Railway, and Railway deploys on red."
} else {
    Invoke-Suite -Focus @(
        "tests/test_minor_unit_currencies.py",
        "tests/test_price_writers_audit.py",
        "tests/test_rebalance_leaves_cash_alone.py",
        "tests/test_accept_conserves_nav.py")
    Write-Host ""
    Write-Host "  The SQLite run above is NOT the Postgres job." -ForegroundColor Yellow
    Write-Host "  Run test-postgres before answering yes. Recipe: CLAUDE.md." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

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
Write-Host "  Read-only:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1 -Sha $sha" -ForegroundColor White
Write-Host "  Repairs a row already stored in agorot (reprices, buys nothing):" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-minor-units.ps1 -Sha $sha -AllowRefresh" -ForegroundColor White

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Holdings: any Tel Aviv or London position must show a price in" -ForegroundColor Cyan
Write-Host "    shekels or pounds, not a number a hundred times too big." -ForegroundColor Cyan
Write-Host "  * A position the provider quoted in a minor unit now carries" -ForegroundColor Cyan
Write-Host "    price_currency_quoted on /api/v1/portfolio, so you can see the" -ForegroundColor Cyan
Write-Host "    conversion happened instead of trusting that it did." -ForegroundColor Cyan
Write-Host "  * The Railway log names any row it repaired, with both prices." -ForegroundColor Cyan
Write-Host "  * If FX goes down, the log now says which pair and what rate it" -ForegroundColor Cyan
Write-Host "    fell back to. It used to say nothing and return 1.0." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "    It still returns 1.0 as a last resort because thirteen call" -ForegroundColor DarkYellow
Write-Host "    sites do arithmetic on the result. A holding the app declines" -ForegroundColor DarkYellow
Write-Host "    to value, and says so on screen, is the follow-up." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one. universe.TASE_STOCKS holds" -ForegroundColor DarkYellow
Write-Host "    seven US-listed names. PR 24 - this deploy is its prerequisite." -ForegroundColor DarkYellow
Write-Host "  * The intermittent +739.21 ILS leak is still unexplained. A" -ForegroundColor DarkYellow
Write-Host "    recurrence now logs a NAV CONSERVATION BREACH line (PR 17)." -ForegroundColor DarkYellow
Write-Host "  * The two contradicting cards (sell Equities / buy SOXL) are" -ForegroundColor DarkYellow
Write-Host "    untouched. PR 19." -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "    PR 20." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 21." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "    scripts\agent-key.ps1 makes rotating it a one-line change; it" -ForegroundColor DarkYellow
Write-Host "    does not rotate it." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
