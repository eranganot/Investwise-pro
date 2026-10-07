# SHIP PR 24 - the objective reaches the screener, and no tilt rides in with it.
#
#   .\scripts\deploy\ship-pr24-the-wiring-without-the-tilt.ps1
#   .\scripts\deploy\ship-pr24-the-wiring-without-the-tilt.ps1 -SkipTests
#
# THIS CHANGES NOTHING ABOUT WHICH SECURITIES ARE RECOMMENDED. That is the
# design, not a limitation. The objective now reaches the screener, but
# OBJECTIVE_WEIGHTS is empty, so every book ranks exactly as it did before.
#
# It also ships the counter that makes a separate, larger defect measurable on
# the live app -- and the smoke script exists mostly to read that number.
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
    "app/services/objective_weights.py",
    "app/services/recommendations.py",
    "app/agents/screener_agent.py",
    "app/api/routes/screener.py",
    "tests/test_the_objective_reaches_the_screener.py",
    "tests/test_the_agent_roster_is_what_actually_runs.py",
    "docs/AGENTS.md",
    "scripts/smoke/smoke-the-wiring-without-the-tilt.ps1",
    "scripts/deploy/ship-pr24-the-wiring-without-the-tilt.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 24: the objective reaches the screener, and no tilt rides in with it",
    "",
    "Eran's item 2: 'make sure all of them are aligned with the",
    "strategy/strategies that I pick to manage my portfolio.' Then, before",
    "deploying the first cut of this: 'I don't want anything to be hardcoded --",
    "everything must be calculated and not hardcoded.'",
    "",
    "Both constraints at once, and they pull in opposite directions. This ships",
    "the half that is defensible and states plainly why the other half is not.",
    "",
    "THE BUG. recommendations.py called screen_equities(top_n=5) with no",
    "weights, so ScreenerEngine used DEFAULT_WEIGHTS for every book. Grow and",
    "Preserve ranked the identical candidates in the identical order. The",
    "objective influenced HOW MUCH was bought -- through the target mix and the",
    "funding logic -- and never WHAT. The one code path that could supply",
    "weights was a query string on /api/v1/screener, and nothing in the app ever",
    "built that URL. The plumbing existed and reached nobody: plan_service.py:55",
    "writes objective into the settings object every engine holds, and grepping",
    "the tree for a read of it returns nothing.",
    "",
    "WHAT SHIPS: the wiring. Both call sites pass the objective, /screener",
    "defaults to it, and the pin travels with every buy idea.",
    "",
    "WHAT DOES NOT SHIP: any weights. OBJECTIVE_WEIGHTS is EMPTY, weights_for",
    "returns None for every objective, ScreenerEngine uses DEFAULT_WEIGHTS, and",
    "THE RANKING IS IDENTICAL TO BEFORE FOR EVERY BOOK. A wiring change that",
    "quietly reordered real buy recommendations while calling itself plumbing",
    "would be worse than the bug it fixes, so that is asserted rather than",
    "assumed - test_every_objective_still_ranks_exactly_as_it_did_before.",
    "",
    "WHY EMPTY, WITH THE EVIDENCE.",
    "",
    "The first cut carried four hand-chosen vectors behind OWNER_REVIEWED =",
    "False. That flag is an admission of a problem, not a solution to it: the",
    "numbers still shipped and still reordered real recommendations.",
    "DEFAULT_WEIGHTS itself has no stated provenance, so 'close to the default'",
    "is not a defence either.",
    "",
    "THE DERIVATION WAS ATTEMPTED AND DID NOT SURVIVE MEASUREMENT. Each",
    "objective's strategies in strategies.py carry a real basket, so the factor",
    "signature of those baskets could BE the tilt -- calculated from live",
    "fundamentals, from data the app already holds and the user can already see.",
    "Grow resolves 14 names, Balanced 5, Income 6, Preserve 6. Computed end to",
    "end, percentiles against the universe plus the basket names (69 tickers):",
    "",
    "    Grow      value .241  growth .292  quality .253  income .214",
    "    Balanced  value .213  growth .224  quality .278  income .285",
    "    Income    value .229  growth .322  quality .252  income .196",
    "    Preserve  value .262  growth .238  quality .212  income .288",
    "",
    "INCOME CAME OUT WEIGHTING GROWTH HIGHEST AND INCOME LOWEST -- the exact",
    "contradiction the tilt exists to remove, reproduced by the derivation. All",
    "four vectors sit inside 0.20-0.33 and would barely differentiate anything.",
    "",
    "The cause is builtin.py:56-81: the sandbox provider seeds every fundamental",
    "off a sha1 of the ticker, so SCHD's dividend yield here is a hash. The",
    "method cannot be validated on the only data this environment has. It might",
    "work on Yahoo. It might also not -- the Income and Preserve baskets are",
    "almost entirely ETFs, and pass-through aggregates of P/E, P/B and ROE are",
    "weak inputs for exactly the objectives where the tilt matters most.",
    "",
    "So: ship numbers that look derived and are not validated -- the",
    "strategy_profile.assumptions_for() failure PR 16 exists to undo, where a",
    "static table priced TQQQ at 12%/yr and 55% vol BUY-AND-HOLD and four",
    "screens inherited it looking measured -- or ship the wiring and leave the",
    "table empty. This takes the second. ITEM 2 STAYS OPEN, with a proven reason",
    "rather than an oversight, and a defensible tilt is four numbers away.",
    "",
    "AND A SECOND DEFECT, FOUND WHILE BUILDING THIS, NOT FIXED HERE.",
    "",
    "The first cut of the tests passed alone and failed intermittently in the",
    "suite. The cause was not the test. _fundamentals_for fires one provider call",
    "per candidate in a tight loop and the tier's bucket is TokenBucket(20, 20):",
    "",
    "    58 equity candidates in, 20 ranked, 38 RateLimitedError",
    "",
    "The survivors are literally the first twenty entries of equity_universe():",
    "AAPL through PFE. MRK, ABBV, PG, KO, PEP, COST, WMT, HD, MCD, NKE, DIS, XOM",
    "and twenty-six more were never scored at all -- so 'the best buys in the",
    "universe' meant 'the best of whichever names the bucket allowed', and which",
    "ones survive depends on transport timing rather than merit.",
    "",
    "ALL SEVEN Israeli names are among the 38. That is a SECOND, previously",
    "unknown cause for item 3, independent of the known one -- that all seven",
    "'TASE_STOCKS' entries are NYSE/NASDAQ listings and there is still not one",
    "Tel Aviv listing in the universe.",
    "",
    "This is the bug screener_agent.py already documents TWICE. _history draws",
    "the line between 'the provider ANSWERED and there is nothing there' and 'we",
    "could not reach it'. screen_commodities and screen_bonds both act on it and",
    "log the unreachable set. screen_equities never got it. It has it now, and",
    "coverage rides in the recommendations payload.",
    "",
    "NOT FIXED HERE, DELIBERATELY. Production runs Yahoo, where each call takes",
    "real network time and the bucket refills as the loop runs, so it may drop",
    "nothing at all. That is a GUESS, and the reason it cannot be checked is",
    "exactly the silence this removes. Smoke section 3 reads the real number off",
    "production; PR 25 fixes the limiter only if that number says it needs",
    "fixing. No latency spent on a hypothesis.",
    "",
    "GATE, INCLUDING THE RUN THAT WENT RED ON THE PREVIOUS CUT OF THIS BRANCH.",
    "",
    "    test_c4_per_sleeve.py::test_two_sleeves_wanting_one_ticker_...",
    "    sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table",
    "    'trading_rules' expected to update 1 row(s); 0 were matched.",
    "",
    "plus an SAWarning on the same table from test_c3_funding. NOT REPRODUCED:",
    "it passed alone, passed paired, and the next full Postgres run on the",
    "identical tree was clean. Ordering is deterministic here, so a deterministic",
    "fault would have repeated. The three previous Postgres runs never mentioned",
    "trading_rules -- but three clean runs do not disprove a race, and",
    "build_recommendations does concurrent work through _offload with",
    "trading_rules writes inside it.",
    "",
    "ROOT CAUSE: UNKNOWN. CONFIDENCE: nothing. Same family as the +739.2 ILS",
    "leak. NO SPECULATIVE FIX. CI can go red on it; if it does, send the block.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  NOTHING ABOUT WHICH SECURITIES ARE RECOMMENDED." -ForegroundColor Green
