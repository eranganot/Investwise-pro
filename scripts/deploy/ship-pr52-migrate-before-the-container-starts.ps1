# ship-pr52-migrate-before-the-container-starts.ps1
# PR 52 - the migration retry, as a pre-deploy step. RUN AFTER PR 49/50/51.
#
# PR 49 IS A HARD PREREQUISITE. Without healthcheckPath a container that cannot
# serve still takes traffic, which is what turned PR 47 into a two-hour outage
# rather than a failed deploy. This script refuses to run if it is missing.
#
# The root cause of PR 47 is STILL UNKNOWN and the deploy log that held the
# answer is gone. So this changes the MECHANISM rather than guessing, and every
# surviving hypothesis is contained:
#
#   * preDeployCommand runs the migration as its own step with its own log -
#     the missing artifact is exactly what ended the last investigation.
#   * preDeployTimeoutSeconds turns "the process never exited" from an
#     unbounded hang into a failed deploy at a known time.
#   * healthcheckPath (PR 49) keeps the working container serving if it fails.
#   * The database is ALREADY stamped at 0020_solver_verdicts, so
#     "alembic upgrade head" is a no-op on this run. The first attempt tests the
#     mechanism against a database that needs nothing done to it.
#
# Rehearsed against a production-shaped postgres:16 - create_all-built, then
# stamped at head, which is the state PR 47 left production in:
#     $ sh -c 'alembic upgrade head && alembic current'
#     0020_solver_verdicts (head)
#     PRE-DEPLOY EXIT CODE=0
#
# AUTO_CREATE_TABLES stays TRUE in this PR on purpose. Today create_all plus the
# ALTER list in main.py is what maintains production's schema; turning it off in
# the same push that first tries the pre-deploy step means a failure leaves
# nothing maintaining the schema, and two changes landing together makes the
# result unattributable. It goes in the NEXT push, after a log has shown this
# one running clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$deploy = (Get-Content 'railway.json' -Raw | ConvertFrom-Json).deploy

if (-not $deploy.healthcheckPath) {
  throw 'PR 49 is not in yet. Without healthcheckPath a failed migration takes production down again - ship PR 49 first.'
}
if ($deploy.startCommand -like '*alembic*') {
  throw 'the migration is in the START command. That is PR 47, and it returned 502 for two hours.'
}
if ($deploy.preDeployCommand -notlike '*alembic upgrade head*') {
  throw "preDeployCommand does not run the migration: $($deploy.preDeployCommand)"
}
if (-not $deploy.preDeployTimeoutSeconds) {
  throw 'preDeployTimeoutSeconds is not set - a migration that never exits would hang the deploy.'
}
Write-Host ("preDeployCommand: {0}" -f $deploy.preDeployCommand) -ForegroundColor Green
Write-Host ("preDeployTimeout: {0}s   healthcheck: {1}" -f $deploy.preDeployTimeoutSeconds, $deploy.healthcheckPath) -ForegroundColor Green

# What the database says BEFORE. This is the number the whole PR is about.
Write-Host '=== 0b. what production is stamped at right now ===' -ForegroundColor Cyan
$before = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
$revBefore = $before.schema_revision
Write-Host ("  commit {0}   schema_revision {1}" -f $before.commit, $(if ($null -eq $revBefore) { 'null' } else { $revBefore }))
if ($null -eq $revBefore) {
  Write-Host '  NOTE: null means the migrations have never run here. That is not' -ForegroundColor Yellow
  Write-Host '  what was expected - PR 47 left it stamped at 0020_solver_verdicts.' -ForegroundColor Yellow
  Write-Host '  The upgrade will therefore do REAL work rather than being a no-op.' -ForegroundColor Yellow
  Write-Host '  It is still gated by the healthcheck, but stop and think first.' -ForegroundColor Yellow
  $ans = Read-Host '  Continue anyway? (yes/no)'
  if ($ans -ne 'yes') { throw 'stopped by request' }
}

Write-Host '=== 1. ruff + the deploy and migration tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_deploy_runs_the_migrations.py tests/test_the_migration_chain_runs_from_nothing.py tests/test_a_broken_deploy_never_takes_traffic.py tests/test_startup_ddl_covers_migrations.py
if ($LASTEXITCODE -ne 0) { throw 'the deploy/migration tests failed - do not push' }

Write-Host '=== 2. full suite ===' -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 52: run the migrations before the container starts, not as part of it

The root cause of PR 47 is still UNKNOWN and the deploy log that held the answer
is gone, so this changes the mechanism rather than guessing at the cause.

