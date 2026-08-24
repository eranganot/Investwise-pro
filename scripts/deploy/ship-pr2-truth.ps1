# SHIP PR 2 - the app says true things about money and time.
#
#   .\scripts\deploy\ship-pr2-truth.ps1
#   .\scripts\deploy\ship-pr2-truth.ps1 -SkipTests    # only if you just ran them
#
# Four small, independent fixes. None of them changes what the app DOES with
# your money; all four change what it TELLS you, which is what made the last
# week hard to diagnose.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml (pytest AND ruff on app AND tests); untracked files are
# harmless and must not halt this script.
#
# AND, new since PR 1: run the POSTGRES job too. See -WithPostgres below.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/nav_history.py",
    "app/services/push_service.py",
    "app/services/recommendations.py",
    "app/api/routes/intake.py",
    "app/static_app/index.html",
    "tests/test_job_health_and_gain.py",
    "CLAUDE.md",
    "FIX_PLAN_2026-08-23_audit.md",
    "scripts/smoke/smoke-truth.ps1",
    "scripts/deploy/ship-pr2-truth.ps1"
)

$Message = @(
    "Say true things about money and time",
    "",
    "Four reports that looked like reports.",
    "",
    "  nav_history      run_nav_snapshot_blocking imported async_session;",
    "                   the name is AsyncSessionLocal. The import sits INSIDE",
    "                   the function, so neither import time nor ruff saw it,",
    "                   and NO TEST CALLED THE FUNCTION. It failed 7 nights out",
    "                   of 7 from the day it shipped. Every missed night is a",
    "                   day of history that cannot be reconstructed - which is",
    "                   what makes this the one job whose failure is permanent.",
    "",
    "                   Renaming it alone would not have been enough: APScheduler",
    "                   runs it in a worker thread via asyncio.run(), so the",
    "                   shared pooled engine hands asyncpg a connection from",
    "                   another loop and it refuses. Green on SQLite, dead on",
    "                   Postgres. It now builds its own NullPool engine, the",
    "                   pattern pricing_service has run 332 times without a",
    "                   failure. A test now actually invokes it.",
    "",
    "  push/status      computed blockers from a hardcoded (push_evaluate,",
    "                   push_digest), so nav_snapshot at 7/7 failures was not",
    "                   among the jobs it could see and the endpoint answered",
    "                   'Nothing obviously broken'. It now reads every job in",
    "                   JOB_STATE. A monitor that cannot see a dead job is worse",
    "                   than none, because it is what you check INSTEAD of",
    "                   looking.",
    "",
    "  /portfolio       returned ONE invested_ils that meant contributions",
    "                   when a ledger existed and cost basis otherwise. Live:",
    "                   20,000 from a one-row ledger against 27,387.81 of actual",
    "                   basis, both in the same response. Headline read +8,496",
    "                   (+42.48%) on a book that had gained 1,109 (+4.0%), and",
    "                   the 7,388 between them - minted by the FX bug PR 1 fixed",
    "                   - was being reported as performance.",
    "",
    "                   Now: gain_vs_basis answers 'did my holdings make money',",
    "                   gain_vs_contributions answers 'is it worth more than I",
    "                   paid in', and unexplained_basis_ils is the gap that",
    "                   should be zero. The card leads with gain-vs-basis and",
    "                   names the gap instead of absorbing it.",
    "",
    "  _DONE_TTL_DAYS   90 -> 14. The number was not the root cause - the ID is:",
    "                   sha1(category|ticker) with no amount and no date, so",
    "                   today's completed card and tomorrow's regenerated one",
    "                   share an id. Five Accepts on 08-20/21 muted the tax card,",
    "                   the sell-Equities card and the SOXL cold-start card until",
    "                   mid-November while /mix went on asking to sell 5,078 of",
    "                   Equities. Content-bearing ids are PR 4; this caps the",
    "                   blast radius meanwhile.",
    "",
    "CLAUDE.md: the 'Postgres job cannot be reproduced locally' line was wrong",
    "and is why 5a6dfef shipped with three cross-loop failures only that job",
    "could see. It now says how to run it, records that Railway deploys on red,",
    "and states the rule this PR is an instance of: a job nothing calls is a job",
    "nobody has tested.",
    "",
    "Verified before pushing this time - all three CI jobs:",
    "  pytest on postgres:16     960 passed, 12 skipped",
    "  pytest on sqlite          964 passed, 4 skipped",
    "  ruff check app tests      clean"
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) {
    Warn "-SkipTests set. CI is still the real gate."
} else {
    Invoke-Suite -Focus @("tests/test_job_health_and_gain.py", "tests/test_nav_history.py", "tests/test_contributions.py")
}
Write-Host ""
Warn "The POSTGRES job is not run here - it needs a local server. It IS"
Warn "reproducible (see CLAUDE.md). It was verified in the sandbox for this PR."

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

Step "6. Smoke (read-only)"
if (-not $env:IW_AGENT_KEY) { Warn "IW_AGENT_KEY not set - the smoke falls back to the committed key." }
& (Join-Path $RepoRoot "scripts\smoke\smoke-truth.ps1") -Sha $sha -BaseUrl $BaseUrl
$smokeExit = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  WHAT TO CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "    - Open the app. The headline gain should now read about +4%," -ForegroundColor Gray
Write-Host "      not +42.48%, with a line naming the unexplained cost basis." -ForegroundColor Gray
Write-Host "    - T2.4 will SKIP with that gap. It is REAL and PR 3 repairs it." -ForegroundColor Gray
Write-Host "    - T1.2 is the check that matters and it TAKES A NIGHT. The job" -ForegroundColor Gray
Write-Host "      fires at 22:10 UTC. Run this again tomorrow:" -ForegroundColor Gray
Write-Host "        .\scripts\smoke\smoke-truth.ps1 -Sha $sha" -ForegroundColor Gray
Write-Host "      nav_snapshot must show last_ok true. Until then nothing here" -ForegroundColor Gray
Write-Host "      proves the fix - the failure only appears when the job runs." -ForegroundColor Gray
Write-Host ""
Write-Host "  Aug 18-21 are gone permanently. They cannot be backfilled and" -ForegroundColor Yellow
Write-Host "  nothing in this PR pretends otherwise." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then update STATUS.md." -ForegroundColor Cyan
Write-Host ""
if ($smokeExit -ne 0) { exit 1 }
exit 0