Write-Host "  The objective now reaches the screener, but no factor tilt is" -ForegroundColor Green
Write-Host "  defined, so every book ranks exactly as it does today. The suite" -ForegroundColor Green
Write-Host "  asserts that rather than assuming it." -ForegroundColor Green
Write-Host ""
Write-Host "  WHY NO TILT: you said nothing hardcoded. Four hand-chosen" -ForegroundColor Cyan
Write-Host "  vectors behind an 'unreviewed' flag is still hardcoding. The one" -ForegroundColor Cyan
Write-Host "  derivation available - the factor signature of each objective's" -ForegroundColor Cyan
Write-Host "  own strategy baskets - was computed and produced INCOME weighting" -ForegroundColor Cyan
Write-Host "  GROWTH highest and INCOME lowest, because the sandbox provider" -ForegroundColor Cyan
Write-Host "  seeds fundamentals off a hash of the ticker. It cannot be" -ForegroundColor Cyan
Write-Host "  validated here, so it does not ship. Item 2 stays open." -ForegroundColor Cyan
Write-Host ""
Write-Host "  WHAT IS WORTH DEPLOYING THIS FOR: the coverage counter. In the" -ForegroundColor Yellow
Write-Host "  sandbox the screener ranks 20 of 58 candidates and silently drops" -ForegroundColor Yellow
Write-Host "  38 - including all seven Israeli names. The smoke script measures" -ForegroundColor Yellow
Write-Host "  that on YOUR live app, and the number decides PR 25." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
python -m pytest -q tests/test_the_pins_are_installable_and_installed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  STOP. The versions installed here are not the versions" -ForegroundColor Red
    Write-Host "  requirements.txt pins, so nothing measured in this" -ForegroundColor Red
    Write-Host "  environment can be quoted as a fact about the project." -ForegroundColor Red
    Write-Host ""
    Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}
