# ship-pr47-migrate-on-deploy.ps1
# PR 47 - the migrations run where the database is reachable: inside the deploy.
#
# Commits four files plus STATUS.md, pushes to main, waits for Railway to serve
# the new SHA, then runs a smoke that ASSERTS the schema revision.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1632 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1632 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
#   End to end on postgres:16: booted the app the way production boots
#   (create_all + the main.py DDL list, no alembic) -> alembic_version table
#   absent, /health schema_revision None -> ran the new migration step ->
#   alembic_version = 0020_solver_verdicts.
# All four new tests were confirmed RED against cce75af (PR 46) first.
#
# THIS CHANGES HOW PRODUCTION BOOTS. alembic upgrade head && uvicorn ... means
# a failed migration stops the container instead of serving against a schema it
# does not have. That is the intended failure mode. AUTO_CREATE_TABLES and the
# main.py DDL list are deliberately left in place as the backstop for this
# deploy; removing them is the NEXT PR and only after /health proves this one.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'railway.json',
  'Dockerfile',
  'app/api/routes/health.py',
  'tests/test_the_deploy_runs_the_migrations.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$startCmd = (Get-Content 'railway.json' -Raw | ConvertFrom-Json).deploy.startCommand
if ($startCmd -notlike '*alembic upgrade head*') { throw "railway.json startCommand does not migrate: $startCmd" }
if ($startCmd -notlike '*uvicorn*') { throw "railway.json startCommand does not start the app: $startCmd" }
Write-Host "startCommand: $startCmd" -ForegroundColor Green

$before = $null
try { $before = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20 } catch { }
if ($null -ne $before) {
  $revBefore = if ($null -ne $before.PSObject.Properties['schema_revision']) { $before.schema_revision } else { '<absent>' }
  Write-Host ("  before: commit {0}, schema_revision {1}" -f $before.commit, $revBefore)
}

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the deploy and migration tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_deploy_runs_the_migrations.py tests/test_the_migration_chain_runs_from_nothing.py tests/test_startup_ddl_covers_migrations.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 47: the migrations run where the database is reachable

Nothing ran them, and nothing could. railway.json started uvicorn and only
uvicorn; the Dockerfile said the same. So on every deploy since the first, the
only thing shaping production's schema was create_all at boot plus a
hand-maintained list of ALTER TABLE ... IF NOT EXISTS statements in main.py,
whose own comment concedes the position:

  "This list IS the mechanism that keeps production's schema current, so a
   migration that adds a column and does not appear here has not really shipped."

That list exists because alembic could not be run against production at all. The
comment records both attempts: once against the sqlite URL in a local .env, once
on DNS, because Railway's DATABASE_URL names a private host that does not resolve
from outside their network. The cost is recorded in the same block -
strategy_backtests.benchmark_ticker was added by migration 0015 on 2026-08-15,
never mirrored into the list, and every read of that table failed for ten days
while the app degraded silently to "no strategy has ever been measured".

The database is reachable from exactly one place: inside the deploy. So the
migrations now run there, ahead of uvicorn, chained with && so a failed migration
stops the boot rather than starting an app against a schema it does not have.
railway.json's startCommand is what Railway uses and the Dockerfile CMD is kept
identical, so docker run reproduces the deploy instead of quietly skipping it.

Proven end to end on postgres:16 before shipping:

  1. booted the app the way production boots (create_all + the DDL list, no
     alembic)  ->  /health schema_revision: None
  2. alembic_version table in that database  ->  0 rows. Never stamped.
  3. ran the deploy's new migration step  ->  alembic_version =
     0020_solver_verdicts

Step 1 is the shape production is in right now; step 3 is what the next deploy
does to it. Only safe because PR 46 made the chain runnable from nothing and
idempotent.

/health now reports schema_revision, the companion to deployed_commit() and for
the same reason: the deploy's schema state could only be checked by eye, and here
it could not be checked at all. null is a real answer and it is the one
production gives today. It never raises - a liveness probe that can fail on a
side query is worse than the thing it reports.

AUTO_CREATE_TABLES and the main.py DDL list are deliberately LEFT IN PLACE as the
backstop for this deploy. Removing them is the next PR, and not until /health has
said 0020_solver_verdicts out loud.

Tests: tests/test_the_deploy_runs_the_migrations.py, four cases, all confirmed
FAILING against cce75af first - the migration runs, it runs before the server, it
is chained with && and not ;, and the Dockerfile does not drift from railway.json.

Gate: SQLite 1632 passed / 0 failed / 0 skipped; Postgres 1632 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- railway.json Dockerfile app/api/routes/health.py tests/test_the_deploy_runs_the_migrations.py STATUS.md scripts/deploy/ship-pr47-migrate-on-deploy.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

# --------------------------------------------------------------------- push
Write-Host '=== 3. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

# ------------------------------------------------------------ wait for deploy
# This deploy runs a migration before the app starts, so give it longer and read
# the Railway deploy log if it does not come up. A container that never becomes
# healthy here means the migration failed - that is the designed failure mode,
# not a mystery.
Write-Host '=== 4. waiting for production to serve ' -NoNewline -ForegroundColor Cyan
Write-Host "$sha ===" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(12)
$live = $null
while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
    if ($h.commit -eq $sha) { $live = $h; break }
    Write-Host ("  serving {0}, waiting..." -f $h.commit)
  } catch {
    Write-Host '  /health not answering yet (a migration runs before the app)...'
  }
  Start-Sleep -Seconds 20
}
if ($null -eq $live) {
  Write-Host 'The container never came up. Read the Railway deploy log:' -ForegroundColor Red
  Write-Host '  a failed "alembic upgrade head" now stops the boot on purpose.' -ForegroundColor Red
  throw "production never served $sha within 12 minutes"
}
Write-Host ("LIVE: {0} v{1}" -f $live.commit, $live.version) -ForegroundColor Green

# -------------------------------------------------------------------- smoke
Write-Host '=== 5. smoke (asserted) ===' -ForegroundColor Cyan
$rev = $live.schema_revision
Write-Host ("  schema_revision: {0}" -f $(if ($null -eq $rev) { 'null' } else { $rev }))

if ($null -eq $rev -or $rev -eq '') {
  Write-Host 'SMOKE FAILED - the migrations did not run.' -ForegroundColor Red
  Write-Host '  /health answered, so the app booted, which means the start command' -ForegroundColor Red
  Write-Host '  Railway used is not the one in railway.json. Check the service' -ForegroundColor Red
  Write-Host '  settings for a startCommand override in the Railway dashboard.' -ForegroundColor Red
  throw 'schema_revision is null after the deploy'
}
if ($rev -ne '0020_solver_verdicts') {
  throw ("schema_revision is {0}, expected 0020_solver_verdicts - the chain stopped early" -f $rev)
}

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'X-Agent-Key' = (Get-Content $keyPath -Raw).Trim() }
  $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  NAV {0:N2} ILS across {1} positions" -f $p.nav_ils, $p.positions.Count)
  if ($p.positions.Count -lt 1) { throw 'the book came back empty after a schema change - stop and investigate' }
}

Write-Host ''
Write-Host 'SMOKE PASSED' -ForegroundColor Green
Write-Host '  schema_revision went from absent to 0020_solver_verdicts. The migrations'
Write-Host '  have now run against production for the first time, and /health can be'
Write-Host '  asked about it from outside instead of guessed at.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
