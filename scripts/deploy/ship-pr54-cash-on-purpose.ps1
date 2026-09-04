# ship-pr54-cash-on-purpose.ps1
# PR 54 - the book can hold cash on purpose. Run after PR 53.
#
# YOUR DECISION: 7.5% cash as dry powder. This is the implementation, and the
# two judgement calls inside it are stated so you can overrule either.
#
#  1. THE RESERVE COMES OUT OF THE CORE, NEVER THE SLEEVES. Making room by
#     scaling everything by 0.925 would silently manage a sleeve you set at
#     38.4% of NAV to 35.5% - the defect book_target.py exists to record. So it
#     comes out of the 27% you delegated, not the 73% you specified:
#     sleeves 73% untouched, cash 7.5%, core strategy 19.5%.
#
#  2. THE FLOOR IS THE TARGET. A reserve routine funding may nibble into is not
#     a reserve, so cash_floor_pct now reads the same number. Cards may not fund
#     themselves out of the powder; deploying it is a deliberate act by you.
#     This is why spendable_cash is 0 AT target - that is the reserve working,
#     not a bug. Trims still fund buys.
#
# WHAT CHANGES ON SCREEN, and it is the opposite of today: against the new
# target the book is 4.6 points UNDER on cash (about 972 ILS) and 2.9 points
# OVER on Fixed Income. Today says nothing to do. After this it should say to
# raise about 972 ILS, mostly out of BND. That IS dry powder; it is not a
# surprise.
#
# Found on the way: cash_floor_pct read getattr(plan, "cash_floor_pct") - a
# column Plan has never had - so the override returned None on every call in
# production. Its test passed for months against a SimpleNamespace that
# fabricated the field. A fixture that invents what it is testing proves the
# branch, not the behaviour.
#
# THIS PR IS ALSO THE DECISIVE TEST OF PR 52. Migration 0021 is the first new
# revision since the pre-deploy step shipped. If that step runs,
# /health.schema_revision moves 0020_solver_verdicts -> 0021_plan_cash_target.
# If it does not, create_all plus the ALTER mirror still add the column and the
# stamp stays at 0020. The app works either way; the field says which happened.
# Step 6 asserts it.
#
# Gates already run on these exact bytes: 1650 on SQLite, 1650 on PostgreSQL
# (dropped and recreated first), ruff clean.
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

Write-Host '=== 0b. the revision production is stamped at, BEFORE ===' -ForegroundColor Cyan
$before = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
$revBefore = $before.schema_revision
Write-Host ("  commit {0}   schema_revision {1}" -f $before.commit, $(if ($null -eq $revBefore) { 'null' } else { $revBefore }))

