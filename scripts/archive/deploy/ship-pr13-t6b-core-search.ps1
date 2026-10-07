# SHIP PR 13 / T6b - the solver varies the core, and the answer is "none of them".
#
#   .\scripts\deploy\ship-pr13-t6b-core-search.ps1
#   .\scripts\deploy\ship-pr13-t6b-core-search.ps1 -SkipTests
#
# No migration. T6b rows go into the EXISTING solver_verdicts table, tagged
# solver_version = "t6b". Read section 0.
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
    "app/services/core_search.py",
    "app/services/strategies.py",
    "app/services/target_solver.py",
    "app/services/backtest_service.py",
    "app/services/solver_store.py",
    "app/services/recommendations.py",
    "app/api/routes/plan.py",
    "tests/test_the_solver_varies_the_core.py",
    "tests/test_the_card_says_how_bad_it_is.py",
    "scripts/smoke/smoke-t6b-core-search.ps1",
    "scripts/deploy/ship-pr13-t6b-core-search.ps1",
    "STATUS.md"
)

$Message = @(
    "T6b: the solver varies the core, and the answer for this book is none of them",
    "",
    "BEAT_MARKET_TARGET_SOLVER_PLAN.md:686 - 'Vary the core: each static family",
    "in services/strategies.CATALOG as the core component, plus current holdings",
    "as the baseline core.'",
    "",
    "solve() sweeps ONE axis and holds the core fixed at whatever the book",
    "happens to hold. _components() already treats it as an ordinary component",
    "and measure_blend never special-cased __core__, so varying it is a change to",
    "the SEARCH, not to the simulator.",
    "",
    "WHAT IT MEASURED, AND WHY THAT IS THE HEADLINE.",
    "",
    "All fifteen candidates - the fourteen catalog families plus current",
    "holdings - came back INADMISSIBLE against the 13.3% ceiling. The shallowest",
    "drawdown on offer is 31.9%. With 64% of the book in TQQQ and SOXL the",
    "sleeves dominate, and no choice of core repairs a ceiling they have already",
    "broken.",
    "",
    "So the payload leads with a verdict, not a table. A ranked list of fifteen",
    "rows and no verdict reads as a menu whose top row is a fix. It is not one,",
    "and NONE_ADMISSIBLE says so in words the card renders directly.",
    "",
    "AND IT CONTRADICTS THE CARD PR 12 SHIPPED TWO HOURS EARLIER. That card's",
    "action was 'Change the strategy managing your core'. The measurement",
    "refutes it: the breach is real, the remedy was wrong. The card now reads the",
    "stored core search and drops that advice when the search has refuted it -",
    "keeping the original wording when no search has run, because that is the",
    "honest thing to say when nothing has measured the alternatives. Two cards",
    "disagreeing about one book is the defect PR 9 existed to remove; a single",
    "card giving advice its own measurement refuted is that defect from inside.",
    "",
    "THE WINDOW. Ranked on the recent window, which is Eran's decision of",
    "2026-08-25 taken against the spec's recommendation and with both sets of",
    "drawdown figures in front of him. The spec's table says the short window has",
    "'no bear market'; measured that day it is false - SOXL fell 69.42% and TQQQ",
    "37.17% inside it against SPY's 9.13%, so for a book whose sleeves ARE those",
    "two the recent window prices the binding risk.",
    "",
    "But BND measured -4.02% short against -23.96% long, understating the 2022",
    "rate shock roughly six-fold for the four bond-heavy families a drawdown",
    "ceiling selects for. So BOTH windows are measured for EVERY candidate, not",
    "for the winner, and any row whose windows disagree is flagged in the payload",
    "rather than left for a renderer to derive.",
    "",
    "260 SESSIONS, NOT THE 250 THE DECISION WAS WORDED IN, and the difference is",
    "not mine to choose: strategy_backtest._MIN_OBSERVATIONS is 260 with the",
    "comment '~1 trading year; below this, CAGR is noise', and measure_blend",
    "abstains under it. A 250-session request returned INSUFFICIENT_HISTORY for",
    "all fifteen. Worth stating rather than quietly rounding up: the ranking",
    "window is the shortest window this engine will measure at all.",
    "",
    "TWO BUGS FOUND BY RUNNING IT.",
    "",
    "1. _fetch called a rate limit a delisting. First live run: 18 of 28 tickers",
    "came back 'no history' - SPY, VTI, QQQ, MSFT among them - purely from rate",
    "limiting, and every candidate was excluded as unmeasurable. 'backtest: no",
    "history for SPY' is false about SPY and true about nothing. It is the same",
    "defect PR 10 removed from the screener and PR 5b introduced there, and it",
    "stopped being a corner case the moment something fetched 26 tickers at once.",
    "_fetch now retries with backoff and, with classify=True, separates",
    "unavailable from no_data; the legacy two-tuple is unchanged for callers that",
    "cannot yet tell them apart.",
    "",
    "2. My first concentration check compared each basket's INTERNAL weights",
    "against the cap and excluded 8 of 14 families - including Classic 60/40",
    "(VTI is 60% OF THE BASKET) and Capital Preservation (BIL 60%). A core that",
    "is 36% of NAV holding VTI at 60% of itself puts 21.6% of the BOOK in VTI,",
    "nowhere near a 40% cap. It now reads peak_weight_pct_by_ticker off the",
    "measurement rather than recomputing it - blend.py's own comment says why",
    "that is the authoritative copy. The baseline stays exempt: its concentration",
    "is a fact about the book the reader already owns, not a property of this",
    "decision, which is the distinction solve() already draws in its own words.",
    "",
    "PER-BLEND DATE AXES. JEPI listed 2020-05. One shared price feed would have",
    "silently truncated all fourteen candidates to inc_high_yield's six years and",
    "reported ten-year figures for none of them, because align() intersects",
    "across everything in the feed. Each blend gets its own axis, and each row",
    "reports its own window - 260/1573 for that one, 260/2513 for the rest.",
    "",
    "THE FIREWALL (investing-discipline SS5). Candidates come from",
    "strategies.CATALOG and nowhere else; the moment the search could pick a",
    "ticker it would be recommending a product. /plan/target has /apply and",
    "/undo; /plan/target/cores deliberately has neither, and returns no",
    "execution_plan at all. The disclaimer ships with the payload rather than",
    "being left to a renderer that may not know the rule exists.",
    "",
    "STORED, NOT RECOMPUTED. One search is ~54s against live data, so it cannot",
    "run on card load. The 04:10 job now runs it against the SAME ceiling the",
    "target solve just used - two answers read side by side on one card must not",
    "be about two different ceilings - and the card reads the stored result. Same",
    "write/read split as PR 11, for the same reason. Its own try: a core search",
    "that fails must not undo a target solve that succeeded.",
    "",
    "latest() now excludes t6b rows. It filtered on nothing before, so a core row",
    "would have been handed to the war room as a target-solver constraint - and",
    "NONE_ADMISSIBLE is not in BLOCKING_OUTCOMES, so the constraint would have",
    "quietly stopped blocking.",
    "",
    "AND run_solver_refresh_blocking loses its swallow, the same correction PR 12",
    "made to pricing_service. Returning {'solved': 0, 'skipped': 0, 'failed': 0}",
    "from a crash is the shape of a run that found no book with a stored question",
    "- the common healthy case - so a broken nightly re-solve would have read as",
    "a quiet night forever. I wrote that in PR 11 with the older bug in front of",
    "me.",
    "",
    "51 new tests. Suite green on SQLite (1176) and on Postgres (1172), plus ruff",
    "on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  No migration. No new table." -ForegroundColor Green
Write-Host ""
Write-Host "  T6b rows go into the EXISTING solver_verdicts table with" -ForegroundColor DarkGray
Write-Host "  solver_version = 't6b'. latest() now excludes them so the war" -ForegroundColor DarkGray
Write-Host "  room cannot mistake a core search for a target-solver constraint." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record what the card says BEFORE the change"
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
try {
    $pre = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $ApiHeaders -TimeoutSec 600
    $cd = @(@($pre.recommendations) | Where-Object { $_.meta.source -eq 'target_solver' })[0]
    if ($null -ne $cd) {
        Write-Host "  card action now: $($cd.action)" -ForegroundColor DarkGray
    } else {
        Write-Host "  no core-ceiling card right now" -ForegroundColor DarkGray
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
& "$PSScriptRoot\..\smoke\smoke-t6b-core-search.ps1" -BaseUrl $BaseUrl -Sha $sha -Search
$fail = $LASTEXITCODE

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * The smoke ran the real search with -Search, so a verdict is" -ForegroundColor Cyan
Write-Host "    now stored. The CRITICAL card on Today should have changed its" -ForegroundColor Cyan
Write-Host "    advice: it should no longer tell you to change the core, and it" -ForegroundColor Cyan
Write-Host "    should name your sleeves as where the drawdown comes from." -ForegroundColor Cyan
Write-Host "  * The card still has NO Accept button, and the new route has no" -ForegroundColor Cyan
Write-Host "    apply path at all. If either grows one, that is the bug." -ForegroundColor Cyan
Write-Host "  * Expect all fifteen candidates to read 'no' against a 13.3%" -ForegroundColor Cyan
Write-Host "    ceiling. That is the finding, not a failure." -ForegroundColor Cyan
Write-Host "  * Two families should carry a CONCENTRATION flag - they stack" -ForegroundColor Cyan
Write-Host "    TQQQ on top of the 38.6% TQQQ sleeve you already run." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The sleeve COUNT is still fixed at the two you run. Discovering" -ForegroundColor DarkYellow
Write-Host "    it by measurement is the other half of T6b and is PR 13b -" -ForegroundColor DarkYellow
Write-Host "    split off so this one stays reviewable." -ForegroundColor DarkYellow
Write-Host "  * Nothing in the UI shows the ranked table yet. The finding" -ForegroundColor DarkYellow
Write-Host "    reaches you through the card; the full ranking is API-only." -ForegroundColor DarkYellow
Write-Host "  * Raising the ceiling or resizing the sleeves is YOUR call and" -ForegroundColor DarkYellow
Write-Host "    the app will not do either for you. Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
