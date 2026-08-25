# SHIP PR 14b - two Accept buttons, two questions, and a winner that was clipped.
#
#   .\scripts\deploy\ship-pr14b-two-questions.ps1
#   .\scripts\deploy\ship-pr14b-two-questions.ps1 -SkipTests
#
# No migration.
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
    "app/services/split_solver.py",
    "app/services/target_solver.py",
    "app/static_app/index.html",
    "tests/test_two_answers_two_questions.py",
    "scripts/deploy/ship-pr14b-two-questions.ps1",
    "STATUS.md"
)

$Message = @(
    "Two Accept buttons, two questions - and a winner that was clipped, not chosen",
    "",
    "The Target card offers 'Set your sleeves to 65% of the book' and, directly",
    "below it, 'Best split found: 40% / 40%'. Both are green Accept buttons.",
    "Both are correct. They differ because they answer DIFFERENT questions:",
    "",
    "  T2   solve()        the SMALLEST sleeve share that REACHES a target you",
    "                      typed",
    "  T6a  solve_split()  the LARGEST excess your ceiling ALLOWS, no target",
    "",
    "T2's card already said 'Anything larger buys return you did not ask for at",
    "risk you did not agree to'. T6a's docstring already said 'No excess_pct",
    "parameter, deliberately'. Neither said it where a reader can see both",
    "numbers at once, which is the card-claim invariant broken ACROSS two cards",
    "instead of inside one. Both payloads now carry a question block naming what",
    "they asked and pointing at the other - and neither calls the other wrong,",
    "because they are different questions and not a right and a wrong answer.",
    "",
    "AND THE SPLIT WINNER WAS CLIPPED, NOT OPTIMISED.",
    "",
    "Every one of the top eight splits on this book had 40 in the first column.",
    "Each sleeve holds a single ticker and the concentration cap is 0.40, so a",
    "40% sleeve IS that ticker at exactly the cap. The search did not find an",
    "interior optimum; it ran into a wall and stopped. coverage.method already",
    "said 'the best point REACHED, not a proven global optimum' - true, and it",
    "still reads as 'optimised' to anyone not hunting for the distinction.",
    "",
    "split_solver now probes the winner's own neighbourhood and reports whether",
    "the optimum sits on the edge of the admissible region. GENERICALLY: that",
    "module is handed a measure callable and knows nothing about caps or",
    "ceilings, and a module that guessed would be a second opinion about a rule",
    "it cannot see. Naming WHICH constraint binds is the caller's job, because",
    "only the caller knows what it enforced - so target_solver reads the",
    "winner's full-window peaks and names concentration_cap, cash_floor or",
    "drawdown_ceiling.",
    "",
    "Measured on the real book: winner [40.0, 40.0], 4 of 6 neighbours",
    "inadmissible, binding constraint concentration_cap on QQQ - the dip-buy",
    "rule's BASE holding, not its leveraged leg. Worth seeing: the cap is biting",
    "on what the rule sits in when it is OUT, which is not where anyone would",
    "have looked.",
    "",
    "The interior case is asserted too. A block that can only ever say one thing",
    "is not a measurement.",
    "",
    "ALSO, from PR 14 in production: the ceiling line now says 'Solved against a",
    "58.6% ceiling' rather than 'Your ceiling is'. The input is uncontrolled, so",
    "a typed value does not reach TGT.ceiling until Solve reads it - reported",
    "live with the box showing 43.1 and the line showing 58.6, where the line",
    "was right about the solve it described. Naming the solve removes the",
    "contradiction without pretending to know what is in the box.",
    "",
    "11 new tests. Suite green on SQLite (1227) and on Postgres 16 (1223), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no table, no stored verdict." -ForegroundColor Green

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
Write-Host "  * Solve, then Find the best split. Each block now says which" -ForegroundColor Cyan
Write-Host "    question it answered, and each points at the other." -ForegroundColor Cyan
Write-Host "  * Under BEST SPLIT FOUND expect an amber line saying the winner" -ForegroundColor Cyan
Write-Host "    sits on a boundary, naming QQQ at your 40% cap. That is why" -ForegroundColor Cyan
Write-Host "    every top row reads 40 in the first column." -ForegroundColor Cyan
Write-Host "  * The ceiling line now reads 'Solved against a NN% ceiling', so" -ForegroundColor Cyan
Write-Host "    it no longer looks like it disagrees with the input box." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Both buttons still ACT. Nothing here stops you accepting one" -ForegroundColor DarkYellow
Write-Host "    then the other; they are now labelled, not gated." -ForegroundColor DarkYellow
Write-Host "  * The intermittent invested_ils balloon from CI #169 is still" -ForegroundColor DarkYellow
Write-Host "    unexplained. 926c4e3 makes the next occurrence name the row." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
