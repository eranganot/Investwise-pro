# ship-pr57-c8-one-author-for-the-schema.ps1
# PR 57 - C8. The app stops building its own schema, and the container stops
# running as root. Run after PR 56.1 (c9a171f).
#
# THIS IS THE DEPLOY WHERE THE SAFETY NET COMES OFF, so read this before you
# run it.
#
# At boot the app ran create_all from today's models plus a 23-statement
# ALTER TABLE IF NOT EXISTS list in main.py. For most of this repo's life that
# list, not alembic, was what actually reached production. PR 52 moved
# 'alembic upgrade head' into Railway's preDeployCommand and PR 54 proved it
# runs - schema_revision moved 0020 -> 0021 across that deploy and nothing in
# app/ writes alembic_version. After that, the boot-time builder is not a net;
# it is a way for a migration that did not run to be papered over from the
# models while the deploy reports healthy.
#
# MEASURED BEFORE FLIPPING IT, on postgres:16. A database built by
# 'alembic upgrade head' alone against one built by create_all plus the 23
# startup statements:
#
#   columns          272  vs  272
#   indexes           72  vs   72
#   constraints       46  vs   45   (the extra is alembic_version's own PK)
#   column defaults        identical
#
# Nothing is lost by turning it off.
#
# IF THE MIGRATIONS FAIL, this deploy fails - which is the point, and it is
# safe: preDeployCommand runs in its own container and must exit 0 before the
# app container starts (PR 52), and PR 49's healthcheck keeps the CURRENT
# deployment serving while that happens. You do not get a half-migrated site.
#
# THE CONTAINER HALF IS NOT BUILD-TESTED. There is no Docker daemon in these
# gates. Railway builds it; an image that fails to build never deploys, and one
# that builds but cannot serve /health/ready never takes traffic. The changes:
# a .dockerignore (there was none at all), a non-root appuser created after the
# COPYs, python:3.12.14-slim-bookworm instead of the moving 3.12-slim tag, and
# a stdlib HEALTHCHECK for local docker run.
#
# WHAT TO EXPECT AFTER: /health.builds_own_schema goes FALSE and
# schema_revision stays 0021_plan_cash_target - no new migration ships here.
# Step 5 checks both.
#
# Gates already run on these exact bytes: 1679 SQLite, 1679 PostgreSQL on a
# dropped and recreated database, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) { $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() } }

