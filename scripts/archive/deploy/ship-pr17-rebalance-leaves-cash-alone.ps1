# SHIP PR 17 - a rebalance stops spending your cash, and an accept says what it did.
#
#   .\scripts\deploy\ship-pr17-rebalance-leaves-cash-alone.ps1
#   .\scripts\deploy\ship-pr17-rebalance-leaves-cash-alone.ps1 -SkipTests
#
# No migration. No engine version bump. THIS CHANGES WHAT AN ACCEPT DOES to
# your book - read section 0 before running it.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job; it is the one job SQLite cannot stand in for. See CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/models/tables.py",
    "app/services/fx.py",
    "app/services/intake_service.py",
    "app/services/pricing_service.py",
    "app/services/reconcile_service.py",
    "app/services/strategy_service.py",
    "app/services/recommendations.py",
    "tests/test_rebalance_leaves_cash_alone.py",
    "scripts/deploy/ship-pr17-rebalance-leaves-cash-alone.ps1",
    "scripts/smoke/smoke-rebalance-cash.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 17: accepting a rebalance spent the whole cash balance",
    "",
    "Found while instrumenting the intermittent NAV leak, not by reading the",
    "code. It is a different defect, deterministic, and live.",
    "",
    "_book_target returns INVESTABLE weights only. Grow is",
    "{Equities .80, Fixed Income .10, Commodities .10} and no objective has a",
    "Cash key at all. The CASH row classifies as the asset class 'Cash', so",
    "_rebalance_to looked up target.get('Cash', 0.0), got 0.0, computed a desired",
    "value of zero, and set the balance to zero - spreading the money across the",
    "equity legs.",
    "",
    "MEASURED on the seeded USD book: 621.79 ILS of cash -> 0.00, in one accept.",
    "It also drove the balance through the app's own cash floor for that book",
    "(funding_service.cash_floor_ils -> 419.69 ILS), a policy this path never",
    "consulted.",
    "",
    "WHY NOTHING CAUGHT IT: NAV is conserved by the move, so _rebalance_to's own",
    "guard passed and so did every test. What it actually did was BUY 621.79 ILS",
    "of equities - no trade legs, no ledger entry, no tax - under a card whose",
    "text says only 'Sell about X of Equities to move toward 80%' and never",
    "mentions cash. That is the card-claim invariant, on the accept path: a card",
    "may not produce a change it does not claim.",
    "",
    "A rebalance is a reshuffle of INVESTED value between investable classes.",
    "Turning cash into securities is a purchase and belongs to the funded-buy",
    "path, which writes legs and says what it bought. Cash is now excluded from",
    "both the classes being scaled and the NAV they are scaled against.",
    "",
    "TWO THINGS THAT MADE IT UNDIAGNOSABLE, FIXED IN THE SAME PASS:",
    "",
    "1. Every MONEY column is Numeric(18, 4); the writers handed it six decimal",
    "   places. Postgres rounds on write, the identity-mapped ORM object keeps",
    "   all six, so a conservation check reading the ORM object measured a book",
    "   the database never had. SQLite keeps whatever it is given and shows",
    "   nothing, which is why only test-postgres could ever have seen it.",
    "   Measured: writing 4.712276 shares reads back 4.7123.",
    "   app.models.tables.money() quantises to the column's own scale - read off",
    "   MONEY.scale, not repeated as a literal - and every writer goes through",
    "   it: upsert_positions, buy_into_book, update_position, pricing_service,",
    "   reconcile_service, strategy_service.",
    "",
    "2. The conservation invariant was enforced inside exactly ONE of the twelve",
    "   actionable handlers. The other eleven changed money with nothing checking",
    "   them; the only thing behind them was a test sweeping whatever cards the",
    "   engine happened to emit that run, and which cards it emits varies with",
    "   process-global provider state. Every accept now returns nav_before_ils,",
    "   nav_after_ils and nav_drift_ils, and logs the per-position diff at ERROR",
    "   when the drift exceeds the tax the card reported.",
    "",
    "It REPORTS rather than REFUSES, deliberately: several handlers commit",
    "part-way through (update_position commits; the funding paths commit), so by",
    "the time the drift is measurable there is nothing left to roll back. Making",
    "an accept atomic is a change to the commit boundaries and is not smuggled in",
    "here. What this buys is that a breach is attributable at the moment it",
    "happens - kind, card, drift, and the positions that moved.",
    "",
    "THE +739.21 ILS LEAK IS NOT FIXED AND IS NOT CLAIMED TO BE. It did not",
    "reproduce: the full Postgres suite ran green as a control before any change",
    "(1322 passed, 7m13s), and again after. Ruled out by observation, so nobody",
    "repeats them:",
    "  * not _rebalance_to's position writes - its guard compares total NAV",
    "    before and after over ALL positions to 1.0 ILS, using the same formula",
    "    the /portfolio route uses, so a 739 ILS move cannot pass it;",
    "  * not build_recommendations mutating the book inside the accept bracket -",
    "    six consecutive GET /recommendations moved NAV by exactly 0.0000;",
    "  * not card ORDER - the sweep was re-run in emitted, reversed and",
    "    rebalance-last order; worst drift 0.12 ILS, which was this cash bug.",
    "The reporting above is what turns the next occurrence into evidence instead",
    "of a bare number.",
    "",
    "Gate: SQLite 1330 passed / 4 skipped; Postgres 1326 passed / 12 skipped;",
    "ruff check app tests clean. The four new tests all fail on the old code.",
    "",
    "Not financial advice."
)

