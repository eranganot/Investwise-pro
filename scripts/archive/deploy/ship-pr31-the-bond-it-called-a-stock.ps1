# SHIP PR 31 - 13.7% of the book was bonds and every screen called it equity.
#
#   .\scripts\deploy\ship-pr31-the-bond-it-called-a-stock.ps1
#   .\scripts\deploy\ship-pr31-the-bond-it-called-a-stock.ps1 -SkipTests
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
    "app/services/allocation_mix.py",
    "app/services/context_service.py",
    "app/services/funding_service.py",
    "app/services/intake_service.py",
    "app/services/market_impact.py",
    "app/services/recommendations.py",
    "tests/test_the_bond_was_filed_as_a_stock.py",
    "tests/test_pr4_cards.py",
    "scripts/smoke/smoke-asset-class.ps1",
    "scripts/deploy/ship-pr31-the-bond-it-called-a-stock.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 31: the bond it called a stock",
    "",
    "MEASURED on production 63c0559, read-only, 2026-09-02:",
    "",
    "    /api/v1/portfolio  BND 13.1331 sh @ 71.79 USD = 2,842.15 ILS",
    "                       of a 20,777.45 NAV, meta.asset_class = 'Equities'",
    "    /api/v1/mix        current_allocation {Equities 0.9703, Cash 0.0298}",
    "                       Fixed Income: ABSENT. Not 0.0 - absent.",
    "                       BUY  Fixed Income  2,243.96",
    "                       SELL Equities      1,626.87",
    "",
    "2,243.96 is exactly 10.8% of NAV: the WHOLE fixed-income target, because",
    "the app believed the holding was zero. Accepting that card lands the book",
    "at about 24% bonds against a 10.8% target. The equity leg is wrong in the",
    "same breath - the book was 83.3% equities against 89.2%, so the card asked",
    "to sell what it was short of and buy what it already held too much of.",
    "Both signs inverted, from one string.",
    "",
    "THE CHAIN, END TO END",
    "",
    "1. intake_service.buy_into_book defaulted asset_class='Equities'.",
    "2. strategy_service._execute_funded_sleeve - the sleeve funder, the busiest",
    "   caller - passes none. The audit log puts two POST /plan/sleeves/fund",
    "   calls at 17:23 and 17:24 on 2026-08-31, which is where this row was made.",
    "3. So the row was stamped 'Equities' on the way in.",
    "4. allocation_mix.classify returned that stamp on its FIRST line, before",
    "   reading FIXED_INCOME_TICKERS four lines below - where 'BND' has sat",
    "   since the fix for this same failure one asset class over. That set's own",
    "   comment ends 'every Vanguard and Schwab ticker, i.e. all the cheap ones'.",
    "",
    "Nothing here is a new capability. Every part of the answer was already in",
    "the file; the precedence read them in the wrong order.",
    "",
    "THE PRECEDENCE, NOW",
    "",
    "  Cash first and alone. 'CASH' is in neither table, so letting the tables",
    "  run first would file the shekel row as an equity and _class_gap_recs",
    "  would offer to top it up - buying cash with cash.",
    "  Then a class a HUMAN set, recorded by asset_class_source='user'.",
    "  update_position is the ONE writer of that flag, so reaching this branch",
    "  means a person named the class through the API and the app has no",
    "  business overruling them.",
    "  Then the tables.",
    "  Then whatever was stored, which is only ever a default. Then Equities.",
    "",
    "_from_tables is split out of classify so the tables can be consulted",
    "WITHOUT the Equities fallthrough. 'I do not recognise this ticker' and",
    "'this ticker is an equity' are different answers, and collapsing them is",
    "what let a default outrank a lookup.",
    "",
    "NO DATA MIGRATION. The stale 'Equities' stamps stay on the rows and simply",
    "stop being authoritative. That is a strictly smaller change than rewriting",
    "a user's holdings in place, and it needs no repair script, no downtime and",
    "no way to get it half-done.",
    "",
    "WHAT ELSE WAS READING THE FIELD RAW",
    "",
    "  api/routes/intake.py:154 - the holdings card. This is literally where",
    "    'BND  NASDAQ  Equities' was printed on screen.",
    "  services/context_service.py - the assistant and digest context, which",
    "    read the stamp raw AND then applied a second 'or Equities' on top of",
    "    it: two independent fallbacks to the same wrong answer, in the one",
    "    module whose docstring says the LLM 'can't invent' these numbers. A",
    "    number it cannot invent can still be wrong.",
    "  funding_service, market_impact, recommendations (x3) - each spelling out",
    "    classify(p.ticker, p.market, (p.meta or {}).get('asset_class')).",
    "",
    "classify_holding() reads a Position row or a projection dict correctly and",
    "every one of those call sites now uses it, so the provenance flag did not",
    "have to be threaded through seven call sites - which is how the eighth",
    "forgets.",
    "",
    "ONE EXISTING TEST WAS ASSERTING THE DEFECT. test_pr4_cards.py:329 read",
    "",
    "    assert classify('GLD', 'NYSE', asset_class='Equities') == 'Equities'",
    "",
    "and it is now the opposite, plus a case proving a human's override still",
    "wins. The lookup was well covered; the branch that outranked it was not",
    "covered once, because every other test in the area called classify with no",
    "meta at all.",
    "",
    "upsert_positions replaces row.meta wholesale, so a deliberate class is now",
    "carried across a re-import that does not name one. Without it a second",
    "intake silently demotes an override back to a guess, and the demotion is",
    "invisible because the value it lands on is usually the same one.",
    "",
    "Gate: SQLite 1483 passed / 0 failed; Postgres 16 1483 passed / 0 failed;",
    "ruff check app tests clean. Baseline on unmodified HEAD in the same window",
    "was 1475 (1472 + the 3 py-vapid pin tests, which fail only where that wheel",
    "cannot build), so the delta is exactly this PR's 8 new tests.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR MIX CARD WILL FLIP SIGN ON BOTH LEGS, and that is the point." -ForegroundColor Green
