# SHIP PR 15 / W1 - walk-forward parameter sweeps, and the answer is mostly "no".
#
#   .\scripts\deploy\ship-pr15-w1-walk-forward.ps1
#   .\scripts\deploy\ship-pr15-w1-walk-forward.ps1 -SkipTests
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
    "app/services/walk_forward.py",
    "app/services/backtest_service.py",
    "app/api/routes/plan.py",
    "tests/test_does_fitting_beat_not_fitting.py",
    "scripts/smoke/smoke-w1-walk-forward.ps1",
    "scripts/deploy/ship-pr15-w1-walk-forward.ps1",
    "PHASE_W_PLAN.md",
    "STATUS.md"
)

$Message = @(
    "W1: walk-forward parameter sweeps, and not one strategy earns a win",
    "",
    "BEAT_MARKET_TARGET_SOLVER_PLAN.md:936 calls these 'the only untested source",
    "of additional measured edge in the family, and a bigger lever on excess than",
    "any sleeve size'. Measured across all six sweepable strategies, none of them",
    "reaches FITTING_HELPS:",
    "",
    "  btm_trend_tqqq       INCONCLUSIVE           3/6  mean  +8.49",
    "  btm_trend_soxl       FITTING_DOES_NOT_HELP  1/6  mean -21.58",
    "  btm_vol_target_tqqq  INCONCLUSIVE           4/6  mean  +3.23",
    "  btm_swing_dip        FITTING_DOES_NOT_HELP  3/6  mean  -0.11",
    "  btm_swing_breakout   FITTING_DOES_NOT_HELP  3/6  mean  -6.62",
    "  btm_factor_stack     NO_SWEEP               -- buy_hold, no parameter",
    "  btm_dual_momentum    INCONCLUSIVE           4/6  mean  +5.35",
    "",
    "THREE OF THOSE WOULD HAVE BEEN WINS ON THE MEAN ALONE. btm_trend_tqqq",
    "averages +8.49 while winning three folds of six - one or two folds carrying",
    "the whole figure. That is why the bar has two halves, and why both are",
    "module constants set BEFORE any number was measured: with six folds it is",
    "trivial to decide afterwards what counts as a win, and that is how a sweep",
    "manufactures an edge.",
    "",
    "THE QUESTION. Not 'which parameter value wins' - that always has an answer,",
    "the answer is always the in-sample winner, and reporting it is the whole",
    "failure mode. The question is whether fitting the parameter beats NOT",
    "fitting it, so every fold measures the value fitted on the data available at",
    "the time against the value ALREADY IN THE CATALOG: the counterfactual that",
    "actually existed had nobody swept. The baseline is the default rather than",
    "the grid's median because the median is a value nobody was running.",
    "",
    "TWO CORRECTIONS THE CODE MADE TO THE SPEC.",
    "",
    "The sweeps exist on 6 of 7 catalog entries, not 'every overlay'.",
    "btm_factor_stack is buy_hold and correctly has no parameter - it abstains",
    "NO_SWEEP by name, because a strategy that silently vanished from a report",
    "would read as one that was measured and found wanting.",
    "",
    "And they ARE run: nightly, on the full sample, by backtest_service.measure,",
    "stored in StrategyBacktest.robustness - and read by NOTHING. Grepping every",
    "renderer, route and script, robustness.sweep has zero consumers. The compute",
    "was already being paid and discarded. What was missing is the split and the",
    "selection, which is what 'never run walk-forward' actually means.",
    "",
    "GEOMETRY IN SESSIONS, NOT YEARS, and this is the second time that floor has",
    "decided a design. strategy_backtest._MIN_OBSERVATIONS is 260 with the",
    "comment 'below this, CAGR is noise', and run() abstains under it. A calendar",
    "year is ~252 sessions, so the 3y/1y geometry recommended before the floor",
    "was found would have abstained on every fold. Rolling window, 756-session",
    "fit, 260-session test, 260-session step: six folds on 2,513 sessions.",
    "",
    "THE FIT WINDOW IS FIXED LENGTH on purpose. That is the whole reason to",
    "prefer a rolling window over an expanding one: it makes the folds comparable",
    "to each other, so 'the parameter is stable' becomes a claim the data can",
    "support. Under an expanding window the first fold chooses on 260 sessions",
    "and the last on 2,080, and a value that changes between them says nothing",
    "about the parameter - only about how much data produced each choice.",
    "",
    "It costs the last 197 sessions, which are in no test window. 2,513 does not",
    "divide into whole folds and the remainder sits at the RECENT end, so the",
    "months a reader cares most about are the untested ones. Extending the final",
    "fold would make it shorter than the rest and break the comparability the",
    "fixed length exists for, so the remainder is left out and NAMED.",
    "",
    "STABILITY IS REPORTED, because a parameter that jumps every fold has been",
    "resampled rather than measured - and a report giving only the mean",
    "difference would hide that completely. On this catalog the modal value is",
    "chosen in 3 or 4 folds out of 6 everywhere. Nothing is stable.",
    "",
    "NOTHING IS WRITTEN BACK. backtest_service already sets the rule that a",
    "measured-better variant ships OFF until a human compares the numbers, and a",
    "route that tuned a live strategy from one history would be precisely the",
    "overfitting this module exists to detect. The trial spec is built per",
    "measurement and thrown away; a test asserts nothing assigns into the",
    "caller's overlay.",
    "",
    "26 new tests. Suite green on SQLite (1253) and on Postgres 16 (1249), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no table, no stored result." -ForegroundColor Green
Write-Host ""
Write-Host "  One new read-only route: GET /api/v1/plan/walk-forward" -ForegroundColor DarkGray

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
& "$PSScriptRoot\..\smoke\smoke-w1-walk-forward.ps1" -BaseUrl $BaseUrl -Sha $sha -All
$fail = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * The smoke swept all seven. Expect NO strategy to reach" -ForegroundColor Cyan
Write-Host "    FITTING_HELPS, and btm_factor_stack to abstain NO_SWEEP." -ForegroundColor Cyan
Write-Host "  * That is the finding, not a failed search. The spec expected" -ForegroundColor Cyan
Write-Host "    these sweeps to be the biggest lever in the family." -ForegroundColor Cyan
Write-Host "  * Nothing changed any of your strategy parameters, and there is" -ForegroundColor Cyan
Write-Host "    no button that can. If a parameter moves, that is the bug." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * No UI. The result is API-only:" -ForegroundColor DarkYellow
Write-Host "      /api/v1/plan/walk-forward?strategy_id=btm_swing_dip" -ForegroundColor DarkYellow
Write-Host "  * Not wired into the nightly job. The plan says that is the right" -ForegroundColor DarkYellow
Write-Host "    home, but nothing reads the result yet, and a stored answer with" -ForegroundColor DarkYellow
Write-Host "    no reader is what robustness.sweep already was." -ForegroundColor DarkYellow
Write-Host "  * W2 (the vol_target gate) and W3 (contributions) are unstarted." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
