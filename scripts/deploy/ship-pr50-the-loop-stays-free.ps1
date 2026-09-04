# ship-pr50-the-loop-stays-free.ps1
# PR 50 - take the synchronous provider calls off the event loop.
#
# ROOT CAUSE, proven by reproduction rather than inferred from the source:
# guarded_quote / guarded_fx / guarded_history / guarded_events and
# ResearchAgent().scan() are plain "def" functions making SYNCHRONOUS network
# calls. Thirteen call sites invoked them straight from inside "async def". On
# one uvicorn worker that holds the event loop for the whole round trip, so
# while any one of them waits the app answers nobody.
#
# Measured in-process with the provider held at 2.0s:
#    quote:  2.00s   health:  2.01s   -> /health queued behind one quote
#
# RULED OUT - "httpx's ASGITransport serialises requests, so that measures the
# harness". Control run, same transport, the wait moved behind await
# asyncio.sleep:
#    awaited:  slow 2.01s   /health 0.00s
#    blocking: slow 2.00s   /health 2.01s
# The transport is concurrent. The loop was not.
#
# The worst site is not an endpoint: pricing_service.refresh_all_positions is
# the SCHEDULED refresh and runs in-process, so a refresh over N positions held
# the loop for N round trips - the app went unresponsive on a timer.
#
# Call sites found with an AST walk, not a grep. The first sweep matched "scan"
# on the method name and produced four false positives (LagEngine().scan is
# arithmetic over a list already in memory). Grep is what produced PR 42's
# non-fix.
#
# Gates already run in the sandbox on these exact bytes: 1637 passed on SQLite,
# 1637 on PostgreSQL (1634 of them without PR 49's deploy tests), ruff clean.
# The PostgreSQL database was dropped and recreated first - a dirty one has
# produced 46 false failures on card-generation state before.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the new guard, then the full suite ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_loop_stays_free_while_a_provider_answers.py
if ($LASTEXITCODE -ne 0) { throw 'the loop guard failed - do not push' }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 50: take the synchronous provider calls off the event loop

guarded_quote, guarded_fx, guarded_history, guarded_events and
ResearchAgent().scan() are plain "def" functions that make SYNCHRONOUS network
calls. Thirteen call sites invoked them directly from inside "async def". This
app runs on one uvicorn worker, so that holds the event loop for the whole
upstream round trip and while any one of them waits the app answers nobody.

Reproduced in-process against 6f3cc4f with the market provider held at 2.0s:

     quote:  2.00s  HTTP 200
    health:  2.01s  HTTP 200

RULED OUT - "httpx's ASGITransport serialises requests, so this measures the
harness rather than the app". A control run put the same 2.0s wait behind
await asyncio.sleep instead of time.sleep, same transport, same loop:

      awaited: slow  2.01s   /health  0.00s
     blocking: slow  2.00s   /health  2.01s

The transport is concurrent. The loop was not. That control ships as
test_the_harness_is_concurrent so a future httpx cannot turn the real test into
a tautology that passes for the wrong reason.

Not a new fault - a missed sweep. app/core/offload.py exists for exactly this
and its docstring carries the production measurement that motivated it: /plan
alone 0.38-0.52s, /plan issued while /recommendations was in flight 2.09 / 2.22
/ 3.05s, finishing the instant the loop was released. #15 Phase B moved the
AGENTS off the loop and left the provider calls made straight from handlers and
services.

Why it was silent: it never produced a wrong number, only a stalled one, and
from outside a stall is indistinguishable from a slow upstream. The worst site
is not an endpoint at all - pricing_service.refresh_all_positions is the
scheduled refresh, running in-process, so a refresh over N positions held the
loop for N round trips and the app went unresponsive on a timer rather than
under load.

Call sites found by AST walk. The first sweep matched "scan" on the method name
and produced four false positives: LagEngine().scan(observations) is arithmetic
over a list already in memory and belongs on the loop. The shipped guard
requires the receiver to be ResearchAgent(). Grep is what produced PR 42's
non-fix, and this class of change now gets a tree walk.

Thread safety was already settled rather than assumed: recommendations.py has
offloaded _buy_ideas - which hits the same providers through the same circuit
breakers - since #15 Phase B, so provider calls already run on worker threads
in production.

Fixed at all thirteen sites plus the Yahoo fallback in pricing_service, which
the guard does not catch and is the same call by another name.

Gates: 1634 on SQLite and 1634 on PostgreSQL with this change alone; 1637 and
1637 once PR 49's three deploy tests are in, which is what this script's own
run will show. ruff clean throughout.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/api/routes/market.py app/api/routes/intake.py app/services/portfolio_risk_service.py app/services/pricing_service.py app/services/strategy_service.py app/services/recommendations.py tests/test_the_loop_stays_free_while_a_provider_answers.py STATUS.md scripts/deploy/ship-pr50-the-loop-stays-free.ps1
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

# -------------------------------------------------------------------- smoke
Write-Host '=== 6. smoke ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) {
  Write-Host '  no scripts\.agent-key - skipping the authenticated smoke' -ForegroundColor Yellow
} else {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }

  $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  NAV {0:N2} ILS across {1} positions" -f $p.nav_ils, $p.positions.Count)
  if ($p.positions.Count -lt 1) { throw 'the book came back empty - stop and investigate' }

  $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  {0} card(s) on Today" -f $r.count)

  # The endpoints this PR actually touched.
  $q = Invoke-RestMethod -Uri "$base/api/v1/market/quote?ticker=VTI" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  /market/quote VTI -> {0} {1}" -f $q.price, $q.currency)
  if (-not $q.price) { throw '/market/quote returned no price' }

  $fx = Invoke-RestMethod -Uri "$base/api/v1/fx/rate?base=USD&quote=ILS" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  /fx/rate USD/ILS -> {0}" -f $fx.rate)

  $ev = Invoke-RestMethod -Uri "$base/api/v1/research/events" -Headers $apiHeaders -TimeoutSec 30
  Write-Host ("  /research/events -> {0} event(s)" -f $ev.count)

  $risk = Invoke-RestMethod -Uri "$base/api/v1/portfolio/risk" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  /portfolio/risk -> ok={0}" -f $risk.ok)
}

# --------------------------------------------------- the thing this PR is for
Write-Host '=== 7. is the loop actually free? ===' -ForegroundColor Cyan
Write-Host '  Firing /research/events (provider-bound) and /health together.'
$job = Start-Job -ScriptBlock {
  param($u) Invoke-RestMethod -Uri $u -TimeoutSec 60 | Out-Null
} -ArgumentList "$base/api/v1/research/events"
Start-Sleep -Milliseconds 150
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Invoke-RestMethod -Uri "$base/health" -TimeoutSec 30 | Out-Null
$sw.Stop()
Wait-Job $job | Out-Null
Remove-Job $job
Write-Host ("  /health answered in {0:N0} ms while a provider call was in flight" -f $sw.Elapsed.TotalMilliseconds)
Write-Host '  (this is indicative, not the proof - the proof is the in-process test,'
Write-Host '   which controls for the provider being cached and for network jitter.)'

Write-Host ''
Write-Host 'PR 50 SHIPPED.' -ForegroundColor Green
