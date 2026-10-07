# SHIP PR 27 - the screener can see, but three of the numbers it saw were wrong.
#
#   .\scripts\deploy\ship-pr27-the-numbers-it-was-given.ps1
#   .\scripts\deploy\ship-pr27-the-numbers-it-was-given.ps1 -SkipTests
#
# PR 25 got the equity screener answering again. This is what the answers
# contained. All three defects were MEASURED on the live top 25 on 2026-08-31.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags; run BOTH backends.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/engines/screener_engine.py",
    "app/providers/live.py",
    "app/schemas/screener.py",
    "app/services/recommendations.py",
    "tests/test_the_screener_reads_what_it_was_given.py",
    "scripts/smoke/smoke-the-numbers-it-was-given.ps1",
    "scripts/deploy/ship-pr27-the-numbers-it-was-given.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 27: the screener could see again, and three of the numbers were wrong",
    "",
    "PR 25 got the equity screener answering after months of silence. VERIFIED",
    "LIVE 2026-08-31: 58 of 58 candidates ranked, five equity buy ideas where",
    "there had been none, coverage clean. What that exposed is that the ENGINE",
    "consuming those numbers had never been exercised on real data at scale,",
    "and it makes the same mistake PR 25 fixed one layer down: a missing answer",
    "and a bad answer rendering identically.",
    "",
    "MEASURED, on the live top 25:",
    "",
    "    BRK-B  #7   pb 0.00096701446   value 98.2 - the highest in the top 25",
    "                reasons: 'Trades near book value (P/B 0.0)'",
    "    MCD    #11  pb + roe + debt_to_equity all null",
    "                value from P/E alone, quality from profit margin alone,",
    "                and quality 78.3 - the HIGHEST of the names checked",
    "    JPM #2      debt_to_equity null, quality 61.0",
    "    BAC #5      debt_to_equity null",
    "",
    "    8 of the 25 carried at least one missing factor. None of them said so.",
    "",
    "1. AN IMPLAUSIBLE P/B WAS RANKED AS A BARGAIN, AND THE CARD SAID SO.",
    "",
    "Berkshire's price-to-book is about 1.6. Yahoo returns 0.00096701446 - the",
    "class-B PRICE over the class-A BOOK VALUE PER SHARE, off by ~1650x. P/B is",
    "a lower-is-better column, so the broken number handed BRK-B very nearly the",
    "best value percentile in the cohort, and _reasons then asserted it to the",
    "user in plain English. A caption that vouches for a number tells the reader",
    "the checking has already been done; PR 22 recorded that exact failure on",
    "the history chart.",
    "",
    "_safe_pe had this instinct already and it was never generalised. Same 'a",
    "guard protects the path it is on, not the behaviour' lesson STATUS.md",
    "records against the NAV-inflation bug.",
    "",
    "_safe_pb returns None rather than the worst value, and the difference",
    "matters: a P/E of zero means something specific (no earnings), while an",
    "implausible P/B says nothing about the DIRECTION of the error. Berkshire's",
    "real ratio is mid-cohort; the minor-unit case runs the other way (PR 25",
    "measured TEVA 5.48 against TEVA.TA 1657.9). Scoring it worst would be as",
    "wrong as scoring it best. Ground or abstain.",
    "",
    "This also disarms the trap PR 25 left for the Tel Aviv work: a .TA P/B is",
    "now rejected rather than ranked.",
    "",
    "2. A MISSING FACTOR HAD TWO OPPOSITE MEANINGS.",
    "",
    "    _mean([50.0, None])  -> 50.0   dropped   (quality has 3 slots)",
    "    _mean([None])        ->  0.0   the WORST (income has 1 slot)",
    "",
    "The only thing deciding which is how many factors happen to share the",
    "pillar. Dropping does not score the gap 50 - it scores it at the mean of",
    "whatever else is in the pillar, so a name that is best-in-cohort on the two",
    "factors it was measured on took a PERFECT quality score for a balance sheet",
    "nobody looked at. Meanwhile 'Yahoo did not say' was rendered identically to",
    "'worst payer in the cohort'.",
    "",
    "_pillar scores an unmeasured slot neutral, in both directions. A name",
    "cannot win a factor nobody measured and cannot be convicted on one. _mean",
    "is UNCHANGED - rank_commodities and rank_bonds depend on it and neither has",
    "the pillar problem; a test pins that so a later cleanup does not merge the",
    "two and silently move the bond rankings.",
    "",
    "The gap is disclosed rather than swallowed: data_gaps names every factor",
    "that did not contribute and why ('not reported' vs 'implausible'), and a",
    "gapped name is flagged partial-data. It is a LABEL, not a second penalty -",
    "docking a name for its provider's silence is the mirror of the bug.",
    "metrics keeps the raw value, because a silently nulled field cannot be",
    "diagnosed.",
    "",
    "3. A COMPANY THAT PAYS NOTHING ANSWERS ZERO. THE ADAPTER FILED IT AS",
    "UNKNOWN. Measured against Yahoo:",
    "",
    "    BRK-B  dividendYield {}   trailingAnnualDividendYield {'raw': 0.0}",
    "    AMZN   dividendYield {}   trailingAnnualDividendYield {'raw': 0.0}",
    "    JPM    dividendYield {'raw': 0.0168}",
    "",
    "Yahoo drops dividendYield for a non-payer while still reporting, on the",
    "same object, that the trailing yield is zero. Reading only the first field",
    "filed a MEASURED zero as 'we do not know'. It produced the right ranking by",
    "luck, because _mean([None]) collapsed to 0.0 and 0.0 was the truth - so the",
    "moment _pillar starts scoring an unmeasured factor neutral (as it must),",
    "every non-payer would be handed an income score it has not earned. Right",
    "answer, wrong route, and the route is what breaks when the data shifts.",
    "This has to land in the same commit as _pillar, not after it.",
    "",
    "4. THE COVERAGE PIN VANISHED IN THE CASE IT EXISTS TO EXPLAIN. _pin was",
    "attached only to equity picks, so a scan that ranked nothing fell through",
    "to audit_block, which has no coverage key. PR 25's own smoke script names",
    "this: 'PR 24 attached the coverage pin only to equity ideas, so it",
    "disappears in exactly the case worth diagnosing.' Reproduced against PR",
    "25's bug shape - the agent held {requested: 58, ranked: 0} and the payload",
    "said nothing. _buy_ideas now publishes the pin through a caller-supplied",
    "dict, filled before anything can shorten the list. A dict rather than a",
    "module global, so two concurrent scans cannot hand each other the wrong",
    "coverage.",
    "",
    "ELEVEN OF THE FOURTEEN NEW TESTS WERE CONFIRMED TO FAIL AGAINST THE OLD",
    "CODE before being accepted. The three that pass are deliberate no-change",
    "guards (_mean unchanged; a gap is labelled not penalised; None still means",
    "'Yahoo did not say').",
    "",
    "Two of those tests were REWRITTEN because the first versions went green",
    "against the bug. Three names varying only in leverage rank correctly under",
    "both engines, because with the other factors tied the dropped slot lands on",
    "50 by arithmetic accident; and tying the unmeasured name at the top does",
    "not help either, because _pct_ranks gives ties the MEAN of the ranks they",
    "span. The defect only shows when the surviving factors are top-of-cohort",
    "and UNTIED. A test that passes against the bug it was written for is the",
    "failure this repo has recorded more than once.",
    "",
    "Gate: SQLite 1465 passed / 1 failed; Postgres 1465 passed / 1 failed; ruff",
    "check app tests clean. The one failure is",
    "test_minor_unit_currencies.py::test_a_row_already_stored_in_agorot_is_repaired",
    "and it is NOT from this PR: it fails identically on unmodified HEAD in a",
    "same-window control run on both backends, and it fails alone on a fresh",
    "process. It is recorded in STATUS.md as an open, unexplained item rather",
    "than fixed here.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR BUY LIST WILL MOVE, and that is the point." -ForegroundColor Green
