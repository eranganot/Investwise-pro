# SHIP PR 12 - the job that lied, the card that was missing, the severity that
# never moved.
#
#   .\scripts\deploy\ship-pr12-core-card-and-price-refresh.ps1
#   .\scripts\deploy\ship-pr12-core-card-and-price-refresh.ps1 -SkipTests
#
# No migration. No new table. Read section 0.
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
    "app/services/pricing_service.py",
    "app/worker/scheduler.py",
    "app/services/solver_store.py",
    "app/services/recommendations.py",
    "tests/test_the_card_says_how_bad_it_is.py",
    "scripts/smoke/smoke-core-card-and-price-refresh.ps1",
    "scripts/deploy/ship-pr12-core-card-and-price-refresh.ps1",
    "STATUS.md"
)

$Message = @(
    "Three things that reported success, or reported nothing, while wrong",
    "",
    "1. A JOB THAT CRASHED ON EVERY BOOT AND SAID IT WAS FINE.",
    "",
    "scheduler.start_scheduler primed the price refresh with a bare",
    "run_price_refresh_blocking() on the FastAPI startup thread. That thread has",
    "a running event loop and the function calls asyncio.run(), which refuses to",
    "nest. So every boot logged",
    "",
    "  scheduled price refresh failed",
    "  exc=RuntimeError: asyncio.run() cannot be called from a running event loop",
    "  pricing_service.py:256: RuntimeWarning: coroutine '_refresh_all' was never awaited",
    "",
    "and then RETURNED {'updated': 0, 'failed': 0}. Those are the same two",
    "numbers a clean run produces when nothing needs repricing, so a crash and a",
    "quiet no-op were indistinguishable to every reader downstream. This is the",
    "nav_snapshot failure PR 2 removed one layer up, and it is worse than a",
    "failure: nobody goes looking at a job reporting success.",
    "",
    "The call was also OUTSIDE _guarded, so JOB_STATE never heard about it and",
    "push_service.blockers - which PR 2 taught to read job state - could not name",
    "it. A job that fails outside _guarded fails invisibly.",
    "",
    "Both fixed. Exceptions now PROPAGATE to _guarded, which is the one place",
    "that records an outcome; two recorders is two versions of whether the job",
    "ran. And the prime goes through the SAME registration via next_run_time",
    "rather than a second call site that can rot - _guarded already runs its",
    "target in a worker thread, which is the context asyncio.run() needs, so the",
    "30-minute runs were always fine and only the prime was broken.",
    "",
    "run_price_refresh_blocking now also refuses up front, by name, when it finds",
    "a running loop. asyncio's own wording reads like a library bug rather than",
    "what it is: a caller putting the function on the wrong thread.",
    "",
    "2. THE SOLVER'S BIGGEST FINDING HAD NO CARD.",
    "",
    "PR 11 stored the verdict and let the war room take a one-tap away with it.",
    "But floor_breaches_ceiling is not a statement about any one signal - it",
    "means the CORE ALONE breaches the drawdown ceiling, so no sleeve size",
    "repairs it, INCLUDING ZERO. On this book that is 35.1% against a 13.3%",
    "ceiling, 21.8 points over, and Today showed five cards, none of which",
    "mentioned it. It was reachable only by opening the Target card, which is",
    "the 'nothing said it was missing' failure PR 4 existed to end.",
    "",
    "_core_ceiling_recs is deliberately NOT part of _war_room_recs. That function",
    "needs war-room SIGNALS to have something to attach a constraint to, and this",
    "finding is about the book itself. The live book had one displayed session",
    "and it was a Rebalance, so a 21-point breach would have gone unsaid for want",
    "of an unrelated BUY.",
    "",
    "Three properties, each of which is why a line of it exists:",
    "",
    "  IT NEVER CLAIMS A CHANGE IT DOES NOT MAKE. apply.kind is 'none'. Swapping",
    "  a core is a whole-book change and target_solver is read-only without",
    "  exception - a return target one tap from a book change is the C5 slider",
    "  bug with higher stakes, in the solver's own words.",
    "",
    "  IT CARRIES ITS AGE, like every other reader of a stored verdict.",
    "",
    "  A STALE VERDICT STILL SPEAKS. PR 11's rule is 'stale stops blocking, it",
    "  does not stop speaking'. This card blocks nothing, so staleness changes",
    "  the wording and not whether it appears. Hiding a 21-point breach because",
    "  the measurement is 40 hours old would be that rule inverted.",
    "",
    "No verdict at all means no card. A book with nothing measured gets silence,",
    "not an asserted breach - inventing a finding is worse than missing one, and",
    "the smoke fails if a card appears with no verdict behind it.",
    "",
    "as_constraint gains one field, core_drawdown_pct. _headline already stated",
    "it in prose; a card that has to pull the figure back out of a sentence to",
    "put it in an audit trail is one rewording away from printing nothing.",
    "",
    "3. THE REBALANCE CARD CALLED A 20-POINT DRIFT 'NICE TO HAVE'.",
    "",
    "severity was the literal string 'MEDIUM', so a book 3.1 points out of target",
    "and a book 20.4 points out rendered identically. Four other card types in",
    "the same file already scale by magnitude - behind-goal at CRITICAL >= 40,",
    "benchmark lag at HIGH <= -10, tax harvest, holding verdict. The one card",
    "that reports a measured gap did not.",
    "",
    "It scales on the MULTIPLE of allocation_drift_threshold, not on a fresh set",
    "of absolute cut-offs: that setting already decides a drift is worth a card",
    "at all, and a second independent scale would be a second opinion about what",
    "'drifted' means. At the default 3%, 3x is 9 points and 6x is 18. This book:",
    "Equities CRITICAL, Fixed Income HIGH, Commodities HIGH.",
    "",
    "AND _reconcile now takes the MAX severity of the legs it merges. It rewrote",
    "the merged card's id, title, action, why and meta and never touched its",
    "severity, so it kept rebal[0]'s - and rebal[0] is whichever leg sorted",
    "first, which has nothing to do with which leg is worst. Latent only while",
    "every leg was hardcoded MEDIUM. Fixing the scale without this would have",
    "introduced the bug the scale was meant to fix.",
    "",
    "16 new tests. Suite green on SQLite (1141) and on Postgres (1137), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Nothing. No migration, no new table, no column changes." -ForegroundColor Green
Write-Host ""
Write-Host "  It DOES change what you see: a new CRITICAL card whenever the" -ForegroundColor DarkGray
Write-Host "  stored verdict says your core alone breaches your ceiling, and" -ForegroundColor DarkGray
Write-Host "  rebalance cards that scale with the drift they measured." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record what Today says BEFORE the change"
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
try {
    $pre = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $ApiHeaders -TimeoutSec 600
    $sev = (@($pre.recommendations) | ForEach-Object { $_.severity }) -join ', '
    Write-Host "  severities now: $sev" -ForegroundColor DarkGray
    $solverCards = @(@($pre.recommendations) | Where-Object { $_.meta.source -eq 'target_solver' })
    Write-Host "  solver cards now: $($solverCards.Count) (expect 0 before this deploy)" -ForegroundColor DarkGray
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
# EIGHT MINUTES, AND THE POLL IS THE POINT. On PR 11 the payload was checked
# once, seconds before the new container came up, and the absence of a marker
# was read as a failed build. The deploy had landed 21 seconds later.
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
& "$PSScriptRoot\..\smoke\smoke-core-card-and-price-refresh.ps1" -BaseUrl $BaseUrl -Sha $sha
$fail = $LASTEXITCODE

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * A NEW CRITICAL card at the top of Today: 'Your core alone" -ForegroundColor Cyan
Write-Host "    breaches your drawdown ceiling'. It should say 35.1% against" -ForegroundColor Cyan
Write-Host "    13.3%, 21.8 points over, and how old the solve is." -ForegroundColor Cyan
Write-Host "  * That card has NO Accept button. It never will - swapping a core" -ForegroundColor Cyan
Write-Host "    is a whole-book change. If it grows one, that is the bug." -ForegroundColor Cyan
Write-Host "  * 'Rebalance toward your target mix' should now read CRITICAL," -ForegroundColor Cyan
Write-Host "    not MEDIUM. Your Equities leg is 20.4 points out, which is" -ForegroundColor Cyan
Write-Host "    6.8x the 3% trigger." -ForegroundColor Cyan
Write-Host "  * /api/v1/push/status should show price_refresh with a run" -ForegroundColor Cyan
Write-Host "    recorded. Before this it was never there on a fresh boot." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE, because PR 13 owns it:" -ForegroundColor DarkYellow
Write-Host "  * The new card tells you to change the core and does not say" -ForegroundColor DarkYellow
Write-Host "    WHICH. T6b measures the 14 candidate cores and ranks them." -ForegroundColor DarkYellow
Write-Host "  * If price_refresh now shows last_ok FALSE, that is this fix" -ForegroundColor DarkYellow
Write-Host "    working, not a regression - the failure was always there and" -ForegroundColor DarkYellow
Write-Host "    was being reported as success. Send me the error." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