Step "0. What this changes about your book"
Write-Host "  BEFORE: accepting 'Rebalance toward your target mix' set your CASH" -ForegroundColor Yellow
Write-Host "          balance to zero and bought equities with it. Silently." -ForegroundColor Yellow
Write-Host "  AFTER:  the rebalance reshuffles invested value only. Your cash is" -ForegroundColor Yellow
Write-Host "          left exactly where it is." -ForegroundColor Yellow
Write-Host ""
Write-Host "  The legs it proposes will be SMALLER than before, because the" -ForegroundColor Yellow
Write-Host "  target weights now apply to invested value rather than to invested" -ForegroundColor Yellow
Write-Host "  value plus your cash. That is the correction, not a regression." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Any cash a PREVIOUS accept already spent is not restored by this." -ForegroundColor Yellow
Write-Host "  It bought real positions in the tracked book. If your cash line" -ForegroundColor Yellow
Write-Host "  looks wrong against your broker, that is the reconcile path, not" -ForegroundColor Yellow
Write-Host "  this deploy." -ForegroundColor Yellow
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
        "tests/test_rebalance_leaves_cash_alone.py",
        "tests/test_accept_conserves_nav.py",
        "tests/test_cash.py",
        "tests/test_recos_apply.py")
    Write-Host ""
    Write-Host "  The SQLite run above is NOT the Postgres job." -ForegroundColor Yellow
    Write-Host "  This PR changes decimal handling on every MONEY write, and that" -ForegroundColor Yellow
    Write-Host "  is precisely what the two databases disagree about. Run" -ForegroundColor Yellow
    Write-Host "  test-postgres before answering yes. Recipe: CLAUDE.md." -ForegroundColor Yellow
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
Write-Host "    .\scripts\smoke\smoke-rebalance-cash.ps1 -Sha $sha" -ForegroundColor White
Write-Host "  The proof needs one accept on the live book (opt-in, warns first):" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-rebalance-cash.ps1 -Sha $sha -AllowExecute" -ForegroundColor White

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today: note your cash balance. Accept a 'Rebalance toward your" -ForegroundColor Cyan
Write-Host "    target mix' card. The balance must be UNCHANGED. Before this" -ForegroundColor Cyan
Write-Host "    deploy it went to zero." -ForegroundColor Cyan
Write-Host "  * The rebalance legs will read smaller than they used to. They are" -ForegroundColor Cyan
Write-Host "    now sized against invested value, not invested value plus cash." -ForegroundColor Cyan
Write-Host "  * The accept response now carries nav_before_ils, nav_after_ils" -ForegroundColor Cyan
Write-Host "    and nav_drift_ils. Any accept whose drift exceeds the tax it" -ForegroundColor Cyan
Write-Host "    reported also writes a NAV CONSERVATION BREACH line to the" -ForegroundColor Cyan
Write-Host "    Railway log, naming every position that moved." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The intermittent +739.21 ILS leak is NOT fixed. It did not" -ForegroundColor DarkYellow
Write-Host "    reproduce; three candidates were ruled out by observation and" -ForegroundColor DarkYellow
Write-Host "    the commit message names them. A recurrence now logs a BREACH" -ForegroundColor DarkYellow
Write-Host "    line with the positions that moved, which is what was missing." -ForegroundColor DarkYellow
Write-Host "  * An accept REPORTS a conservation breach; it does not REFUSE one." -ForegroundColor DarkYellow
Write-Host "    Refusing needs the handlers to stop committing part-way through." -ForegroundColor DarkYellow
Write-Host "  * A TASE or London holding is still valued 100x too high: Yahoo" -ForegroundColor DarkYellow
Write-Host "    quotes agorot and pence, and fx_rate fails safe to 1.0. PR 18." -ForegroundColor DarkYellow
Write-Host "  * The two contradicting cards (sell Equities / buy SOXL) are" -ForegroundColor DarkYellow
Write-Host "    untouched. PR 19." -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "    PR 20." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 21." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
