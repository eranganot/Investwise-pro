# ship-pr56-1-the-call-site-the-withdrawal-missed.ps1
# PR 56.1 - the withdrawal missed a call site, and production said so.
# Run after PR 56 (f0e570c).
#
# PR 56's own step 5 caught this. Everything about the withdrawal landed -
# the plan headline went 68 -> 45.4, volatility_window appeared, and
# volatility_exceeds_assumption correctly went loud again - but the key
# portfolio_volatility_pct_strategy_longrun was STILL on the payload.
#
# ROOT CAUSE, reproduced rather than argued. PR 55.1 published the figure at
# TWO sites under TWO names:
#
#   _portfolio_stats  (producer)  ->  volatility_strategy_longrun
#   _plan_dict:186    (mapper)    ->  portfolio_volatility_pct_strategy_longrun
#
# PR 56 removed the producer. stats.get("volatility_strategy_longrun") then
# returned None and line 186 assigned it unconditionally, so /plan carried the
# retracted number's NAME with a null value. Reproduced by calling _plan_dict
# directly: KEY PRESENT True, VALUE None, headline 45.4.
#
# RULED OUT: "the deploy did not land" - killed by the same output, since the
# headline, the window label and the flag are all PR 56 behaviour. "the check
# is wrong" - killed by the local reproduction. "something else emits it" -
# grep across app/ finds exactly one site.
#
# BLAST RADIUS: one null key on /plan, one deploy, no reader - nothing in
# app/static_app references either name. The harm is epistemic: a key that only
# ever holds null reads as "we measure this and it came back empty", which is
# the exact claim PR 56 existed to withdraw.
#
# WHY IT WAS SILENT: PR 56's test asserted on the STATS DICT, one layer below
# the rename. A test beneath the API boundary cannot see a key the API layer
# adds. Same shape as PR 43. The new test asserts on the payload _plan_dict
# actually returns, matching any key containing "longrun" or "conditional"
# rather than one spelling.
#
# Gates already run on these exact bytes: 1671 SQLite, 1671 PostgreSQL on a
# dropped and recreated database, ruff clean. The new assertion was confirmed
# RED against the shipped f0e570c bytes.
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

if ($haveKey) {
  Write-Host '=== 0b. the leftover key, before ===' -ForegroundColor Cyan
  $pl = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60
  $p = $pl.PSObject.Properties['portfolio_volatility_pct_strategy_longrun']
  if ($null -ne $p) {
    Write-Host ("  present, value: {0}" -f $(if ($null -eq $p.Value) { 'null' } else { $p.Value })) -ForegroundColor Yellow
  } else {
    Write-Host '  already absent - nothing to do, stop here' -ForegroundColor Green
  }
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the payload guard, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_book_volatility_is_not_rescaled_by_exposure.py
if ($LASTEXITCODE -ne 0) { throw 'the payload guard failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 56.1: the withdrawal missed a call site, and production said so

PR 56's own deploy check found this. Everything about the withdrawal landed on
f0e570c - the plan headline moved from the conditional figure back to the blend
(68 -> 45.4), volatility_window appeared on the payload, and
volatility_exceeds_assumption correctly went loud again - but
portfolio_volatility_pct_strategy_longrun was still there.

ROOT CAUSE, reproduced rather than argued. PR 55.1 published the figure at two
sites under two names:

  _portfolio_stats  (the producer)  ->  volatility_strategy_longrun
  _plan_dict:186    (the mapper)    ->  portfolio_volatility_pct_strategy_longrun

PR 56 removed the producer. stats.get("volatility_strategy_longrun") then
returned None, and line 186 assigned it unconditionally, so /plan went on
carrying the retracted number's NAME with a null value. Reproduced locally by
calling _plan_dict directly: KEY PRESENT True, VALUE None, headline 45.4.

Ruled out, each by the observation that killed it:

  * "the deploy did not land" - killed by the same output. The headline read
    45.4 not 68, volatility_window was present, and the flag had flipped to
    true. All three are PR 56 behaviour.
  * "the deploy check is wrong" - killed by the local reproduction. The key
    really is in the dict; PowerShell's PSObject.Properties[...] returns an
    entry for a present-but-null property, which is correct.
  * "something else emits it" - grep across app/: exactly one site, plan.py:186.

BLAST RADIUS: one null key on /plan, for one deploy, with no reader. Nothing in
app/static_app references either name and nothing sizes a trade from it. The
harm is epistemic rather than numeric - a key that only ever holds null reads
as "we measure this and it came back empty", which is precisely the claim PR 56
existed to withdraw.

WHY IT WAS SILENT, which is the part worth keeping. PR 56's test asserted
"volatility_strategy_longrun" not in out - on the STATS DICT, one layer below
the rename. A test beneath the API boundary cannot see a key the API layer
adds. Same shape as PR 43, where PR 42's fix survived at a second call site.
test_the_plan_payload_itself_carries_no_trace_of_the_withdrawn_number now
asserts on the payload _plan_dict actually returns, and matches on any key
containing "longrun" or "conditional" across the whole payload rather than on
one spelling, so the next retraction cannot leave a differently named survivor.

Gates: 1671 passed on SQLite, 1671 on PostgreSQL against a dropped and
recreated database, ruff check app tests clean. The new assertion was confirmed
RED against the shipped f0e570c bytes before the fix went in.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/api/routes/plan.py
git add -- tests/test_the_book_volatility_is_not_rescaled_by_exposure.py
git add -- STATUS.md scripts/deploy/ship-pr56-1-the-call-site-the-withdrawal-missed.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------------------- is the name gone?
if ($haveKey) {
  Write-Host '=== 5. the payload after ===' -ForegroundColor Cyan
  $pl2 = Invoke-RestMethod -Uri "$base/api/v1/plan" -Headers $apiHeaders -TimeoutSec 60

  $leaked = @($pl2.PSObject.Properties |
              Where-Object { $_.Name -like '*longrun*' -or $_.Name -like '*conditional*' } |
              ForEach-Object { $_.Name })

  if ($leaked.Count -eq 0) {
    Write-Host '  no longrun / conditional key anywhere on /plan' -ForegroundColor Green
  } else {
    Write-Host ('  STILL PRESENT: ' + ($leaked -join ', ')) -ForegroundColor Red
    Write-Host '  a third call site exists - grep app/ before shipping anything else' -ForegroundColor Red
  }

  Write-Host ("  plan volatility  {0,8}%" -f $pl2.portfolio_volatility_pct)
  Write-Host ("  window           {0}" -f $pl2.assumptions.volatility_window)
}

Write-Host ''
Write-Host 'PR 56.1 SHIPPED.' -ForegroundColor Green
Write-Host '  The ops items from PR 56 are still open: .\scripts\ops\ops-check.ps1'
Write-Host '  Branch protection first - it is the only one that prevents a class of incident.'
