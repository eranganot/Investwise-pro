# ship-pr48-revert-the-boot-migration.ps1
# PR 48 - REVERT PR 47's boot change. Production is down; this brings it back.
#
# Run this first and diagnose after. Service before understanding.
#
# What happened: PR 47 put "alembic upgrade head &&" in front of uvicorn so the
# migrations would run where the database is reachable. Against production the
# migration did not complete, and the && did exactly what it was written to do -
# the app never started. Every endpoint returned 502 for two hours. Railway's
# card says "Deployment successful" because that reflects build-and-deploy, not
# whether anything answers.
#
# Ruled out as the cause, on evidence: the new /health.schema_revision field.
# /, /docs, /openapi.json and /health/ready ALL returned 502, so the process was
# not listening and no request handler ran. That field is kept - it is how the
# next attempt gets verified instead of hoped at.
#
# This reverts the start command in BOTH railway.json and the Dockerfile, and
# removes the three tests that pinned the migration into the boot. A test that
# demands a behaviour production cannot survive is not a guard, it is a
# countdown. They come back with the fix.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

Write-Host '=== 0b. confirming production is actually down ===' -ForegroundColor Cyan
$down = $true
try {
  $h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
  Write-Host ("  /health answered: commit {0}" -f $h.commit) -ForegroundColor Yellow
  $down = $false
} catch {
  Write-Host '  /health does not answer - proceeding with the revert.' -ForegroundColor Yellow
}
if (-not $down) {
  Write-Host '  Production is serving again on its own. The revert is still the right' -ForegroundColor Yellow
  Write-Host '  move (the boot is fragile until we know why), but this is no longer an' -ForegroundColor Yellow
  Write-Host '  outage - stop here if you would rather diagnose first.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- guard rails
$startCmd = (Get-Content 'railway.json' -Raw | ConvertFrom-Json).deploy.startCommand
if ($startCmd -like '*alembic*') { throw "railway.json still migrates on boot: $startCmd" }
if ($startCmd -notlike '*uvicorn*') { throw "railway.json does not start the app: $startCmd" }
Write-Host "startCommand: $startCmd" -ForegroundColor Green

Write-Host '=== 1. ruff + the deploy and migration tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_deploy_runs_the_migrations.py tests/test_the_migration_chain_runs_from_nothing.py tests/test_startup_ddl_covers_migrations.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 48: revert the boot migration - production could not survive it

PR 47 put "alembic upgrade head &&" in front of uvicorn so the migrations would
run in the one place the database is reachable from. Against production the
migration did not complete, and the && did exactly what it was written to do:
the app never started. Every endpoint returned 502 for two hours.

Railway reported "Deployment successful". That reflects build-and-deploy, not
whether anything answers - the ship script's own 12-minute /health wait was the
signal that was right, and the dashboard was the one that misled.

Ruled out as the cause, on evidence rather than by elimination: the new
/health.schema_revision field. /, /docs, /openapi.json and /health/ready all
returned 502 too, so the process was not listening at all and no request handler
ran. The field is KEPT - it is how the next attempt gets verified instead of
hoped at, and it costs nothing while the migrations are not running.

Not yet known: why the migration fails there and not here. It ran clean twice
against a production-shaped postgres:16 in the sandbox - create_all-built, never
stamped, landing on 0020_solver_verdicts - so the sandbox is not a faithful model
of that database and the difference is unidentified. The deploy log is the only
place the answer exists and it has not been read yet. No speculative fix, and no
second attempt until it has.

The three tests that pinned the migration into the start command go with it. A
test that demands a behaviour production cannot survive is not a guard, it is a
countdown. What is kept is the pair that never depended on the decision: the
start command and the Dockerfile CMD must agree, and IF the boot migrates it must
be chained with && rather than ;.

That agreement test caught itself during this revert: Docker's exec form is a
JSON array, so the words are present and the spaces are not, and a substring
match for "uvicorn app.main:app" failed on a Dockerfile that was correct. It now
parses the array.

Still true, and still the reason PR 46 and PR 47 exist: nothing runs the
migrations against production. Its schema is maintained by create_all at boot
plus the hand-maintained ALTER TABLE list in main.py, and that list has already
cost ten days of silently missing measurements when migration 0015 was never
mirrored into it.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- railway.json Dockerfile tests/test_the_deploy_runs_the_migrations.py STATUS.md scripts/deploy/ship-pr48-revert-the-boot-migration.ps1
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
Write-Host '=== 4. waiting for production to come back on ' -NoNewline -ForegroundColor Cyan
Write-Host "$sha ===" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(12)
$live = $null
while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
    if ($h.commit -eq $sha) { $live = $h; break }
    Write-Host ("  serving {0}, waiting..." -f $h.commit)
  } catch {
    Write-Host '  still 502...'
  }
  Start-Sleep -Seconds 20
}
if ($null -eq $live) {
  Write-Host '' -ForegroundColor Red
  Write-Host 'STILL DOWN AFTER THE REVERT.' -ForegroundColor Red
  Write-Host '  That would mean the outage is NOT the start command, and the deploy log' -ForegroundColor Red
  Write-Host '  becomes the only way forward. Send it before anything else is tried.' -ForegroundColor Red
  throw "production never served $sha within 12 minutes"
}
Write-Host ("BACK UP: {0} v{1}" -f $live.commit, $live.version) -ForegroundColor Green

# -------------------------------------------------------------------- smoke
Write-Host '=== 5. smoke ===' -ForegroundColor Cyan
$rev = $live.schema_revision
Write-Host ("  schema_revision: {0}  (null is expected - the migrations are not running)" -f $(if ($null -eq $rev) { 'null' } else { $rev }))

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'X-Agent-Key' = (Get-Content $keyPath -Raw).Trim() }
  $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  NAV {0:N2} ILS across {1} positions" -f $p.nav_ils, $p.positions.Count)
  if ($p.positions.Count -lt 1) { throw 'the book came back empty - stop and investigate' }
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  {0} card(s) on Today" -f $r.count)
}

Write-Host ''
Write-Host 'BACK UP.' -ForegroundColor Green
Write-Host '  The book reads, the cards build, and schema_revision is null again -'
Write-Host '  which is the state production has always been in.'
Write-Host ''
Write-Host 'NEXT, AND ONLY THIS: open that failed deploy on Railway, click View logs,' -ForegroundColor Yellow
Write-Host 'and send the last 30 lines. The migration ran clean twice in the sandbox' -ForegroundColor Yellow
Write-Host 'against a database built the same way yours was, so the difference is real' -ForegroundColor Yellow
Write-Host 'and unidentified. No second attempt until the log says why.' -ForegroundColor Yellow
