# SHIP PR 23 - the agent map. ONE COMMIT. DOCUMENTATION AND TESTS ONLY.
#
#   .\scripts\deploy\ship-pr23-the-agent-map.ps1
#   .\scripts\deploy\ship-pr23-the-agent-map.ps1 -SkipTests
#
# NO APP BEHAVIOUR CHANGES. Nothing in this commit touches a code path that
# runs in production - it adds docs/AGENTS.md, a test file that keeps it
# honest, a smoke script, and this one. Railway will build and deploy it
# because Railway deploys every green push, but the running app is identical
# afterwards. The deploy is a no-op; the REPOSITORY is what changes.
#
# WHAT THE MAP FOUND. Four things that are not documentation problems, three of
# them pinned as DEFECTS in the test file - meaning the tests pass while the bug
# is present and FAIL when it is fixed, telling you to update the map:
#
#   1. "What's moving" reads a four-row fixture, unconditionally.  <- your item 5
#   2. That list is captioned with the PRICE-refresh clock.
#   3. The Allocation Agent's veto is unreachable from the app.
#   4. No agent reads your strategy when choosing what to buy.     <- your item 2
#
# Read docs/AGENTS.md before pushing. It is the deliverable, not the commit.
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
    "docs/AGENTS.md",
    "tests/test_the_agent_roster_is_what_actually_runs.py",
    "scripts/smoke/smoke-the-agent-map-is-measured.ps1",
    "scripts/deploy/ship-pr23-the-agent-map.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 23: the agent map, and it found four things",
    "",
    "Eran: 'map all the agents in the tool and for each one write its goals,",
    "objectives, and purpose.' docs/AGENTS.md is that map. Every claim in it",
    "carries a file:line you can open; where the code and the label disagree,",
    "the disagreement is written down rather than smoothed over.",
    "",
    "THE HEADLINE. war_room.py:18 declares SEVEN agents -- Research, Alpha,",
    "Risk, Tax, Decision, Adversary, UX. FIVE classes exist under app/agents/:",
    "ResearchAgent, OpportunityAgent, FeeAgent, AllocationAgent, Adversary. The",
    "overlap between the two sets is TWO. Alpha, Risk, Tax and Decision are",
    "ENGINES -- LagEngine, RiskEngine, TaxEngine, DecisionEngine -- rendered as",
    "dialogue; UX is a render step with no class at all. Nothing in the suite",
    "noticed, because nothing had ever compared the label to the code.",
    "",
    "Recommendation in the doc: RENAME the four engine speakers in the",
    "transcript rather than promote five engines to agents. Promoting them is a",
    "large change bought for a labelling problem, and the engines are not",
    "deficient -- they are accurately named where they live.",
    "",
    "FOUR FINDINGS THAT ARE NOT DOCUMENTATION PROBLEMS:",
    "",
    "1. 'WHAT'S MOVING' READS A FOUR-ROW FIXTURE, UNCONDITIONALLY. Item 5.",
    "registry.py:63 returns BuiltinEconomicDataProvider() with no settings",
    "branch, and app/providers/live.py has a market provider and an FX provider",
    "and NO economic provider to switch to. builtin.py:113-130 returns the same",
    "four events on every call, on every deployment. Explore -> What's moving",
    "cannot show a fifth item today no matter what the market does. That is the",
    "literal answer to 'I'm not getting enough cards from What's moving': you",
    "are getting four because four is all there are.",
    "",
    "2. THAT LIST IS CAPTIONED WITH THE PRICE-REFRESH CLOCK. index.html:2791",
    "renders 'Updates hourly - last refreshed N' from last_refreshed, which",
    "market.py:44 fills from market_status() -- the PRICE refresh timestamp,",
    "unrelated to the events above it. A list that has never changed, under a",
    "clock that genuinely ticks. Same class of defect as the +35% caption PR 22",
    "removed: a caption that vouches for a number tells the reader the checking",
    "has already been done.",
    "",
    "3. THE ALLOCATION AGENT'S VETO IS UNREACHABLE FROM THE APP. It is the only",
    "veto in the codebase raised BEFORE a card is built rather than annotated",
    "onto one. Its single caller is pipeline.py:86,102, inside",
    "PipelineOrchestrator.generate, reached only from POST",
    "/api/v1/decision-feed/generate -- an ANALYST-role endpoint the front end",
    "never calls (zero references to 'decision-feed' in index.html). Even there",
    "it needs req.allocation AND req.asset_class_map, both defaulting to None",
    "at schemas/feed.py:20-21. The cards the user actually sees come from",
    "recommendations.py, which never imports AllocationAgent. Nothing on the",
    "live path stops a recommendation buying further into an overweight class.",
    "",
    "4. NO AGENT READS YOUR STRATEGY WHEN CHOOSING WHAT TO BUY. Item 2, and it",
    "is a measurement rather than an impression. screen_equities(weights=...)",
    "is the only strategy-shaped input any agent accepts. recommendations.py:",
    "2698 calls ag.screen_equities(top_n=5) -- no weights, so ScreenerEngine",
    "uses DEFAULT_WEIGHTS. The one code path that supplies weights is a query",
    "string on /api/v1/screener (screener.py:31) and nothing in the app builds",
    "that URL. Grow and Preserve rank the identical candidates in the identical",
    "order. The strategy influences HOW MUCH is bought, never WHAT.",
    "",
    "None of the four is fixed here. Each is a PR; (1) and (2) are the same PR.",
    "",
    "THE TESTS ARE THE POINT. A map nobody checks is a map that quietly stops",
    "being true, so test_the_agent_roster_is_what_actually_runs.py fails when:",
    "an agent class is added or removed (discovered by WALKING app/agents/, not",
    "by reading a list -- a list checked against itself proves nothing); the",
    "war-room speaker list changes; the Adversary's BLOCK stops being consulted",
    "at all three stages; ResearchAgent stops being read-only; or any of the",
    "six file:line anchors quoted in the doc drifts off its target.",
    "",
    "Findings 1, 3 and 4 are pinned as DEFECTS -- the tests pass while the bug",
    "is present and FAIL when it is fixed, with a message saying to update the",
    "map. Fixing them should be loud.",
    "",
    "NO APP BEHAVIOUR CHANGES. Docs and tests only. Railway will deploy it",
    "because Railway deploys every green push; the running app is identical",
    "afterwards.",
    "",
    "Gate: SQLite 1406 passed / 0 skipped; Postgres 1406 passed / 0 skipped;",
    "ruff check app tests clean. Both backends run in a venv built from",
    "requirements.txt + requirements-dev.txt, not the developer sandbox.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  NOTHING IN THE RUNNING APP. Documentation and tests only." -ForegroundColor Yellow
