# ship-pr46-schema-from-nothing.ps1
# PR 46 - the schema can be built from nothing again.
#
# Commits two files plus STATUS.md, pushes to main, waits for Railway to serve
# the new SHA, then runs a READ-ONLY smoke.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1628 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1628 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
#   alembic upgrade head on an EMPTY postgres:16 database, twice, landing on
#   0020_solver_verdicts (head)
# Both new tests were confirmed RED against bddba29 (PR 45) first.
#
# THIS PR CHANGES NOTHING PRODUCTION DOES TODAY. The app builds its own schema
# on boot (AUTO_CREATE_TABLES=true) and never runs alembic, which is exactly why
# nobody found out the chain was broken. Flipping that setting is the NEXT PR,
# and it is only safe once these guards are live.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'alembic/versions/0018_transaction_ledger.py',
  'tests/test_the_migration_chain_runs_from_nothing.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$mg = @(Get-Content 'alembic/versions/0018_transaction_ledger.py').Count
if ($mg -lt 120) { throw "0018 is $mg lines - expected ~138. Truncated write; do not commit." }
Write-Host "0018_transaction_ledger.py: $mg lines" -ForegroundColor Green

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }
$before = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
$navBefore = [double]$before.nav_ils
Write-Host ("  NAV before deploy: {0:N2} ILS across {1} positions" -f $navBefore, $before.positions.Count)

# ------------------------------------------------------- fast local re-check
# The migration test is the one that matters here and it is fast (~4s): it runs
# the real alembic in a subprocess against a scratch SQLite file.
Write-Host '=== 1. ruff + the migration chain ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_the_migration_chain_runs_from_nothing.py
if ($LASTEXITCODE -ne 0) { throw 'the migration chain does not run - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 46: the schema can be built from nothing again

Reproduced before it was fixed, on postgres:16 against an empty database:

  $ alembic upgrade head
  sqlalchemy.exc.ProgrammingError: DuplicateColumnError:
    column "subject" of relation "transactions" already exists
  [SQL: ALTER TABLE transactions ADD COLUMN subject VARCHAR(255)]

The cause was already written down in this repo six times. 0013, 0014, 0015,
0016, 0019 and 0020 each carry a comment saying it: 0001_initial runs
Base.metadata.create_all, so a fresh database is built from TODAY's models and
already has every column the later revisions add. Each of those guards itself.
0018_transaction_ledger shipped with ten DDL statements and no guard, and one
unguarded revision is enough - alembic stops there and never reaches the rest.

A second break, which only the test found. With the duplicate columns guarded,
the chain still died on SQLite:

  sqlite3.OperationalError: near "ALTER": syntax error
  [SQL: ALTER TABLE transactions ALTER COLUMN position_id DROP NOT NULL]

SQLite has no ALTER COLUMN. On a fresh database the column is already nullable -
create_all built it from a model that has said so since this revision was
written - so the statement was not only unsupported, it was unnecessary. It now
asks first and falls back to batch_alter_table where a rebuild is really needed.

Guarded per statement rather than with one early return, because the database
this has to survive is not only the empty one: production's schema was built by
create_all on boot, so "has this column but not that index" is a realistic state
and a whole-revision skip would step over the half that is missing.

Why nobody noticed: AUTO_CREATE_TABLES=true in production is what made this
survivable and also what made it invisible. The app builds its own schema on
boot, so the migrations were never run and never found out they could not run.

Tests: tests/test_the_migration_chain_runs_from_nothing.py, two cases, both
confirmed FAILING against bddba29 first. They run the real alembic upgrade head
in a subprocess against a scratch SQLite file, nothing stubbed, and the second
runs it twice because a redeploy does. Verified separately on postgres:16:
upgrade head twice from empty, landing on 0020_solver_verdicts (head).

Next, and only in this order: flipping AUTO_CREATE_TABLES to false is the PR
after this one - the guards have to be live in production before the app stops
building its own schema. Then the Dockerfile.

Gate: SQLite 1628 passed / 0 failed / 0 skipped; Postgres 1628 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- alembic/versions/0018_transaction_ledger.py tests/test_the_migration_chain_runs_from_nothing.py STATUS.md scripts/deploy/ship-pr46-schema-from-nothing.ps1
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
Write-Host '=== 4. waiting for production to serve ' -NoNewline -ForegroundColor Cyan
Write-Host "$sha ===" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(10)
$live = $null
while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
    if ($h.commit -eq $sha) { $live = $h; break }
    Write-Host ("  serving {0}, waiting..." -f $h.commit)
  } catch {
    Write-Host '  /health not answering yet...'
  }
  Start-Sleep -Seconds 20
}
if ($null -eq $live) { throw "production never served $sha within 10 minutes - check the Railway deploy log" }
Write-Host ("LIVE: {0} v{1}" -f $live.commit, $live.version) -ForegroundColor Green

# -------------------------------------------------------------------- smoke
Write-Host '=== 5. smoke (read-only) ===' -ForegroundColor Cyan
$after = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
$navAfter = [double]$after.nav_ils
Write-Host ("  NAV after deploy:  {0:N2} ILS across {1} positions" -f $navAfter, $after.positions.Count)

if ($after.positions.Count -ne $before.positions.Count) {
  throw ("position count changed across the deploy: {0} -> {1}" -f $before.positions.Count, $after.positions.Count)
}
$drift = [math]::Abs($navAfter - $navBefore)
if ($drift -gt ($navBefore * 0.02)) {
  throw ("NAV moved {0:N2} ILS across a deploy that writes nothing - investigate" -f $drift)
}

Write-Host ''
Write-Host 'SMOKE PASSED' -ForegroundColor Green
Write-Host ('  Same positions, NAV within 2% ({0:N2} ILS of market movement).' -f $drift)
Write-Host '  This PR changes migrations only, and production does not run them yet -'
Write-Host '  it builds its own schema on boot. That is the problem the NEXT PR closes.'
Write-Host '  The proof for this one is the two tests, which run the real alembic.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
