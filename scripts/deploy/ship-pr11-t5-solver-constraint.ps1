# SHIP PR 11 / T5 - the war room reads the solver, and the red team can veto.
#
#   .\scripts\deploy\ship-pr11-t5-solver-constraint.ps1
#   .\scripts\deploy\ship-pr11-t5-solver-constraint.ps1 -SkipTests
#
# THIS ONE ADDS A TABLE (solver_verdicts, migration 0020). Read section 0.
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
    "alembic/versions/0020_solver_verdicts.py",
    "app/models/tables.py",
    "app/services/solver_store.py",
    "app/services/target_solver.py",
    "app/services/war_room.py",
    "app/services/recommendations.py",
    "app/api/routes/war_room.py",
    "app/engines/state_machine.py",
    "app/worker/scheduler.py",
    "tests/test_war_room_reads_the_solver.py",
    "scripts/smoke/smoke-t5-solver-constraint.ps1",
    "scripts/deploy/ship-pr11-t5-solver-constraint.ps1",
    "STATUS.md"
)

$Message = @(
    "T5: the war room reads the solver, and the red team can finally veto",
    "",
    "STATUS.md:79 said T5 was 'the solver becomes a constraint the Risk /",
    "Decision / Adversary agents read; not an eighth agent, not the decision",
    "maker'. Reading the code first turned up two things the plan did not know.",
    "",
    "1. THE ADVERSARY COULD NOT VETO ANYTHING ON THE CARD PATH.",
    "",
    "build_war_room() runs vet / optimize / rank / display individually and",
    "appended the Adversary's findings as transcript PROSE, branching on nothing.",
    "adversary_enforce_veto defaults to True and was honoured in exactly one",
    "function - StateMachine.cross_examine - reachable only from",
    "/api/v1/decision-feed. The route that produces the cards people actually see",
    "had no veto at all. A Severity.BLOCK finding was a sentence.",
    "",
    "test_war_room_includes_cross_examination_lines did not catch it. It asserts",
    "the Adversary lines exist and carry a severity field - true the whole time,",
    "and silent on whether anything acts on them. Coverage that looks like",
    "coverage.",
    "",
    "The rule now lives in ONE place, StateMachine.blocks_now(). cross_examine",
    "had it inline four times as 'enforce and note.blocks'; the war room had it",
    "nowhere. Both call the same function, so the two paths cannot drift apart",
    "again - the same argument as PR 8's columns and PR 9's target.",
    "",
    "2. NOTHING STORED A SOLVER VERDICT, SO NOTHING COULD ACT ON ONE.",
    "",
    "GET /plan/target recomputed from scratch on every request: a",
    "coarse-then-fine grid sweep, each point one simulation of ten years of daily",
    "closes. Meanwhile the war room already costs 6,726 ms on /recommendations",
    "while every other agent comes in under 50 ms. So 'the agents consult the",
    "solver' could not mean 'call the solver' - that puts a multi-second sweep on",
    "the slowest path in the app.",
    "",
    "The solver now WRITES (solver_verdicts, migration 0020) and the war room",
    "READS. Two properties make a stored verdict safe to act on:",
    "",
    "  AGE TRAVELS WITH THE ANSWER. A verdict is a statement about one book at",
    "  one moment and this one can disable a button, so as_constraint() returns",
    "  the age and every card that acts on it prints it.",
    "",
    "  STALE STOPS BLOCKING, IT DOES NOT STOP SPEAKING. Past 36 hours - about",
    "  1.5 nightly runs, so a missed job - the constraint keeps its finding and",
    "  drops its teeth. Letting a week-old measurement go on refusing things is",
    "  how an app starts saying no for reasons nobody can reconstruct.",
    "",
    "Only UNREACHABLE and DRAWDOWN_BOUND block. REACHED_ABOVE_CAP deliberately",
    "does not: it means reachable, past your concentration cap, which is a",
    "different conversation and not one that should disable anything.",
    "",
    "WHAT A BLOCKED CARD DOES: it keeps the card and loses the one tap.",
    "apply.kind is demoted to 'none', which is the same mechanism every advisory",
    "card already uses - _is_actionable reads it, so nothing new can forget to",
    "check a new flag. The finding and the verdict's age land in the card's notes.",
    "",
    "Suppressing it was the alternative and it is the PR 4 failure mode: eleven",
    "cards went missing there, and a card deleted because of a background",
    "measurement the user cannot see is strictly worse than one that explains",
    "itself and refuses.",
    "",
    "SCOPED TO SIGNALS THAT ADD RISK. A trim, a hedge or a de-risking rebalance",
    "is exactly what a book that cannot reach its target should be doing;",
    "blocking those would turn a safety feature into a trap. Keyed off",
    "action_type, not the path label - 'path' is Bulletproof for everything that",
    "is not a BUY, and that label has already been misread once (PR 4).",
    "",
    "And the transcript says the constraint WAS CONSULTED even when it does not",
    "bite. Silence is indistinguishable from 'nobody asked', which is the state",
    "this whole phase exists to end.",
    "",
    "THE NIGHTLY RE-SOLVE runs at 04:10, after the 03:30 backtest refresh,",
    "because a re-solve reads the measurements that job writes - solving first",
    "would answer from yesterday's numbers and stamp today's timestamp on them.",
    "",
    "It does not invent a question. A solve needs a target excess and a drawdown",
    "ceiling; neither is stored on the plan (the UI passes them as query",
    "parameters, so they exist only in the answer). The job re-runs the LAST",
    "verdict's own parameters. A book whose owner has never opened the target",
    "card has no stored question, gets no verdict, and its war room says 'no",
    "solve on record' rather than being handed a ceiling nobody chose. A",
    "constraint strong enough to disable a button has to come from a number the",
    "user actually set.",
    "",
    "Its own NullPool engine inside its own loop, per pricing_service - handing",
    "an APScheduler thread a connection from the app's shared pool is the",
    "cross-loop asyncpg rejection that has already cost this repo three",
    "incidents.",
    "",
    "Migration 0020 verified by hand on a real Postgres 16: create, downgrade,",
    "re-upgrade, with jsonb (not json) for the detail column. Suite green on SQLite and on",
    "Postgres, plus ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  Migration 0020 CREATES one table:" -ForegroundColor Yellow
Write-Host "      solver_verdicts" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Additive only. Nothing is dropped, no existing column changes, and" -ForegroundColor DarkGray
Write-Host "  an empty table simply means 'no solve on record' - which every book" -ForegroundColor DarkGray
Write-Host "  is in until the target card is opened or the 04:10 job runs." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record what the war room says BEFORE the change"
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
try {
    $pre = Invoke-RestMethod -Uri "$BaseUrl/api/v1/war-room" -Headers $ApiHeaders -TimeoutSec 600
    $shown = @(@($pre.sessions) | Where-Object { $_.outcome -eq 'DISPLAYED' })
    $vetoed = @(@($pre.sessions) | Where-Object { $_.outcome -eq 'VETOED' })
    Write-Host "  sessions now: $($shown.Count) displayed, $($vetoed.Count) vetoed" -ForegroundColor DarkGray
    Write-Host "  solver_constraint on the payload: $($null -ne $pre.solver_constraint)" -ForegroundColor DarkGray
} catch {
    Warn "could not read the war room first: $($_.Exception.GetType().Name)"
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
& "$PSScriptRoot\..\smoke\smoke-t5-solver-constraint.ps1" -BaseUrl $BaseUrl -Sha $sha
$fail = $LASTEXITCODE

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Your book has NO stored verdict yet, so the war room will say" -ForegroundColor Cyan
Write-Host "    'No solve on record' and nothing will be blocked. That is the" -ForegroundColor Cyan
Write-Host "    correct first state, not a failure." -ForegroundColor Cyan
Write-Host "  * Open the Target card (or run the smoke with -Solve). That writes" -ForegroundColor Cyan
Write-Host "    a verdict. Your solver said DRAWDOWN_BOUND with the core alone" -ForegroundColor Cyan
Write-Host "    breaching the ceiling on 2026-08-16, so expect the constraint to" -ForegroundColor Cyan
Write-Host "    start blocking one-tap BUYs from the war room." -ForegroundColor Cyan
Write-Host "  * Those cards must still be VISIBLE, with the reason and the age" -ForegroundColor Cyan
Write-Host "    on them. If a card disappears instead, that is the bug." -ForegroundColor Cyan
Write-Host "  * Trims and hedges must NOT be blocked. If a de-risking card" -ForegroundColor Cyan
Write-Host "    loses its Accept, tell me - that would be the safety feature" -ForegroundColor Cyan
Write-Host "    working as a trap." -ForegroundColor Cyan
Write-Host ""
Write-Host "  The 04:10 UTC re-solve has not run yet. Until it does, the only" -ForegroundColor DarkYellow
Write-Host "  verdicts are ones you created by opening the Target card, and a" -ForegroundColor DarkYellow
Write-Host "  verdict older than 36h stops blocking by design." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