Write-Host "  Railway will deploy it - it deploys every green push - but the" -ForegroundColor Yellow
Write-Host "  app behaves identically afterwards." -ForegroundColor Yellow
Write-Host ""
Write-Host "  What changes is that four real defects are now WRITTEN DOWN and" -ForegroundColor Cyan
Write-Host "  PINNED BY TESTS:" -ForegroundColor Cyan
Write-Host "   1. What's moving reads a four-row fixture   <- your item 5" -ForegroundColor Cyan
Write-Host "   2. and is captioned with the price clock" -ForegroundColor Cyan
Write-Host "   3. the Allocation Agent's veto never runs" -ForegroundColor Cyan
Write-Host "   4. no agent reads your strategy to pick BUYS <- your item 2" -ForegroundColor Cyan
Write-Host ""
Write-Host "  READ docs/AGENTS.md BEFORE PUSHING. It is the deliverable." -ForegroundColor White
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
# Added by the hotfix that preceded this. Cheap, and it is the check that would
# have stopped py-vapid==1.9.4 from ever being written down.
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
    Invoke-Suite -Focus @("tests/test_the_agent_roster_is_what_actually_runs.py")
    Write-Host ""
    Write-Host "  Then run test-postgres. Both backends must report 0 skipped." -ForegroundColor Yellow
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

Step "6. Smoke - MEASURE the findings against the live app"
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-the-agent-map-is-measured.ps1 -Sha $sha" -ForegroundColor White
Write-Host ""
Write-Host "  This one does not test a fix - there is no fix in this commit." -ForegroundColor Yellow
Write-Host "  It OBSERVES the numbers docs/AGENTS.md asserts, on the running" -ForegroundColor Yellow
Write-Host "  app, so the document is measured rather than argued. If any of" -ForegroundColor Yellow
Write-Host "  them disagrees, the map is wrong and I want to know." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  WHAT TO DECIDE NEXT, from the map:" -ForegroundColor Cyan
Write-Host "  * Seven speakers vs five agents. The doc recommends RENAMING the" -ForegroundColor Cyan
Write-Host "    four engine speakers rather than promoting five engines to" -ForegroundColor Cyan
Write-Host "    agents. Your call - say which and it is a small PR either way." -ForegroundColor Cyan
Write-Host "  * Findings 1+2 are one PR: give What's moving a real provider and" -ForegroundColor Cyan
Write-Host "    stop captioning it with the price clock. That is your item 5." -ForegroundColor Cyan
Write-Host "  * Finding 4 is your item 2 and is the largest of the four." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows," -ForegroundColor DarkYellow
Write-Host "    forever - and is captioned with the price-refresh clock." -ForegroundColor DarkYellow
Write-Host "  * The Allocation Agent's veto is unreachable from the app." -ForegroundColor DarkYellow
Write-Host "  * No agent reads your strategy when choosing WHAT to buy." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED and CI can still go red on" -ForegroundColor DarkYellow
Write-Host "    it. SEND ME THE DIAGNOSTIC BLOCK when it next fires." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you" -ForegroundColor DarkYellow
Write-Host "    accepted." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
