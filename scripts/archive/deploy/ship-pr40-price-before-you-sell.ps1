# ship-pr40-price-before-you-sell.ps1
# PR 40 - a buy that cannot be priced sells nothing.
#
# Commits the two code files and the two status files, pushes to main, waits for
# Railway to serve the new SHA, then runs a READ-ONLY smoke.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1612 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1612 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# The two new failing-first tests were confirmed RED against 4076ef1 first.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'app/services/recommendations.py',
  'tests/test_an_unpriceable_buy_sells_nothing.py',
  'STATUS.md',
  'STATUS-V2.md',
  'EXECUTION_PLAN_2026-09-03_v6.md',
  'EXECUTION_PLAN_2026-09-02_v5.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$lineCount = @(Get-Content 'app/services/recommendations.py').Count
if ($lineCount -lt 4000) { throw "recommendations.py is $lineCount lines - expected ~4111. Truncated write; do not commit." }
Write-Host "recommendations.py: $lineCount lines" -ForegroundColor Green

# ------------------------------------------------------- fast local re-check
# The full suite ran on both backends in the sandbox. This is the targeted
# re-check on your own box, ~1 minute.
Write-Host '=== 1. ruff + the new tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_an_unpriceable_buy_sells_nothing.py tests/test_accept_conserves_nav.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 40: a buy that cannot be priced sells nothing

Two executors sold first and asked for a price afterwards.

_apply_buy_funded ran every sell leg, realized the capital gains tax, and only
then quoted the instrument it was buying. When that quote failed it credited the
proceeds to cash and returned a note saying so - but the positions were already
gone and the card had claimed a purchase that never happened.

_apply_fee_swap had the identical order with more at stake: it sells the WHOLE
position, so a swap whose replacement could not be priced was a full exit the
card never proposed.

Both now quote the buy leg first and return {"aborted": true, "unpriced": <tk>}
with the book untouched. Ordering is the entire fix: delete_position commits, so
once the first sell lands there is nothing left to roll back.

This was live, not theoretical: Today carried two buy_funded cards when this was
written (USO 1,308 ILS and COMT 775 ILS). Commodity tickers are exactly the names
most likely to be missing from a given provider.

_apply_core_to_basket (PR 39) was already written this way and called the older
behaviour "survivable for a single swap". It is not - the rule that a card may
not claim a portfolio change it does not produce has no size threshold. Its
docstring is corrected here; it described a sibling that no longer behaves that
way.

Tests: tests/test_an_unpriceable_buy_sells_nothing.py, three cases. The two
refusal cases were confirmed FAILING against 4076ef1 first - the buy case sold
2 V and credited 2,696.48 ILS to cash for a purchase that never happened, the
swap case liquidated the position outright. The third guards the priceable path
and passes in both states by design: this is a reordering, not a new veto.

Gate: SQLite 1612 passed / 0 failed / 0 skipped; Postgres 1612 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Also in this commit: STATUS.md records PR 40 and the 09-02 core realign
(measured_pct_of_book 78.9 -> 97, Fixed Income drift 0.0), STATUS-V2.md is
banner-corrected, and the execution plans are tracked so a fresh clone can read
what STATUS.md points at.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/recommendations.py tests/test_an_unpriceable_buy_sells_nothing.py STATUS.md STATUS-V2.md EXECUTION_PLAN_2026-09-03_v6.md EXECUTION_PLAN_2026-09-02_v5.md scripts/deploy/ship-pr40-price-before-you-sell.ps1
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
# Railway deploys on push regardless of the CI result, so /health is the real
# signal. Check the Actions tab separately: a red run is an incident, not a
# queue, because the build is already out.
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
# READ-ONLY on purpose. The behaviour this PR changes only fires when a provider
# fails to quote, and that is not something to induce in production - the two
# tests prove the refusal. What this proves is that the card path still works.
Write-Host '=== 5. smoke (read-only) ===' -ForegroundColor Cyan
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }

$p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
Write-Host ("  NAV {0:N2} ILS across {1} positions, cash {2:N2}" -f $p.nav_ils, $p.positions.Count, $p.cash_ils)

$r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 60
Write-Host ("  {0} cards on Today:" -f $r.count)
foreach ($card in $r.recommendations) {
  $kind = if ($card.apply) { $card.apply.kind } else { '-' }
  Write-Host ("    [{0}] {1}" -f $kind, $card.title)
}

$mix = Invoke-RestMethod -Uri "$base/api/v1/mix" -Headers $apiHeaders -TimeoutSec 30
Write-Host ("  rebalance_required: {0}; FI drift {1}" -f $mix.rebalance_required, $mix.drift_percentage.'Fixed Income')

Write-Host ''
Write-Host 'PASS CRITERIA' -ForegroundColor Yellow
Write-Host '  1. /health serves the SHA above (already asserted).'
Write-Host '  2. The card list is non-empty and still shows buy_funded cards with a ticker'
Write-Host '     and an amount in the title - the executor change must not alter what is built.'
Write-Host '  3. NAV is unchanged from before the deploy (~20,950 ILS) - this PR moves no money.'
Write-Host '  4. rebalance_required is False and Fixed Income drift is 0 - the core is still aligned.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