Write-Host ""
Write-Host "  Before: SELL Equities 1,627 / BUY Fixed Income 2,244" -ForegroundColor Yellow
Write-Host "  After:  the book reads 83.3 / 13.7 / 3.0 against a 89.2 / 10.8" -ForegroundColor Green
Write-Host "          target, so you are SHORT equities and OVER on bonds." -ForegroundColor Green
Write-Host ""
Write-Host "  NOTHING IS BOUGHT OR SOLD. This changes how a holding is LABELLED." -ForegroundColor Yellow
Write-Host "  Quantities, prices and NAV are untouched." -ForegroundColor Yellow
Write-Host ""
Write-Host "  You may still see no rebalance card afterwards. That is a" -ForegroundColor Cyan
Write-Host "  DIFFERENT defect and PR 32 owns it: the card ids carry no amount," -ForegroundColor Cyan
Write-Host "  so marking one done mutes it for 14 days however far the drift" -ForegroundColor Cyan
Write-Host "  travels. As of 2026-09-02 nine cards were hidden that way." -ForegroundColor Cyan
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
    Warn "-SkipTests: the gate did NOT run. This PR changes what the mix card"
    Warn "says about your book. Strange one to skip."
} else {
    Invoke-Suite -Focus @(
        "tests/test_the_bond_was_filed_as_a_stock.py",
        "tests/test_pr4_cards.py",
        "tests/test_bonds_are_buyable.py",
        "tests/test_rebalance_leaves_cash_alone.py",
        "tests/test_card_claims.py")
    Write-Host ""
    Write-Host "  Then run test-postgres. It is reproducible locally and it is" -ForegroundColor Yellow
    Write-Host "  the one job the SQLite run does not prove:" -ForegroundColor Yellow
    Write-Host "    apt-get install -y postgresql; service postgresql start" -ForegroundColor White
    Write-Host "    create the investwise role + database, point DATABASE_URL at it" -ForegroundColor White
    Write-Host "    pytest -q" -ForegroundColor White
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

Step "6. Smoke - section 2 and 3 are the whole question"
Write-Host ""
Write-Host ("    .\scripts\smoke\smoke-asset-class.ps1 -Sha " + $sha) -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 reads every catalogue bond ticker you hold and prints" -ForegroundColor Yellow
Write-Host "  what it is filed as. Section 3 checks the mix agrees with the" -ForegroundColor Yellow
Write-Host "  holdings, and that no card offers to buy a target you already hold." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host ("  main: " + $sha) -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Holdings card: BND should read 'Fixed Income', not 'Equities'." -ForegroundColor Cyan
Write-Host "  * Plan -> your mix: Fixed Income should be about 13.7%, not 0%." -ForegroundColor Cyan
Write-Host "  * NAV must be unchanged apart from the market. This PR relabels;" -ForegroundColor Cyan
Write-Host "    it moves nothing." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The rebalance card may still not APPEAR. Card ids carry no" -ForegroundColor DarkYellow
Write-Host "    amount, so a 'done' click mutes them for 14 days. PR 32." -ForegroundColor DarkYellow
Write-Host "  * The core's model basket names VTI and the book holds none. The" -ForegroundColor DarkYellow
Write-Host "    class-level card is the mechanism for that and no second card" -ForegroundColor DarkYellow
Write-Host "    was added, because two cards about one book is the invariant" -ForegroundColor DarkYellow
Write-Host "    this repo already enforces." -ForegroundColor DarkYellow
Write-Host "  * The account history still zeroes any day the book changed. PR 34." -ForegroundColor DarkYellow
Write-Host "  * The 07:00 digest still composes figures across bases. PR 33." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