if ($haveKey) {
  $m = Invoke-RestMethod -Uri "$base/api/v1/mix" -Headers $apiHeaders -TimeoutSec 30
  Write-Host '  the target as it stands (no Cash key at all):'
  Write-Host ("    {0}" -f ($m.target_allocation | ConvertTo-Json -Compress))
  Write-Host ("    current: {0}" -f ($m.current_allocation | ConvertTo-Json -Compress))
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard, the migration chain, then the full suite ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_book_can_hold_cash_on_purpose.py tests/test_funding_service.py tests/test_startup_ddl_covers_migrations.py tests/test_the_migration_chain_runs_from_nothing.py
if ($LASTEXITCODE -ne 0) { throw 'the cash/migration guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 54: the book can hold cash on purpose

Eran's decision, 2026-09-04: target 7.5% cash as dry powder - "so I'd be able
to buy something if there is a good opportunity". This is the implementation,
and the two judgement calls inside it are stated rather than buried.

What it replaces: cash was not in the book target at all, so its target was
zero by omission - the same silent-zero trap targets_for was written to avoid
one layer down. /api/v1/mix on c93ceb9 reported target_allocation
{"Equities": 0.892, "Fixed Income": 0.108} with current_allocation carrying
"Cash": 0.0292, a +0.0292 drift, and rebalance_required false. 618.65 ILS -
twice MIN_TRADE_ILS - sat there because a class the target never modelled
cannot drift.

It was quietly worse than "no opinion". CASH_FLOOR_PCT floors a Grow book at
3%, so spendable_cash was already returning zero: the app had spent the reserve
down to its own limit and nothing on any screen called that a decision.

JUDGEMENT CALL 1 - the reserve comes out of the CORE, never the sleeves.
compose builds the target as sleeves + core, core being the remainder. Making
room by scaling everything by 0.925 would silently manage a sleeve the user set
at 38.4% of NAV to 35.5%, which is the defect book_target's own docstring
exists to record: "The app was telling him to destroy a sleeve it had set
itself." So the reserve comes out of the part of the book he delegated, not the
part he specified. On the live book: sleeves 73% untouched, remainder 27% ->
cash 7.5% + core 19.5%, and bal_6040 at 60/40 across 19.5 gives Equities 11.7
and Fixed Income 7.8, for a target of 0.847 / 0.078 / 0.075 summing to 1.

JUDGEMENT CALL 2 - the floor IS the target. A reserve routine funding may
nibble into is not a reserve. cash_floor_pct now reads plans.cash_target_pct,
so cards may not fund themselves out of the powder and deploying it is a
deliberate act. This is why spendable_cash correctly returns 0 AT target;
trims still fund buys, the reserve does not.

A dead branch found on the way: cash_floor_pct read
getattr(plan, "cash_floor_pct") - a column Plan has never had - so it returned
None on every call in production and the objective table was the only answer
the function could give. Its test passed for months against a SimpleNamespace
that fabricated the field. A fixture that invents what it is testing proves the
branch, not the behaviour. Both now name the real column.

What this changes on screen, in the opposite direction from today: against the
new target the book is 4.6 points UNDER on cash (about 972 ILS) and 2.9 points
OVER on Fixed Income. Today says there is nothing to do; with the reserve it
should say to raise about 972 ILS, mostly out of BND. That is what asking for
dry powder means.

Storage: plans.cash_target_pct, a PERCENTAGE (7.5) like every other _pct column
on that table, converted to a fraction exactly once in book_target. Nullable,
and null is not zero - null means nobody has been asked, zero means someone
chose none. Migration 0021, guarded, and mirrored into the main.py ALTER list,
which is what actually reaches production until the pre-deploy step is
confirmed. Bounded at the API (ge=0, le=50) so a 750 typed for 7.5 is a 422
rather than a clamp nobody sees.

This PR is also the decisive test of PR 52. Migration 0021 is the first new
revision since the pre-deploy step shipped. If that step runs,
/health.schema_revision moves 0020_solver_verdicts -> 0021_plan_cash_target. If
it does not, create_all and the ALTER mirror still add the column and the stamp
stays at 0020 - the app works either way, and the field says which happened.

Gates: 1650 passed on SQLite, 1650 on PostgreSQL, ruff check app tests alembic
clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/models/tables.py app/services/book_target.py app/services/plan_service.py
git add -- app/services/funding_service.py app/api/routes/plan.py app/main.py
git add -- alembic/versions/0021_plan_cash_target.py
git add -- tests/test_the_book_can_hold_cash_on_purpose.py tests/test_funding_service.py
git add -- STATUS.md scripts/deploy/ship-pr54-cash-on-purpose.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------------- the PR 52 question, settled
Write-Host '=== 5. DID THE PRE-DEPLOY STEP RUN? ===' -ForegroundColor Cyan
$after = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 20
$revAfter = $after.schema_revision
Write-Host ("  schema_revision before: {0}" -f $(if ($null -eq $revBefore) { 'null' } else { $revBefore }))
Write-Host ("  schema_revision after : {0}" -f $(if ($null -eq $revAfter) { 'null' } else { $revAfter }))
Write-Host ''
if ($revAfter -eq '0021_plan_cash_target') {
  Write-Host '  YES. The pre-deploy step ran and stamped the new revision.' -ForegroundColor Green
  Write-Host '  PR 52 works, its log is just not in the pane we were reading.'
  Write-Host '  AUTO_CREATE_TABLES=false is now unblocked.' -ForegroundColor Green
} elseif ($revAfter -eq $revBefore) {
  Write-Host '  NO. The stamp did not move, so alembic did not run against' -ForegroundColor Yellow
  Write-Host '  production. The column is there anyway - create_all plus the' -ForegroundColor Yellow
  Write-Host '  ALTER mirror in main.py did it, which is exactly why that mirror' -ForegroundColor Yellow
  Write-Host '  is still the mechanism and why AUTO_CREATE_TABLES must stay ON.' -ForegroundColor Yellow
  Write-Host '  preDeployCommand is being ignored; healthcheckPath from the same' -ForegroundColor Yellow
  Write-Host '  file is not, so check the service settings in the dashboard for' -ForegroundColor Yellow
  Write-Host '  a Pre-Deploy Command field overriding it.' -ForegroundColor Yellow
} else {
  Write-Host ("  UNEXPECTED: {0}. Stop and look." -f $revAfter) -ForegroundColor Red
}

# ------------------------------------------------------ set the reserve itself
Write-Host '=== 6. set the reserve to 7.5% ===' -ForegroundColor Cyan
if (-not $haveKey) {
  Write-Host '  no scripts\.agent-key - set it by hand: PUT /api/v1/plan {"cash_target_pct": 7.5}' -ForegroundColor Yellow
} else {
  $body = @{ cash_target_pct = 7.5 } | ConvertTo-Json
  $p = Invoke-RestMethod -Uri "$base/api/v1/plan" -Method Put -Headers $apiHeaders -Body $body -ContentType 'application/json' -TimeoutSec 30
  Write-Host ("  plan.cash_target_pct = {0}" -f $p.cash_target_pct)
  if ($p.cash_target_pct -ne 7.5) { throw 'the plan did not take the reserve' }

  $m2 = Invoke-RestMethod -Uri "$base/api/v1/mix" -Headers $apiHeaders -TimeoutSec 30
  Write-Host '  the target now:'
  Write-Host ("    {0}" -f ($m2.target_allocation | ConvertTo-Json -Compress))
  Write-Host ("    drift:   {0}" -f ($m2.drift_percentage | ConvertTo-Json -Compress))
  Write-Host ("    rebalance_required: {0}" -f $m2.rebalance_required)

  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 90
  Write-Host ("  {0} card(s) on Today" -f $r.count)
  foreach ($c in $r.recommendations) { Write-Host ("    - {0}" -f $c.title) }
}

Write-Host ''
Write-Host 'PR 54 SHIPPED.' -ForegroundColor Green
Write-Host '  Expect a card asking you to raise about 972 ILS, mostly out of BND.'
Write-Host '  That is the reserve being filled, not a new opinion about bonds.'
