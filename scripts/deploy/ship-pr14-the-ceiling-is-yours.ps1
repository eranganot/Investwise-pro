# SHIP PR 14 - the ceiling went stale and nothing noticed.
#
#   .\scripts\deploy\ship-pr14-the-ceiling-is-yours.ps1
#   .\scripts\deploy\ship-pr14-the-ceiling-is-yours.ps1 -SkipTests
#
# No migration. Read section 0.
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
    "app/services/target_solver.py",
    "app/services/solver_store.py",
    "app/services/recommendations.py",
    "app/static_app/index.html",
    "scripts/smoke/smoke-t5-solver-constraint.ps1",
    "scripts/smoke/smoke-t6b-core-search.ps1",
    "scripts/smoke/smoke-t6b-sleeve-count.ps1",
    "tests/test_the_ceiling_is_yours.py",
    "scripts/deploy/ship-pr14-the-ceiling-is-yours.ps1",
    "STATUS.md"
)

$Message = @(
    "The ceiling went stale, and every answer for a week was about the number",
    "",
    "index.html seeds the drawdown ceiling ONCE, from",
    "performance(range=MAX).max_drawdown_pct - 'the risk the book already",
    "carries' - and only when it is null. From then on it lives in the stored",
    "verdict, and PR 11's nightly job re-runs 'the LAST verdict's own",
    "parameters', so whatever was seeded propagates forever.",
    "",
    "On this book a 13.3% ceiling survived from an era when the history provider",
    "returned a short window. The same endpoint now measures 58.59% over 2,513",
    "sessions. Every answer the solver gave was 'no', and the reason was the",
    "number rather than the portfolio:",
    "",
    "  T2    DRAWDOWN_BOUND             the target needs more risk than 13.3%",
    "  T6b   NONE_ADMISSIBLE            all 15 candidate cores breach 13.3%",
    "  T6b2  BASELINE_BREACHES_CEILING  the two held sleeves breach 13.3%",
    "",
    "At 58.6% the same three searches return RANKED with a real ordering, the",
    "current holdings third of fifteen, and a converged count of 2 whose best",
    "rejected third added 0.10 against a 0.25 floor. Nothing about the book",
    "changed between those two runs.",
    "",
    "IT DOES NOT RE-SEED, and that is the design rather than a shortcut.",
    "Silently moving a risk constraint the user is supposed to own is worse than",
    "leaving it wrong - the rule PR 11 already states for the nightly job: 'a",
    "constraint strong enough to disable a button has to come from a number the",
    "user actually set.' So solve() MEASURES the mismatch and says it, and the",
    "number stays theirs to change.",
    "",
    "ceiling_check rides on EVERY exit path of solve(), not three of four - a",
    "field present on some outcomes is a field no reader can rely on. It carries",
    "the ceiling, what the book measures over the SAME window unchanged, the gap,",
    "and a sentence. It is stored on the verdict so a card can read it without",
    "paying for a solve, and as_constraint exposes it with an explicit known",
    "flag, because 'we did not check' and 'checked, nothing wrong' are different",
    "claims and a verdict solved before this deploy can only say the first.",
    "",
    "THE CARD NOW NAMES ONE CAUSE, IN PRECEDENCE ORDER. Its action has been",
    "rewritten three times in one day - PR 12 said 'change the core', PR 13",
    "measured that and said 'the sleeves', PR 14 measured THAT and says 'check",
    "your ceiling first' - so it stops being a branch each time and becomes one",
    "ordering stated once, in _ceiling_cause:",
    "",
    "  1. THE CEILING, when the book already breaches it unchanged. It BOUNDS",
    "     every finding underneath, so naming anything else first answers a",
    "     question the reader cannot act on yet.",
    "  2. THE SLEEVES, when T6b measured every candidate core and none clears.",
    "  3. THE CORE, the only case where 'change the core' is advice a",
    "     measurement supports.",
    "",
    "An UNCHECKED ceiling never seizes the headline: absence of a check is not",
    "evidence the ceiling is broken. The precedence is total - every combination",
    "lands on exactly one cause, asserted parametrically, because a card that can",
    "name two will name both.",
    "",
    "AND THE SMOKE SCRIPTS STOP RE-ASSERTING A STALE NUMBER. 13.3 was a DEFAULT",
    "in three of my own scripts, so every -Solve run fed it back into the stored",
    "verdict - my contribution to the problem. They now read the ceiling off the",
    "stored verdict and skip loudly when there is none.",
    "",
    "16 new tests. Suite green on SQLite (1216) and on Postgres 16 (1212), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no table, no column changes." -ForegroundColor Green
Write-Host ""
Write-Host "  Verdicts solved BEFORE this deploy carry no ceiling_check, and" -ForegroundColor DarkGray
Write-Host "  report known=false rather than pretending they were checked." -ForegroundColor DarkGray

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
& "$PSScriptRoot\..\smoke\smoke-t5-solver-constraint.ps1" -BaseUrl $BaseUrl -Sha $sha
$fail = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Open Plan, then the Target card, and press Solve. Under the" -ForegroundColor Cyan
Write-Host "    two input boxes you should now get a line comparing your" -ForegroundColor Cyan
Write-Host "    ceiling against what this book has actually measured." -ForegroundColor Cyan
Write-Host "  * Your ceiling reads 52.1% and the book measures under that with" -ForegroundColor Cyan
Write-Host "    your real rule-based sleeves, so expect the QUIET line, not the" -ForegroundColor Cyan
Write-Host "    red one. Set it to 13 and re-solve to see the red one." -ForegroundColor Cyan
Write-Host "  * The number is never changed for you. If it moves on its own," -ForegroundColor Cyan
Write-Host "    that is the bug." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * Two Accept buttons on the Target card still give different" -ForegroundColor DarkYellow
Write-Host "    answers (65% from T2, 80% from T6a) with nothing saying they" -ForegroundColor DarkYellow
Write-Host "    answer different questions. That is PR 14b." -ForegroundColor DarkYellow
Write-Host "  * The best split is pinned at 40/40, exactly your concentration" -ForegroundColor DarkYellow
Write-Host "    cap, and the card does not say the optimum is on a boundary." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