Write-Host ""
Write-Host "  Names whose scores rested on a factor nobody measured rank lower." -ForegroundColor Green
Write-Host "  BRK-B in particular loses a value pillar it never earned: it was" -ForegroundColor Green
Write-Host "  ranked #7 on a price-to-book of 0.00097, which is Yahoo dividing" -ForegroundColor Green
Write-Host "  the class-B price by class-A book value per share." -ForegroundColor Green
Write-Host ""
Write-Host "  The card said so out loud: 'Trades near book value (P/B 0.0)'." -ForegroundColor Yellow
Write-Host ""
Write-Host "  PRICES AND NAV ARE NOT TOUCHED. This changes which names get" -ForegroundColor Yellow
Write-Host "  RANKED. It buys nothing." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Every pick now carries data_gaps naming the factors that did not" -ForegroundColor Cyan
Write-Host "  contribute. Measured before this PR: 8 of the top 25 had at least" -ForegroundColor Cyan
Write-Host "  one, and not one of them said so." -ForegroundColor Cyan
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
    Warn "-SkipTests: the gate did NOT run. This PR changes what the app"
    Warn "recommends. Strange one to skip."
} else {
    Invoke-Suite -Focus @(
        "tests/test_the_screener_reads_what_it_was_given.py",
        "tests/test_screener.py",
        "tests/test_the_objective_reaches_the_screener.py",
        "tests/test_market_data_live.py")
    Write-Host ""
    Write-Host "  EXPECT ONE PRE-EXISTING FAILURE, and do not let it stop you:" -ForegroundColor Yellow
    Write-Host "    test_minor_unit_currencies.py::test_a_row_already_stored_in_agorot_is_repaired" -ForegroundColor Yellow
    Write-Host "  It fails on unmodified HEAD too, on both backends, in a control" -ForegroundColor Yellow
    Write-Host "  run taken in the same window. If you see any OTHER failure, stop." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Then run test-postgres. Both backends, same one failure." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, with only that one failure? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Smoke - section 2 is the whole question"
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-the-numbers-it-was-given.ps1 -Sha $sha" -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 proves no scored pillar rests on an impossible P/B." -ForegroundColor Yellow
Write-Host "  Section 4 reads the full ranked list, which is where BRK-B is" -ForegroundColor Yellow
Write-Host "  visible whether or not it makes the top 5." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today -> buy ideas. The equity names SHOULD have moved." -ForegroundColor Cyan
Write-Host "  * No card should say 'Trades near book value (P/B 0.0)'." -ForegroundColor Cyan
Write-Host "  * NAV must be unchanged: 21333.59 ILS across 4 positions as of" -ForegroundColor Cyan
Write-Host "    2026-08-31. A different number is a finding." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe. The P/B" -ForegroundColor DarkYellow
Write-Host "    trap that blocked that work is disarmed; the listings are not" -ForegroundColor DarkYellow
Write-Host "    added. That is the other half of item 3." -ForegroundColor DarkYellow
Write-Host "  * The minor-unit repair test above is UNEXPLAINED. Not this PR's," -ForegroundColor DarkYellow
Write-Host "    proven by control runs, but it is a real open question." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS rebalance leak is UNEXPLAINED." -ForegroundColor DarkYellow
Write-Host "  * Item 2 has no factor tilt: nothing derives one." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  AND THE ONE THAT HAS BEEN OPEN FOR FOUR WEEKS:" -ForegroundColor Red
Write-Host "  * AGENT_API_KEY and AUTH_PASSWORD are STILL the published values." -ForegroundColor Red
Write-Host "    VERIFIED 2026-08-31: the key committed in 33 scripts still" -ForegroundColor Red
Write-Host "    returns 200 on /api/v1/portfolio. PR 26 is not even pushed yet." -ForegroundColor Red
Write-Host "    See ROTATION.md." -ForegroundColor Red
Write-Host ""
Write-Host "  Not financial advice." -ForegroundColor DarkGray
