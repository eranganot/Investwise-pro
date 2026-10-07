# SHIP PR 15c / W3 - the projection was blind to your deposits.
#
#   .\scripts\deploy\ship-pr15c-w3-contributions.ps1
#   .\scripts\deploy\ship-pr15c-w3-contributions.ps1 -SkipTests
#
# No migration, no engine version bump, no stored row changes meaning.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/engines/simulation_engine.py",
    "app/schemas/simulation.py",
    "app/services/intake_service.py",
    "app/api/routes/plan.py",
    "app/static_app/index.html",
    "tests/test_the_projection_sees_your_deposits.py",
    "scripts/deploy/ship-pr15c-w3-contributions.ps1",
    "scripts/smoke/smoke-w3-contributions.ps1",
    "STATUS.md"
)

$Message = @(
    "W3: the projection could tell you what deposit closes the gap, not show it",
    "",
    "SimulationEngine.run() was a single multiplicative draw on starting NAV:",
    "",
    "  gbm = exp((mu - 0.5*sigma^2)*T + sigma*sqrt(T)*z)",
    "  nominal = initial_value * gbm * fx_factor",
    "",
    "There is no time axis in that expression, so there was nowhere to put a",
    "deposit. The engine's assumptions list said so - 'no rebalancing or cash",
    "flows during the horizon' - and plan.py built its own user-facing string",
    "that dropped the line. The card showed a no-contribution projection without",
    "ever saying it was one.",
    "",
    "The asymmetry that made this worth fixing: recommendations.py already",
    "solves the annuity BACKWARDS - 'add about X/month to close the gap' - and",
    "takes this projection's shortfall as its input. So the app could tell you",
    "what deposit closes a gap but could not show you the future in which you",
    "make it.",
    "",
    "1. THE ZERO-CONTRIBUTION PATH IS THE OLD CODE, IN A BRANCH. Not",
    "'equivalent in distribution' - the same expression, producing bit-identical",
    "numbers, with the pre-W3 figures pinned in the test file as literals.",
    "",
    "That is not caution for its own sake. Stepping and then multiplying is",
    "distributionally equal to the single shot only under NORMAL shocks; under",
    "risk_distribution='t' it is not, because a sum of Student-t draws is not",
    "Student-t. A 'harmless' restructure would have quietly moved every",
    "projection of every user on the t setting, and moved everyone else's by a",
    "little through resampling. A branch is a cheap price for not having that.",
    "",
    "2. DEPOSITS LAND AT PERIOD END, MATCHING THE CARD THAT ASKS FOR THEM.",
    "Lever A solves pmt = gap*rm/(((1+rm)^n)-1), the future value of an ORDINARY",
    "annuity. Depositing at period start would have the card tell you to add X",
    "and the chart show a future built on more than X. Tested against the closed",
    "form at zero volatility, and against Lever A's own arithmetic.",
    "",
    "Per-step deposit is rate*dt rather than rate/steps_per_year, so the total",
    "deposited is rate*T exactly however the step count rounds. FX drift is",
    "folded into the per-step growth: a deposit made in year 9 of 10 has been",
    "exposed to one year of currency drift, not ten.",
    "",
    "3. THE RATE IS MEASURED OR IT IS ABSENT. NEVER INVENTED.",
    "intake_service.contribution_rate reports four distinct reasons it cannot",
    "measure one - NO_LEDGER, SINGLE_ENTRY, TOO_SHORT, NOT_POSITIVE - and none",
    "of them is a silent zero. total_contributed() has always refused to conflate",
    "'never logged a deposit' (None) with 'deposited nothing' (0.0) and that",
    "distinction survives intact: an empty ledger returns None, a ledger that",
    "nets to zero returns 0.0, and they are different reasons on the card.",
    "",
    "The span is first-entry-to-NOW, not to the last entry: 'you have averaged X",
    "since you started' decays when you stop, where first-to-last would report a",
    "stale rate forever. Under 180 days it refuses - three weeks of ledger turns",
    "a 20,000 deposit into 350,000/yr, which is arithmetic, not measurement.",
    "",
    "TWO READINGS ARE RETURNED AND NEITHER IS CHOSEN SILENTLY. per_year divides",
    "the whole ledger by the span, as the plan specified. But the first row is",
    "almost always opening capital rather than a contribution, so",
    "per_year_excluding_first is usually the honest forward-looking figure and is",
    "always the smaller. The payload names the ambiguity instead of resolving it.",
    "",
    "4. THE RIVAL LEVER IS ON THE SAME CARD. Phase W's claim is that at this",
    "book's NAV the contribution rate moves terminal value more than any",
    "admissible excess does. A claim like that is either next to its rival or it",
    "is just a sentence, so the endpoint runs a third simulation at +1pp of",
    "annual return and moves_more names the winner - and can say 'excess'.",
    "",
    "On this book (NAV 21,542, 8% expected, 18% vol, 10 years) the claim holds",
    "by roughly two orders of magnitude:",
    "",
    "  base median            40,463",
    "  +1pp of return         44,719   (+4,256)",
    "  1,000/month deposits  211,867   (+171,404)",
    "",
    "The deposit that MATCHES a full extra point of annual return is 307/yr -",
    "about 26 a month. Every search in Phase T has been arguing over the 4,256.",
    "",
    "26 new tests. Suite green on SQLite (1296) and on Postgres 16 (1292), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no engine version bump, no stored row" -ForegroundColor DarkGray
Write-Host "  changes meaning. The projection endpoint gains two blocks and" -ForegroundColor DarkGray
Write-Host "  an optional query parameter; every existing key still describes" -ForegroundColor DarkGray
Write-Host "  the same no-deposit run it always did." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

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

Step "6. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today, 'Where this could end up': a new grey line reading" -ForegroundColor Cyan
Write-Host "    'Every figure above assumes you never add another shekel'." -ForegroundColor Cyan
Write-Host "    That was always true and the card never said it." -ForegroundColor Cyan
Write-Host "  * Explore, under the projection chart: the assumptions now" -ForegroundColor Cyan
Write-Host "    include the engine's own 'no rebalancing or cash flows'." -ForegroundColor Cyan
Write-Host "  * Your ledger has ONE row, so no rate can be measured and the" -ForegroundColor Cyan
Write-Host "    card will say SINGLE_ENTRY - 'one entry is an opening balance'." -ForegroundColor Cyan
Write-Host "    That is correct behaviour, not a failure. To see the second" -ForegroundColor Cyan
Write-Host "    line without touching the ledger, open:" -ForegroundColor Cyan
Write-Host "      /api/v1/plan/projection?contribution_per_year=12000" -ForegroundColor Cyan
Write-Host "  * In that response, excess_comparison.moves_more should read" -ForegroundColor Cyan
Write-Host "    'contributions', with the +1pp run beside it for comparison." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The chart still draws only the no-deposit line. The" -ForegroundColor DarkYellow
Write-Host "    with-deposits figures are in the caption, not a second curve -" -ForegroundColor DarkYellow
Write-Host "    drawProjection interpolates geometrically between now and the" -ForegroundColor DarkYellow
Write-Host "    terminal value, which is wrong for a path that has money added" -ForegroundColor DarkYellow
Write-Host "    to it, and a wrong curve is worse than an honest number." -ForegroundColor DarkYellow
Write-Host "  * There is no UI to set a contribution rate. The query parameter" -ForegroundColor DarkYellow
Write-Host "    is the whole of it. Logging real deposits in the contributions" -ForegroundColor DarkYellow
Write-Host "    ledger is the intended path and makes the rate measured." -ForegroundColor DarkYellow
Write-Host "  * Lever A ('add a monthly contribution') still computes its own" -ForegroundColor DarkYellow
Write-Host "    answer and does not read this. The two now agree about timing" -ForegroundColor DarkYellow
Write-Host "    but they are still two implementations." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