Write-Host '=== 0b. the schema before ===' -ForegroundColor Cyan
$h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 60
Write-Host ("  commit          {0}" -f $h.commit)
Write-Host ("  schema_revision {0}" -f $h.schema_revision)
if ($null -eq $h.schema_revision) {
  throw 'schema_revision is null - the migrations have never stamped this database. Do NOT turn off the boot-time builder until they have.'
}
Write-Host '  the migrations own this database, so the fallback can go' -ForegroundColor Green

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the C8 guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_migrations_are_the_only_thing_that_builds_the_schema.py tests/test_the_container_is_not_root_and_ships_no_secrets.py tests/test_startup_ddl_covers_migrations.py tests/test_the_migration_chain_runs_from_nothing.py
if ($LASTEXITCODE -ne 0) { throw 'the schema guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 57: C8 - the schema has one author, and the container is not root

TWO HALVES OF ONE IDEA: stop the deploy doing things nobody asked it to do.

1. AUTO_CREATE_TABLES defaults to false.

At boot the app ran create_all from today's models plus a 23-statement
ALTER TABLE ... IF NOT EXISTS list in main.py. For most of this repo's life
that list was not a convenience beside the migrations - it was the mechanism,
and test_startup_ddl_covers_migrations.py exists because it twice failed to
keep up: 0011's last_error, and 0015's benchmark_ticker, which degraded
silently for ten days behind a swallowed error.

PR 52 moved alembic upgrade head into Railway's preDeployCommand and PR 54
proved it runs - /health.schema_revision moved 0020_solver_verdicts to
0021_plan_cash_target across that deploy, and nothing in app/ writes
alembic_version. With the migrations running for real, the boot-time builder is
no longer a safety net. It is a way for a migration that did not run to be
papered over from the models while the deploy reports healthy and
alembic_version sits behind. The fallback's danger was always that it is
silent.

MEASURED BEFORE FLIPPING IT. Two databases on postgres:16 - one built by
alembic upgrade head on a virgin database, one by create_all plus the 23
startup statements:

                    alembic only    create_all + startup DDL
  columns                    272                         272
  indexes                     72                          72
  constraints                 46                          45
  column defaults                    identical

The one constraint difference is alembic_version's own primary key, which the
second database does not have because it has no alembic_version table. Nothing
is lost by turning it off.

Off by DEFAULT rather than off by env var, because an env var someone has to
remember to set is exactly the failure mode this removes - and the ops board
carries four-week-old items of that shape. tests/conftest.py turns it on
explicitly, and a fresh clone builds its schema the way production does:
alembic upgrade head, which test_the_migration_chain_runs_from_nothing.py
already proves works from an empty file.

Made observable: /health gained builds_own_schema. schema_revision says where
the migrations got to; it cannot say whether something ELSE is also writing the
schema, and a revision at head while create_all runs at boot cannot distinguish
"the migrations built this" from "the models did, and the migrations came
along". The production warning became an error, and no longer says "prefer
Alembic migrations" - that read as advice about a choice, and there is no
longer a choice.

THE NEW GUARD RUNS THE OTHER WAY ROUND. test_startup_ddl_covers_migrations.py
checks that every migration is mirrored into the startup list. C8 inverts which
one is authoritative, so
test_the_migrations_are_the_only_thing_that_builds_the_schema.py checks the
reverse: every ADD COLUMN in the startup list is a column alembic upgrade head
actually builds. Get that backwards and the failure is silent in the worst way
- the app comes up, every table exists, and one column that only the startup
list knew about is missing from a database the repo cannot reproduce. It also
guards itself: the parser reads a literal block out of main.py, so an assertion
that the block still yields at least five statements stops a refactor turning
the whole check vacuously green.

2. The container.

  .dockerignore   did not exist        ->  60 lines: .env, .git, scripts/,
                                           tarballs, tests
  user            root                 ->  appuser, uid 10001, after the COPYs
  base image      python:3.12-slim     ->  python:3.12.14-slim-bookworm
  HEALTHCHECK     none                 ->  stdlib urllib on /health/ready

On the missing .dockerignore, precisely: the Dockerfile COPYs only app/,
alembic/, alembic.ini and requirements.txt, so scripts/.agent-key never reached
a layer. Build CONTEXT is not the image, and that distinction is the whole
reason this was easy to leave undone. But the context was the entire working
tree, uploaded to the builder and cached, and one COPY . . written in a hurry
is the whole distance between "not in the image" and "in a layer, forever". The
key is named explicitly as well as covered by scripts/, so narrowing that line
later cannot silently re-admit it.

USER comes after the COPYs deliberately: the application files stay root-owned
and world-readable, so the process can read them and cannot rewrite them.
alembic upgrade head runs in this same image as the pre-deploy step and needs
no write access either - it writes to the database.

UNTESTED BY BUILD, and bounded. There is no Docker daemon in these gates, so
the image was not built here;
test_the_container_is_not_root_and_ships_no_secrets.py pins the properties
statically instead. The risk is bounded by PR 49: an image that fails to build
never deploys, and one that builds but cannot serve /health/ready never takes
traffic. The previous deployment keeps serving in both cases.

Gates: 1679 passed on SQLite, 1679 on PostgreSQL against a dropped and
recreated database, ruff check app tests alembic clean. Five of the nine new
assertions were confirmed RED against the pre-C8 tree; the other four are
structural pins that pass on both sides, including the column-coverage check
that makes the flip safe.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/core/config.py app/main.py app/api/routes/health.py
git add -- Dockerfile .dockerignore
git add -- tests/test_the_migrations_are_the_only_thing_that_builds_the_schema.py
git add -- tests/test_the_container_is_not_root_and_ships_no_secrets.py
git add -- STATUS.md scripts/deploy/ship-pr57-c8-one-author-for-the-schema.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
Write-Host '  if the pre-deploy migration fails, THIS DEPLOY FAILS and the' -ForegroundColor Yellow
Write-Host '  current deployment keeps serving. That is the designed outcome.' -ForegroundColor Yellow
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------------ does the schema have one author?
Write-Host '=== 5. the schema after ===' -ForegroundColor Cyan
$h2 = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 60
Write-Host ("  commit            {0}" -f $h2.commit)
Write-Host ("  schema_revision   {0}" -f $h2.schema_revision)
Write-Host ("  builds_own_schema {0}" -f $h2.builds_own_schema)

if ($h2.builds_own_schema -eq $false) {
  Write-Host '  THE APP NO LONGER BUILDS ITS OWN SCHEMA.' -ForegroundColor Green
  Write-Host '  alembic is the only thing that writes it, and schema_revision' -ForegroundColor Green
  Write-Host '  is now a complete answer rather than half of one.' -ForegroundColor Green
} elseif ($null -eq $h2.builds_own_schema) {
  Write-Host '  builds_own_schema is MISSING - this deploy is not serving PR 57' -ForegroundColor Red
} else {
  Write-Host '  STILL TRUE. AUTO_CREATE_TABLES is set in Railway and is' -ForegroundColor Red
  Write-Host '  overriding the new default. Remove that variable.' -ForegroundColor Red
}

if ($h2.schema_revision -ne $h.schema_revision) {
  Write-Host ("  NOTE: schema_revision moved {0} -> {1}. No migration ships in PR 57," -f $h.schema_revision, $h2.schema_revision) -ForegroundColor Yellow
  Write-Host '  so something else stamped it. Worth a look.' -ForegroundColor Yellow
} else {
  Write-Host ("  schema_revision unchanged at {0}, as expected" -f $h2.schema_revision)
}

$ready = Invoke-RestMethod -Uri "$base/health/ready" -TimeoutSec 60
Write-Host ("  ready: {0}, database {1}" -f $ready.status, $ready.database)

Write-Host ''
Write-Host 'PR 57 SHIPPED.' -ForegroundColor Green
Write-Host '  Still open from PR 56: .\scripts\ops\ops-check.ps1 - VAPID is the'
Write-Host '  one real TODO, and it is four weeks old.'