Write-Host "  This interpreter matches the pins." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run."
} else {
    Invoke-Suite -Focus @(
        "tests/test_the_objective_reaches_the_screener.py",
        "tests/test_the_agent_roster_is_what_actually_runs.py")
    Write-Host ""
    Write-Host "  Then run test-postgres. Both backends must report 0 skipped." -ForegroundColor Yellow
    Write-Host "  NOTE: a previous cut of this branch saw ONE unreproduced" -ForegroundColor Yellow
    Write-Host "  Postgres failure on trading_rules. If you see it, send it to" -ForegroundColor Yellow
    Write-Host "  me - that is a second sighting and worth more than reasoning." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, with 0 skipped? (y/N)") -ne "y") {
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

Step "6. Smoke - and SECTION 3 IS THE ONLY REASON TO RUN IT TODAY"
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-the-wiring-without-the-tilt.ps1 -Sha $sha" -ForegroundColor White
Write-Host ""
Write-Host "  Section 3 prints how many candidates your LIVE app ranked." -ForegroundColor Yellow
Write-Host "    * all of them  -> the limiter is a sandbox artifact, and PR 25" -ForegroundColor Yellow
Write-Host "      only has to add real Tel Aviv listings." -ForegroundColor Yellow
Write-Host "    * fewer        -> the limiter is real on Yahoo too, and PR 25" -ForegroundColor Yellow
Write-Host "      must fix it FIRST: adding listings to a list that never gets" -ForegroundColor Yellow
Write-Host "      read would change nothing." -ForegroundColor Yellow
Write-Host ""
Write-Host "  SEND ME THAT SECTION EITHER WAY." -ForegroundColor Cyan

Step "7. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today -> the buy ideas should be UNCHANGED. This PR ranks" -ForegroundColor Cyan
Write-Host "    nothing differently. If a name moved, tell me." -ForegroundColor Cyan
Write-Host "  * Your NAV must be unchanged." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Item 2 is HALF fixed. No factor tilt is defined, so Grow and" -ForegroundColor DarkYellow
Write-Host "    Preserve still rank identically. The wiring is ready for one." -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe, and all" -ForegroundColor DarkYellow
Write-Host "    seven Israeli names may be rate-limited out before scoring." -ForegroundColor DarkYellow
Write-Host "    Both are item 3. PR 25." -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows," -ForegroundColor DarkYellow
Write-Host "    forever - and is captioned with the price-refresh clock." -ForegroundColor DarkYellow
Write-Host "  * The Allocation Agent's veto is unreachable from the app." -ForegroundColor DarkYellow
Write-Host "  * settings.objective is threaded into every engine and read by" -ForegroundColor DarkYellow
Write-Host "    nothing; TaxEngine reads no plan setting at all." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED, and a Postgres run went red" -ForegroundColor DarkYellow
Write-Host "    once on trading_rules without repeating." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you" -ForegroundColor DarkYellow
Write-Host "    accepted." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
