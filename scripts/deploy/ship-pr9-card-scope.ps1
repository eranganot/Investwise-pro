# SHIP PR 9 - one target for the whole book, a scope on every card, and no card
# that contradicts another or a sleeve.
#
#   .\scripts\deploy\ship-pr9-card-scope.ps1
#   .\scripts\deploy\ship-pr9-card-scope.ps1 -SkipTests
#
# No migration. Behaviour change on Today and /mix, so section 3 records what
# the live book is being told BEFORE the push and section 7 checks it after.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml. AND run the Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/book_target.py",
    "app/services/card_scope.py",
    "app/services/contradictions.py",
    "app/services/recommendations.py",
    "app/services/rules_service.py",
    "app/api/routes/plan.py",
    "app/static_app/index.html",
    "tests/test_cards_know_their_strategy.py",
    "tests/test_c6_core.py",
    "scripts/smoke/smoke-card-scope.ps1",
    "scripts/deploy/ship-pr9-card-scope.ps1",
    "STATUS.md"
)

$Message = @(
    "The app stops arguing with itself: one book target, scoped cards",
    "",
    "Three things Eran asked to check before the next PR. All three were real,",
    "and all three were ONE root cause with three faces: nothing forced a card to",
    "say which part of the book it was talking about, so cards were computed at",
    "the wrong scope and nothing could see it.",
    "",
    "Measured on the live book, not reasoned about:",
    "",
    "    NAV      21,205      sleeves  btm_swing_dip  38.4% -> TQQQ (held 38.6%)",
    "    holdings 97.0% Eq             btm_trend_soxl 25.6% -> SOXL (held 25.7%)",
    "              3.0% Cash  core     bal_all_weather, 36% of book, 35/40/25",
    "",
    "1. 'MAX WEIGHT 15% - TRIGGERED - NOW 69.01'. That 69.01 was the SHARE PRICE.",
    "The API was right all along (current: 38.6, unit: pct_of_portfolio). The",
    "rules screen has a unit-aware renderer built for exactly this bug - its own",
    "comment cites SOXL's $137.61 against a 10% cap - and the TRIGGERED branch was",
    "never converted. A weight cap read its own weight correctly right up until it",
    "fired, then started quoting dollars. The one branch that matters most was the",
    "one left behind. The target column read '15.00' too, indistinguishable from a",
    "price on a row sitting under a stop-loss reading '65.70'.",
    "",
    "2. THE REBALANCE CARD WAS OUT BY EXACTLY 2.78x.",
    "",
    "    mix,   _ = current_mix(rows)                            # WHOLE book",
    "    target   = await _core_target(session, user, objective) # the CORE's mix",
    "    report   = AllocationEngine().compute(target, mix, nav=nav)  # FULL nav",
    "",
    "The core governs 36% of the book. Its target was measured against 100% of it",
    "and sized off full NAV, so every leg came out overstated by 1/0.36:",
    "",
    "    the card said            correct",
    "    Sell Equities  12,139    4,324",
    "    Buy Fixed Inc   8,469    3,054",
    "    Buy Commodities 5,293    1,908",
    "",
    "AND CHOOSING A CORE MADE IT WORSE, which is the opposite of what choosing one",
    "should do. Before that the fallback was OBJ_TARGET[objective] - Grow, 80/10/10",
    "- which at least READS as a whole-book target, so the error looked like",
    "ordinary drift. Choosing All-Weather turned a 3,600 equity sale into a 12,139",
    "one, on a book whose sleeves are 64% equity BY DESIGN and cannot be sold",
    "without abandoning the strategies.",
    "",
    "book_target.py replaces it. The book's target is the two ADDED: the sleeves'",
    "per-ticker targets classified and summed, plus the core's mix scaled to the",
    "share the sleeves left. It sums to 1.0 BY CONSTRUCTION, because the core's",
    "share is derived as the remainder rather than read from a second place that",
    "could disagree. AllocationEngine.compute assumes a target covering 100% of",
    "NAV and never checks; _rebalance_to renormalises downstream, which HID the",
    "problem at exactly the point it would have been caught.",
    "",
    "_rebalance_to - the function ACCEPT runs - reads the same helper. Fixing only",
    "the card would have been worse than fixing neither: the screen would say",
    "'sell 4,324' and the button would sell 12,139.",
    "",
    "3. TWO TARGETS FOR ONE ASSET CLASS, IN ONE PAYLOAD. recommendations.py:2111",
    "read the objective's commodity target while the rebalance card three cards",
    "above read the core's: 'Buy Commodities ~5,293' (25%) beside 'Add a",
    "commodities sleeve - 2,120 of USO ... against your 10% target'. Both cards",
    "internally consistent, the pair nonsense. Every reader that answers WHAT",
    "SHOULD THE BOOK BE now uses book_target; the ones that are genuinely",
    "per-class BUDGETS keep OBJ_TARGET as a documented fallback.",
    "",
    "Also deleted: a private copy of OBJ_TARGET and a private _classify in",
    "api/routes/plan.py, both with zero readers and both already drifted - the",
    "local _classify had none of the commodity or bond ticker sets, so it called",
    "GLD, IAU and DBC Equities. A dead fork of a lookup table is the PR 8 failure",
    "one layer up.",
    "",
    "4. THE 15% CAP THAT FOUGHT A 38.4% SLEEVE. The loudest card on Today, HIGH,",
    "was 'TQQQ is 39% of your portfolio - Trim 24.231 TQQQ' against a sleeve",
    "TARGETING 38.4% and sitting on it.",
    "",
    "Traced to source: 'cap = max(15, 5*ceil(weight/5))' appears in TWO places -",
    "rules_service._suggestions_from and recommendations._momentum_recs - and both",
    "derive the level from the position's CURRENT weight, knowing nothing about",
    "sleeves. The cap was suggested while TQQQ was small, armed by an 'uptrend,",
    "protect your gains' card, and the sleeve grew to 38.4% afterwards. Every rule",
    "on the live book carries strategy_id: null, so nothing could re-level it.",
    "",
    "The bug only exists in the gap between arming a cap and resizing a sleeve -",
    "run the same formula at today's 38.6% and it gives 40, which is why it was",
    "invisible.",
    "",
    "Fixed at both sources: a suggested cap is floored ABOVE the sleeve target, not",
    "on it - a cap exactly at the target fires the moment the sleeve is correctly",
    "funded, which is the same bug with a friendlier number. Plus a HIGH card for",
    "the caps already armed, re-levelling in one tap via _arm_sleeve_caps, which",
    "already did the right thing and had simply never run against caps armed after",
    "the last apply. Behind a tap rather than on the read, because /recommendations",
    "is a GET and a GET that quietly writes makes 'does this book have a stale cap'",
    "depend on whether anyone opened the page.",
    "",
    "5. EVERY CARD NAMES THE STRATEGY IT SERVES. card_scope.py, three kinds:",
    "SLEEVE (the card is arguing with a strategy you chose), CORE (the part no",
    "sleeve claims), BOOK (goal, fees, cash floor - belongs to no strategy and says",
    "so rather than pretending to be scoped). Derived from what cards already",
    "carry and stamped once after reconciliation, so a merged card is scoped from",
    "what it ended up containing rather than from whichever leg was first.",
    "",
    "A card naming SOME sleeve tickers and some not is BOOK, deliberately: a",
    "tax-harvest card listing four losers of which one is sleeved acts on the book,",
    "and calling it a sleeve card would license it to argue with that sleeve's",
    "target.",
    "",
    "6. CARDS CAN NO LONGER CONTRADICT EACH OTHER SILENTLY. contradictions.py.",
    "_reconcile already existed and is about cards saying the SAME thing twice;",
    "this is about cards saying OPPOSITE things once each - every card",
    "individually defensible, the set incoherent. Four rules:",
    "",
    "  - A card may not sell a holding at or below its sleeve target. Suppressed,",
    "    and the stale rule behind it NAMED. A sleeve genuinely OVER target is",
    "    still trimmed - the guard is scoped to the hazard, not the feature.",
    "  - Buy and sell of one asset class ('Sell Equities 12,139' beside 'Buy 2,120",
    "    of VXUS'). Flagged on both, suppressed on neither - which to drop is the",
    "    user's call.",
    "  - Two targets for one class. Reported loudly, never suppressed: a bug in a",
    "    generator, not something to ask the user to arbitrate.",
    "  - Cards that together exceed the book (5,003 + 12,139 + 2,180 = 19,322 of",
    "    selling out of 20,567 held; each card funds itself and none knows the",
    "    others exist).",
    "",
    "Every suppression appends to a notes list on the surviving card and appears in",
    "contradictions[] on the payload. A card that vanishes silently is how eleven",
    "cards went missing in PR 4.",
    "",
    "VERIFIED END TO END on a book rebuilt to match his: /mix and Today now quote",
    "the same 76.6 / 14.4 / 9.0, every card carries a scope chip, and arming a 15%",
    "TQQQ cap raises the HIGH re-level card. Full suite green on SQLite and on a",
    "real Postgres 16, plus ruff on app and tests.",
    "",
    "NOT FIXED HERE, now in STATUS.md: rule notes freeze the price they were",
    "created at and present it as today's - 'Caps downside ~15% below today's",
    "77.30' when TQQQ is 69.01 and the stop is 4.8% away. The LEVELS are right and",
    "the progress bar computes distance live; it is the frozen prose that lies."
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record what Today is telling you BEFORE the change"
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
try {
    $preRecs = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $ApiHeaders -TimeoutSec 300
    Write-Host "  cards on Today right now:" -ForegroundColor DarkGray
    foreach ($cd in @($preRecs.recommendations)) {
        Write-Host "      [$($cd.severity)] $($cd.title)" -ForegroundColor DarkGray
        if ($cd.action) { Write-Host "          $($cd.action)" -ForegroundColor DarkGray }
    }
} catch {
    Warn "could not read Today first: $($_.Exception.GetType().Name)"
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Wait for Railway to actually serve it"
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

Step "7. Verify"
& "$PSScriptRoot\..\smoke\smoke-card-scope.ps1" -BaseUrl $BaseUrl -Sha $sha
$fail = $LASTEXITCODE

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Holdings > Rules: the TQQQ max-weight row should now read" -ForegroundColor Cyan
Write-Host "    'Triggered - 38.6% of book vs a 15% cap', not 'now 69.01'," -ForegroundColor Cyan
Write-Host "    and its target should read '15% of book', not '15.00'." -ForegroundColor Cyan
Write-Host "  * Today: every card carries a SLEEVE / CORE / BOOK chip." -ForegroundColor Cyan
Write-Host "  * Today: a HIGH card offering to re-level the TQQQ cap to your" -ForegroundColor Cyan
Write-Host "    38.4% sleeve. Accepting it buys and sells NOTHING - a cap is an" -ForegroundColor Cyan
Write-Host "    alert threshold, not an instruction." -ForegroundColor Cyan
Write-Host "  * Today: 'Rebalance toward your target mix' should now ask for" -ForegroundColor Cyan
Write-Host "    roughly 4,300 of equities, not 12,100, and should name where" -ForegroundColor Cyan
Write-Host "    the target came from: '77% (64% your sleeves + 13% your core)'." -ForegroundColor Cyan
Write-Host "  * Compare that against Plan > Mix. They must agree exactly." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE: rule NOTES still quote the price they" -ForegroundColor DarkYellow
Write-Host "  were written at - 'Caps downside ~15% below today's 77.30' when" -ForegroundColor DarkYellow
Write-Host "  TQQQ is 69.01. The LEVEL is right and the bar computes distance" -ForegroundColor DarkYellow
Write-Host "  live; only the frozen sentence is stale. Backlog in STATUS.md." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
