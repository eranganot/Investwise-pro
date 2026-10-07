# SHIP PR 13b / T6b second half - discover the sleeve count, don't pick a number.
#
#   .\scripts\deploy\ship-pr13b-sleeve-count.ps1
#   .\scripts\deploy\ship-pr13b-sleeve-count.ps1 -SkipTests
#
# No migration, no new table, no stored verdict. Read section 0.
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
    "app/services/sleeve_count.py",
    "app/services/target_solver.py",
    "app/api/routes/plan.py",
    "tests/test_how_many_sleeves.py",
    "scripts/smoke/smoke-t6b-sleeve-count.ps1",
    "scripts/deploy/ship-pr13b-sleeve-count.ps1",
    "STATUS.md"
)

$Message = @(
    "T6b second half: discover the sleeve count, and say how the search ended",
    "",
    "BEAT_MARKET_TARGET_SOLVER_PLAN.md - 'Discover the sleeve count, don't cap",
    "it. Grow the number of concurrent sleeves while each addition improves the",
    "constrained objective; stop when it stops, and report where it stopped and",
    "by how much the last one helped. The count is then a measurement rather",
    "than a number someone picked. Keep a hard runtime ceiling as a backstop,",
    "and log it if the backstop is what stopped the search - a limit that binds",
    "silently reads as a finding.'",
    "",
    "T2 resizes a FIXED set. T6a sizes each member of that fixed set on its own",
    "axis. Neither can answer 'should there be a third one at all', because both",
    "take the membership as given - and this book runs two sleeves for no",
    "measured reason.",
    "",
    "THE HONESTY IS IN THE REPORT, NOT THE ALGORITHM. Greedy forward selection",
    "is four lines. What makes the answer usable is that 'it stopped at two' can",
    "be CHECKED rather than trusted: every round records the candidates it tried",
    "and what each would have added, and the round that stopped the search",
    "records the addition it rejected and by how much it missed. 'It stopped at",
    "two' is not a finding; 'the best third added 0.11% against a 0.25% noise",
    "floor, and here is what each candidate third would have added' is.",
    "",
    "THE STOP REASON IS A FIRST-CLASS FIELD, because the ways this search can",
    "end mean completely different things and a payload reporting only the count",
    "renders them identically:",
    "",
    "  BELOW_NOISE_FLOOR         converged - the finding",
    "  NO_ADMISSIBLE_ADDITION    the held set is fine, nothing improves it",
    "  BASELINE_BREACHES_CEILING the held set already breaches - see below",
    "  MAX_SLEEVES               the pool ran out, not the improvement",
    "  RUNTIME_BACKSTOP          a budget stopped it; the count is a FLOOR",
    "",
    "'converged' and 'count_is_a_floor' are separate booleans for that reason.",
    "",
    "BASELINE_BREACHES_CEILING WAS FOUND BY RUNNING THIS AGAINST THE REAL BOOK.",
    "Every candidate third sleeve breached, so the search returned",
    "NO_ADMISSIBLE_ADDITION with count=2 - technically true and badly",
    "misleading, because a reader sees 2 and concludes two is right. The",
    "measured truth is that the two already held measure 44.6% against a 13.3%",
    "ceiling, which makes 'which third improves it' the wrong question rather",
    "than one with no answer. It is now its own outcome, checked BEFORE the",
    "first round (every gain against an inadmissible baseline is None, so the",
    "rounds would measure the whole pool to learn nothing), and its verdict says",
    "in words that the count is NOT a recommendation.",
    "",
    "THE COUNT IS NEVER CONFOUNDED WITH THE SIZE. Every candidate set is",
    "measured at an EQUAL split of the same total sleeve share. A three-sleeve",
    "set that wins because the search also handed it more of the book has not",
    "shown that three beats two. Sizing is the T6a search; this counts.",
    "",
    "CONSTRAINED EXCESS, not raw. Ranking additions on return alone selects for",
    "exactly the sleeves that make a book unholdable - every sleeve in this",
    "family is leveraged or concentrated, so raw excess is maximised by whichever",
    "addition adds the most leverage. Same argument target_solver's own docstring",
    "makes about the ceiling constraining the ADMISSIBLE SET.",
    "",
    "IT DOES NOT PRETEND TO BE EXHAUSTIVE. Greedy is not optimal: the best pair",
    "is not necessarily the best single plus one, so a set this search rejects",
    "may beat the set it keeps. method.caveat says so on the payload rather",
    "than letting the word 'discovered' imply otherwise - the same lie of",
    "omission T2's docstring warns about for its one-axis answer. The",
    "alternative, all 127 subsets of seven candidates, is not a better answer so",
    "much as the same answer arrived at expensively.",
    "",
    "MEASURED ON THE LIVE BOOK, both paths:",
    "",
    "  ceiling 13.3, from the two held  -> BASELINE_BREACHES_CEILING, 1 measure",
    "     'The 2 sleeve(s) you already run measure 44.6% against your 13.3%",
    "      ceiling'",
    "  ceiling 45.0, from zero          -> BELOW_NOISE_FLOOR, count 3",
    "     +1.80 swing_breakout, +4.30 trend_tqqq, +5.14 trend_soxl,",
    "     4th rejected at -0.82",
    "",
    "Read-only, no apply path, no stored verdict - unlike the core search this",
    "is cheap enough to be a button and nothing on Today depends on it yet.",
    "",
    "24 new tests. Suite green on SQLite and on Postgres, plus ruff on app and",
    "tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no table, no stored verdict." -ForegroundColor Green
Write-Host ""
Write-Host "  One new read-only route: GET /api/v1/plan/target/sleeve-count" -ForegroundColor DarkGray

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

Step "6. Verify"
& "$PSScriptRoot\..\smoke\smoke-t6b-sleeve-count.ps1" -BaseUrl $BaseUrl -Sha $sha -Search
$fail = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * At your 13.3% ceiling the search returns" -ForegroundColor Cyan
Write-Host "    BASELINE_BREACHES_CEILING after ONE measurement, and says the" -ForegroundColor Cyan
Write-Host "    count is not a recommendation. That is the correct answer, not" -ForegroundColor Cyan
Write-Host "    a failure to search." -ForegroundColor Cyan
Write-Host "  * Try a ceiling your book can actually meet to see it search:" -ForegroundColor Cyan
Write-Host "      /api/v1/plan/target/sleeve-count?max_drawdown_pct=45" -ForegroundColor Cyan
Write-Host "    It should pick 3 and show you the 4th it rejected." -ForegroundColor Cyan
Write-Host "  * Nothing on Today changes. This route has no card yet." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The count and the SIZE are answered by two separate searches" -ForegroundColor DarkYellow
Write-Host "    that do not talk to each other. Running T6a on the discovered" -ForegroundColor DarkYellow
Write-Host "    set is the obvious next step and is not in this PR." -ForegroundColor DarkYellow
Write-Host "  * Greedy, not exhaustive. The payload says so; I am repeating it" -ForegroundColor DarkYellow
Write-Host "    because a ranked count is easy to over-read." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
