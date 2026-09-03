# ship-pr45-intake-prices-are-shekels.ps1
# PR 45 - intake wrote a price it could not explain, and one of them was 100x.
#
# Commits three files plus STATUS.md, pushes to main, waits for Railway to serve
# the new SHA, then runs a READ-ONLY smoke.
#
# Pure ASCII, no backticks. Parse-checked with pwsh 7.4.6 before delivery.
#
# Already done in the sandbox, so this script does not repeat it:
#   SQLite   pytest -q  -> 1626 passed / 0 failed / 0 skipped
#   Postgres pytest -q  -> 1626 passed / 0 failed / 0 skipped
#   ruff check app tests -> clean
# All three new tests were confirmed RED against 712ff7c (PR 44) first.
#
# THE SMOKE CANNOT PROVE THIS ONE. The path fires only when a holding is added,
# and adding a Tel Aviv position to a live book to demonstrate a fix is not a
# smoke test. The tests are the proof; the smoke confirms the deploy is healthy
# and that no existing row moved.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# ---------------------------------------------------------------- guard rails
$files = @(
  'app/api/routes/intake.py',
  'app/services/intake_service.py',
  'tests/test_intake_prices_are_shekels_and_say_so.py',
  'STATUS.md'
)
foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "missing file: $f" }
}
$ir = @(Get-Content 'app/api/routes/intake.py').Count
$is = @(Get-Content 'app/services/intake_service.py').Count
if ($ir -lt 650) { throw "intake.py is $ir lines - expected ~702. Truncated write; do not commit." }
if ($is -lt 540) { throw "intake_service.py is $is lines - expected ~575. Truncated write; do not commit." }
Write-Host "intake.py: $ir lines; intake_service.py: $is lines" -ForegroundColor Green

# --------------------------------------------------------- NAV before/after
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "no agent key at $keyPath" }
$key = (Get-Content $keyPath -Raw).Trim()
$apiHeaders = @{ 'X-Agent-Key' = $key }
$before = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
$navBefore = [double]$before.nav_ils
Write-Host ("  NAV before deploy: {0:N2} ILS across {1} positions" -f $navBefore, $before.positions.Count)

# ------------------------------------------------------- fast local re-check
Write-Host '=== 1. ruff + the affected tests ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q tests/test_intake_prices_are_shekels_and_say_so.py tests/test_minor_unit_currencies.py tests/test_intake.py tests/test_accept_conserves_nav.py
if ($LASTEXITCODE -ne 0) { throw 'tests failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 2. commit ===' -ForegroundColor Cyan
$msg = @'
PR 45: intake prices are shekels, and say so

Two faults at one boundary, found together because they are the same mistake:
intake wrote a number without writing what the number meant.

THE 100x. to_major_units says "Use at every boundary" in its own docstring and
exactly one boundary used it. Yahoo quotes Tel Aviv in agorot and says so in the
CURRENCY CODE rather than in the price - TEVA.TA comes back as 11030.0 with
currency ILA, meaning 110.30 shekels. add_holding took .price and threw .currency
away.

What makes that worse than a wrong price is that it cancelled out at the moment
it was made: a 1,000 ILS budget booked 1000/11030 = 0.0907 shares at 11,030 each,
so NAV read 1,000. Nothing downstream disagreed either - MARKET_CCY["TASE"] is
"ILS" and the row carried no currency stamp to contradict it. Then the next
scheduled price refresh wrote the correct major-unit price of 110.30 against
those 0.0907 shares and the holding was worth 10 ILS. An error invisible until a
job that did nothing wrong exposed it.

THE PROVENANCE. upsert_positions did row.meta = meta - a wholesale replace of a
dict the price pipeline also writes to. Every re-intake dropped price_source,
price_currency and price_as_of, leaving a stored price with nothing saying what
currency it was in; the CSV path and broker_sync never wrote those keys at all. A
previous fix had noticed the replace and rescued exactly one key
(asset_class_source), which is how the next one gets lost.

_intake_meta() now merges, the way pricing_service always has, and then
re-stamps the price keys deliberately: a row whose price came from a CSV must not
still claim it came from Yahoo, and a repair flag about a price that no longer
exists is worse than no flag. That one helper covers three of the four writers -
add_holding, the CSV path and broker_sync all go through upsert_positions.

Scope note: V5 called this "four writers put a price on a holding without
normalising it". Only one of the four has a currency to normalise FROM; the CSV
and broker paths carry a bare number and no code, so for them the gap was
provenance, not normalisation. Fixed as it is rather than as it was described.

Tests: tests/test_intake_prices_are_shekels_and_say_so.py, three cases, all
confirmed FAILING against 712ff7c first. The share count is the assertion that
matters - 9.07 shares, not 0.0907 - because that is the number the next refresh
turns into a loss.

Gate: SQLite 1626 passed / 0 failed / 0 skipped; Postgres 1626 passed / 0 failed
/ 0 skipped; ruff check app tests clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/api/routes/intake.py app/services/intake_service.py tests/test_intake_prices_are_shekels_and_say_so.py STATUS.md scripts/deploy/ship-pr45-intake-prices-are-shekels.ps1
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
Write-Host '=== 5. smoke (read-only, asserted) ===' -ForegroundColor Cyan
$after = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 30
$navAfter = [double]$after.nav_ils
Write-Host ("  NAV after deploy:  {0:N2} ILS across {1} positions" -f $navAfter, $after.positions.Count)
foreach ($p in $after.positions) {
  Write-Host ("    {0,-6} qty {1,12} @ {2,10} {3}" -f $p.ticker, $p.quantity, $p.current_price, $p.currency)
}

$drift = [math]::Abs($navAfter - $navBefore)
if ($after.positions.Count -ne $before.positions.Count) {
  throw ("position count changed across the deploy: {0} -> {1}" -f $before.positions.Count, $after.positions.Count)
}
if ($drift -gt ($navBefore * 0.02)) {
  throw ("NAV moved {0:N2} ILS across a deploy that writes nothing - investigate before trusting it" -f $drift)
}

Write-Host ''
Write-Host 'SMOKE PASSED' -ForegroundColor Green
Write-Host ('  Same positions, NAV within 2% ({0:N2} ILS of market movement).' -f $drift)
Write-Host '  This PR only fires when a holding is ADDED, so there is nothing else'
Write-Host '  production can show without placing a trade. The three tests are the proof.'
Write-Host ''
Write-Host 'Then check the Actions tab: test, test-postgres and lint must all be green.' -ForegroundColor Yellow