What was established after the revert, and inverts the original diagnosis: the
PR 47 migration RAN TO COMPLETION. /health returned schema_revision
"0020_solver_verdicts" afterwards, nothing in app/ writes alembic_version
(health.py only reads it), and the reverted boot logs "Ensuring database schema
(auto_create_tables=True)" with no alembic line.

Ruled out, each with the observation that killed it:
  * the && chain - re-run exactly as deployed against an empty database,
    sh -c 'alembic upgrade head && echo ...' exits 0 and continues.
  * alembic/env.py importing the app - it imports only app.core.config,
    app.models.base and app.models; no app.main, no scheduler, disposes its
    engine.
  * the new /health.schema_revision field - /, /docs, /openapi.json and
    /health/ready ALL returned 502, so no handler ran at all.

Still open, and not distinguishable without the lost log: alembic exited
non-zero at interpreter teardown despite doing the work (an unraisable at
shutdown does exactly that, and && swallows the boot), or the process never
exited so the chain never advanced.

Every one of those is contained by this change rather than tested on
production:
  * preDeployCommand runs the migration as its own step with its own log. The
    missing artifact is precisely what ended the last investigation.
  * preDeployTimeoutSeconds turns "never exited" into a failed deploy at a
    known time instead of an unbounded hang.
  * healthcheckPath (PR 49) means a container that does not answer never takes
    traffic, so a repeat is a failed deploy with production still up.
  * The database is already stamped at 0020_solver_verdicts, so the upgrade is
    a NO-OP on this run. The first attempt tests the mechanism against a
    database that needs nothing done to it.

Rehearsed against a production-shaped postgres:16 - schema built by create_all
plus the main.py ALTER list, then stamped at head, which is the state PR 47 left
production in:

    $ sh -c 'alembic upgrade head && alembic current'
    0020_solver_verdicts (head)
    PRE-DEPLOY EXIT CODE=0

alembic current is chained after the upgrade so the log carries a positive
statement of where the database ended up, rather than only the absence of an
error.

AUTO_CREATE_TABLES stays true here on purpose. Today create_all plus the ALTER
list in main.py IS what maintains production's schema; turning it off in the
same push that first tries the pre-deploy step would mean a failure leaves
nothing maintaining the schema, and two changes landing together makes the
result unattributable. It goes in the push AFTER a deploy log has shown this
one running clean. Dockerfile hardening waits for the same reason: it changes
the image, which adds a variable to a retry whose cause is unknown.

The three tests PR 48 removed come back, in the shape production can survive:
something in the deploy must migrate, it must be the pre-deploy step and not
the boot, and that step must have a timeout.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- railway.json tests/test_the_deploy_runs_the_migrations.py STATUS.md scripts/deploy/ship-pr52-migrate-before-the-container-starts.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

# ------------------------------------------------------------------- verify
& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------------- the thing this PR is for
Write-Host '=== 5. did the pre-deploy step actually run? ===' -ForegroundColor Cyan
$after = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
$revAfter = $after.schema_revision
Write-Host ("  schema_revision before: {0}" -f $(if ($null -eq $revBefore) { 'null' } else { $revBefore }))
Write-Host ("  schema_revision after : {0}" -f $(if ($null -eq $revAfter) { 'null' } else { $revAfter }))

if ($null -eq $revAfter) {
  Write-Host '' -ForegroundColor Red
  Write-Host 'The database is not stamped. The pre-deploy step did not do its job,' -ForegroundColor Red
  Write-Host 'and production is up only because the healthcheck let a container' -ForegroundColor Red
  Write-Host 'through that had not migrated. Read the deploy log before anything' -ForegroundColor Red
  Write-Host 'else - that log is the whole reason this PR exists.' -ForegroundColor Red
} else {
  Write-Host ''
  Write-Host 'PR 52 SHIPPED.' -ForegroundColor Green
  Write-Host '  Production answers and the database is stamped.'
}

Write-Host ''
Write-Host 'NEXT, AND DO THIS EVEN IF EVERYTHING IS GREEN:' -ForegroundColor Yellow
Write-Host '  Open this deploy on Railway and find the PRE-DEPLOY step. It should' -ForegroundColor Yellow
Write-Host '  contain one line: "0020_solver_verdicts (head)". Send it either way.' -ForegroundColor Yellow
Write-Host '  A clean run is the first direct evidence about this mechanism that' -ForegroundColor Yellow
Write-Host '  anyone has had, and a failed one is the log that has been missing' -ForegroundColor Yellow
Write-Host '  since PR 47. AUTO_CREATE_TABLES=false is gated on reading it.' -ForegroundColor Yellow
