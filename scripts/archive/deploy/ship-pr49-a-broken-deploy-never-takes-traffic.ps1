# ship-pr49-a-broken-deploy-never-takes-traffic.ps1
# PR 49 - make Railway refuse to promote a container that does not answer.
#
# RUN THIS BEFORE PR 50. It is the smaller change and it is the gate every
# later push goes through.
#
# The gap: nothing in the pipeline asked the new container whether it answers
# before the old one was retired. PR 47 shipped a start command production
# could not survive, every endpoint returned 502 for two hours, and Railway's
# card said "Deployment successful" the entire time - that card reflects
# build-and-deploy, not whether anything responds.
#
# Railway already had the gate and this service was not using it. From their
# healthcheck reference: Railway queries the endpoint until it gets a 2xx, and
# ONLY THEN makes the new deployment active and the previous one inactive; if
# it never passes, the deployment is marked failed after the timeout. So PR 47
# would have failed its own deploy and left the working container serving.
#
# healthcheckPath is /health/ready, not /health. /health answers 200 with no
# database at all, on purpose - schema_revision() swallows its own error
# because a liveness probe that fails on a side query is worse than the thing
# it reports. A probe that cannot fail cannot gate anything. The suite asserts
# that distinction rather than trusting it.
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
if (-not $deploy.healthcheckPath) { throw 'railway.json has no healthcheckPath' }
if ($deploy.healthcheckPath -eq '/health') {
  throw '/health answers 200 with no database - health-check /health/ready instead'
}
if ($deploy.startCommand -like '*alembic*') {
  throw 'railway.json migrates on boot again - that is PR 47 and it is not fixed yet'
}
Write-Host ("healthcheckPath: {0}  timeout: {1}s" -f $deploy.healthcheckPath, $deploy.healthcheckTimeout) -ForegroundColor Green

Write-Host '=== 1. ruff + the deploy tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_a_broken_deploy_never_takes_traffic.py tests/test_the_deploy_runs_the_migrations.py
if ($LASTEXITCODE -ne 0) { throw 'the deploy tests failed - do not push' }

Write-Host '=== 2. full suite ===' -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 49: a broken deploy could replace a working one, and did

Nothing in the pipeline asked the new container whether it answers before the
old one was retired. PR 47 shipped a start command production could not
survive; every endpoint returned 502 for two hours; and Railway's card read
"Deployment successful" throughout, because that card reflects build-and-deploy
and not whether anything responds. The only honest signal in the whole episode
was the ship script's own /health wait, and by the time it spoke the healthy
container was already gone.

Railway already had the gate and this service was not using it. From their
healthcheck reference: Railway queries the endpoint until it receives a 2xx,
and only then is the new deployment made active and the previous one inactive;
if it never passes, the deployment is marked failed after the timeout expires.
healthcheckPath and healthcheckTimeout are both real keys under deploy,
verified against the live railway.schema.json on 2026-09-04.

So PR 47 would have failed its own deploy and left the working container
serving. That outage was a bad start command shipped through a pipeline with no
gate, and only the first half of it has been addressed until now.

healthcheckPath is /health/ready, not /health. That distinction is the whole
point and is asserted rather than assumed: /health answers 200 with no database
at all, by design, because schema_revision() swallows its own error - a
liveness probe that fails on a side query is worse than the thing it reports. A
probe that cannot fail cannot gate anything.

tests/test_a_broken_deploy_never_takes_traffic.py pins three things: the key is
set, the path is a route this app actually serves (a 404 is not a 2xx, so a
typo would fail EVERY deploy), and the path goes red when the database is
unreachable.

scripts/deploy/verify-deploy.ps1 is the second half, and the reason it is one
file rather than a paragraph copied into each ship script: Railway's
healthcheck cannot tell you WHICH commit answered, because a healthcheck
passing on the previous container looks identical from outside. It waits for
/health.commit to equal the SHA just pushed, then asks /health/ready and
reports schema_revision. It throws rather than warns.

What this does not do: make the migration safe to retry. That root cause is
still unknown and the deploy log that held the answer is gone. What changes is
the cost of finding out - the next attempt fails its healthcheck and leaves
production up, instead of taking it down for two hours.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- railway.json tests/test_a_broken_deploy_never_takes_traffic.py scripts/deploy/verify-deploy.ps1 scripts/deploy/ship-pr49-a-broken-deploy-never-takes-traffic.ps1 STATUS.md
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

# ------------------------------------------------------------------- verify
# The first deploy that goes through the gate it just installed.
& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

Write-Host '=== 5. smoke ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  NAV {0:N2} ILS across {1} positions" -f $p.nav_ils, $p.positions.Count)
  if ($p.positions.Count -lt 1) { throw 'the book came back empty - stop and investigate' }
  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  {0} card(s) on Today" -f $r.count)
}

Write-Host ''
Write-Host 'PR 49 SHIPPED.' -ForegroundColor Green
Write-Host '  From here on, a container that cannot serve /health/ready never takes'
Write-Host '  traffic - Railway keeps the working one and marks the deploy failed.'
Write-Host '  Next: scripts\deploy\ship-pr50-the-loop-stays-free.ps1'
