# SHIP PR 16 - the forward numbers stop guessing.
#
#   .\scripts\deploy\ship-pr16-measured-assumptions.ps1
#   .\scripts\deploy\ship-pr16-measured-assumptions.ps1 -SkipTests
#
# No migration. No engine version bump. But YOUR HEALTH SCORE WILL MOVE -
# read section 0 before running this.
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
    "app/services/measured_assumptions.py",
    "app/core/config.py",
    "app/api/routes/plan.py",
    "app/api/routes/workflows.py",
    "app/services/portfolio_analytics.py",
    "app/static_app/index.html",
    "tests/test_the_numbers_come_from_measurement.py",
    "PR16_MEASURED_ASSUMPTIONS_PLAN.md",
    "scripts/deploy/ship-pr16-measured-assumptions.ps1",
    "scripts/smoke/smoke-measured-assumptions.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 16: four wrong screens, one static table behind all of them",
    "",
    "Eran reviewed the live app on 2026-08-29 and raised eight things. Four of",
    "them - the projection, the health score, the expected-return banner and the",
    "year-6 chart - are one defect.",
    "",
    "_portfolio_stats and compute_snapshot both derived the book's forward return",
    "and volatility from strategy_profile.assumptions_for(), a static per-ticker",
    "lookup that prices TQQQ and SOXL at 12%/yr and 55% volatility BUY-AND-HOLD.",
    "Grepping portfolio_analytics.py, strategy_profile.py and the plan routes for",
    "StrategyBacktest returns NOTHING: every measurement Phase T produced was",
    "invisible to the numbers on Today.",
    "",
    "He does not hold those tickers buy-and-hold. He holds them inside",
    "btm_swing_dip and btm_trend_soxl - rules whose whole purpose is to be OUT of",
    "the instrument some of the time.",
    "",
    "REPRODUCED AGAINST THE LIVE SCREENS BEFORE ANY CODE WAS WRITTEN:",
    "",
    "  Risk chip 15       <- weighted volatility 45.6% against a 25% cap",
    "  Health 69          <- 0.30*15 + 0.25*100 + 0.25*100 + 0.20*71 = 68.7",
    "  Projection 21,723  <- NAV 21,689 at 10.87%/yr and 46% vol over 6 years",
    "",
    "Every screen's arithmetic is right. One input is wrong and four inherit it.",
    "At 45.6% volatility the drag term sigma^2/2 is 10.4 points a year, which",
    "consumes the whole 10.87% expected return - which is why the median line is",
    "flat while the 95th percentile reaches 135k.",
    "",
    "1. THE TWO HALVES ARE NOT EQUALLY EVIDENCED, SO THEY ARE NOT TREATED ALIKE.",
    "",
    "VOLATILITY is measured wherever a measurement exists. It is the stable half",
    "of a backtest: a trend rule that sits in cash through drawdowns genuinely",
    "realises less volatility than the instrument it trades, and that is a",
    "property of the RULE, not of the decade.",
    "",
    "RETURN defaults to the OUT-OF-SAMPLE figure, not the full window. W1 walked",
    "every overlay forward on 2026-08-27 and NOT ONE reached FITTING_HELPS, so a",
    "full-sample CAGR for a 3x leveraged trend strategy over 2015-2025 is a",
    "bull-market extrapolation. robustness.out_of_sample already exists and the",
    "app already RANKS on it. Both readings travel together so the gap is",
    "visible, and ASSUMPTION_RETURN_BASIS moves it without a code edit - the same",
    "treatment W2 gave the gate threshold.",
    "",
    "2. GROUND OR ABSTAIN. Four named reasons a measurement is refused -",
    "NO_MEASUREMENT, MEASUREMENT_FAILED, ENGINE_VERSION_MOVED,",
    "TOO_FEW_OBSERVATIONS - and none of them is a silent zero. A refusal falls",
    "back to the instrument table LABELLED as the instrument table. A failed",
    "measurement is not a low number and is never used as one.",
    "",
    "The observation floor is 260, the fourth design that number has decided.",
    "",
    "3. WEIGHTED BY HELD SHARE, NEVER BY TARGET. This book's sleeves target 64%",
    "and hold far less; a forward number weighted by targets describes a book",
    "that does not exist. That distinction is the whole of PR 6.",
    "",
    "4. NOTHING MOVES SILENTLY. Both functions gained an OPTIONAL argument and",
    "eight call sites did not change; omitted, they produce byte-identical output",
    "and the tests assert it. Every payload carries the basis, the measured share",
    "of the book, and what the old number would have been. The banner and the",
    "Risk chip say which volatility produced them.",
    "",
    "28 new tests. Suite green on SQLite (1324) and on Postgres 16 (1320), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your numbers"
Write-Host "  No migration, no engine version bump, no stored row changes." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  BUT YOUR HEALTH SCORE WILL MOVE once your sleeves have a" -ForegroundColor Yellow
Write-Host "  measurement, because Risk is 30% of it and Risk is scored on the" -ForegroundColor Yellow
Write-Host "  book's volatility. That is the fix working, not a regression." -ForegroundColor Yellow
Write-Host ""
Write-Host "  nav_snapshots stores a daily health score, so the stored series" -ForegroundColor DarkGray
Write-Host "  will have a SEAM at this deploy. Recording the basis on the row" -ForegroundColor DarkGray
Write-Host "  needs a migration and is deliberately not smuggled in here." -ForegroundColor DarkGray

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
Write-Host "  * Plan, under the expected-return banner: a new line saying" -ForegroundColor Cyan
Write-Host "    whether that figure is measured or the instrument table. It has" -ForegroundColor Cyan
Write-Host "    never said either." -ForegroundColor Cyan
Write-Host "  * Today, under the health chips: a line naming the volatility" -ForegroundColor Cyan
Write-Host "    that produced your Risk score, against your cap." -ForegroundColor Cyan
Write-Host "  * If your sleeves have current backtests, the Risk chip should" -ForegroundColor Cyan
Write-Host "    rise well above 15 and the health score with it. If they do" -ForegroundColor Cyan
Write-Host "    not, the line will say WHY by name - that is the feature." -ForegroundColor Cyan
Write-Host "  * /api/v1/health-check now carries assumptions.abstained[] with a" -ForegroundColor Cyan
Write-Host "    reason per sleeve it could not measure." -ForegroundColor Cyan
Write-Host ""
Write-Host "  TO SEE THE OTHER RETURN BASIS, on Railway, then redeploy:" -ForegroundColor DarkYellow
Write-Host "    ASSUMPTION_RETURN_BASIS=measured_full" -ForegroundColor DarkYellow
Write-Host "  That uses the whole-window CAGR instead of the out-of-sample one." -ForegroundColor DarkYellow
Write-Host "  It will read HIGHER. W1 is the reason it is not the default." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The two contradicting cards you found (sell Equities / buy" -ForegroundColor DarkYellow
Write-Host "    SOXL) are untouched. contradictions.py rule 2 matches on title" -ForegroundColor DarkYellow
Write-Host "    text and a single asset_class field, and misses both legs." -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
