# SHIP PR 1 - accepting a card may not create or destroy money.
#
#   .\scripts\deploy\ship-pr1-money-safety.ps1
#   .\scripts\deploy\ship-pr1-money-safety.ps1 -SkipTests    # only if you just ran them
#
# Runs the gate, commits the named files, pushes to main, waits for Railway to
# serve the new commit, then runs the PR's own smoke read-only.
#
# House rules this obeys (see CLAUDE.md and repo_shipping_conventions):
#   * never `git add -A` - investwise.db and frontend/node_modules ride along
#   * commit message via `git commit -F`, never a here-string
#   * the local gate MIRRORS ci.yml: pytest AND ruff on app AND tests
#   * untracked files are harmless and must not halt this script

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/intake_service.py",
    "app/services/recommendations.py",
    "app/services/strategy_service.py",
    "tests/test_accept_conserves_nav.py",
    "tests/test_backlog_fixes.py",
    "scripts/smoke/smoke-money-safety.ps1",
    "scripts/deploy/ship-pr1-money-safety.ps1"
)

$Message = @(
    "Accepting a card stops creating and destroying money",
    "",
    "Three write paths could change NAV without a trade. All three were",
    "invisible from outside: the book simply had different numbers in it.",
    "",
    "  _rebalance_to       summed quantity*price with NO FX, mixing USD",
    "                      holdings with the ILS cash row, and wrote the",
    "                      resulting class factors back into share counts.",
    "                      Measured on a production-shaped book: +4.8% of NAV",
    "                      per tap, no trade, no cash movement. Two taps ten",
    "                      seconds apart on 2026-08-21 are most of an",
    "                      unexplained +7,328. Now ILS throughout, and",
    "                      conservation is ASSERTED before the write lands -",
    "                      the docstring said NAV was held constant and only",
    "                      the docstring did.",
    "",
    "  _apply_buy_funded   routed its buy through upsert_positions, which SETS",
    "                      row.quantity. Topping up a ticker you already held",
    "                      replaced the position with just the shares bought:",
    "                      -4,388 in one accept. The live 'Swap into SCHD' card",
    "                      is exactly that shape.",
    "",
    "  _apply_redeploy_cash called upsert_positions with six arguments against",
    "                      its three-argument signature - a TypeError waiting",
    "                      for a leg in a ticker not already held.",
    "",
    "There were three implementations of 'top up a holding' and two were wrong,",
    "so there is now one: intake_service.buy_into_book(). It looks the ticker up",
    "across the WHOLE book rather than one account, which also closes the",
    "duplicate-row path where a buy landing in a second account was counted",
    "twice by NAV. _apply_fee_swap and _execute_funded_sleeve use it too.",
    "",
    "Both `max(0.0, cash - from_cash)` clamps are gone. Clamping the debit at",
    "zero AFTER buying the full amount is a purchase funded by arithmetic; the",
    "spend is now capped by the balance before the budget is set.",
    "",
    "tests/test_accept_conserves_nav.py is the invariant that did not exist:",
    "after an accept, NAV equals NAV before minus the tax the card reported.",
    "test_accept_honesty only ever proved the ADVISORY case. Prices are pinned",
    "in the fixture so a stub quote repricing a held position cannot read as a",
    "leak.",
    "",
    "Also fixes test_backlog_fixes::test_a_primary_without_timestamps_is_counted,",
    "which hardcoded 2026-08-10 inside a five-trading-day freshness window and",
    "began failing on its own a week later. The gate was already red on main."
)

# --------------------------------------------------------------------------- #
Step "1. Repo"
# --------------------------------------------------------------------------- #
Enter-Repo

# --------------------------------------------------------------------------- #
Step "2. Gate (mirrors ci.yml exactly)"
# --------------------------------------------------------------------------- #
if ($SkipTests) {
    Warn "-SkipTests set. CI is still the real gate; a red run blocks the deploy."
} else {
    Invoke-Suite -Focus @("tests/test_accept_conserves_nav.py", "tests/test_accept_honesty.py", "tests/test_card_claims.py")
}

# --------------------------------------------------------------------------- #
Step "3. Commit"
# --------------------------------------------------------------------------- #
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - continuing to verify what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

# --------------------------------------------------------------------------- #
Step "4. Push, and let CI gate it"
# --------------------------------------------------------------------------- #
Push-AndWatch

# --------------------------------------------------------------------------- #
Step "5. Wait for Railway to actually serve it"
# --------------------------------------------------------------------------- #
# Never assume a deploy landed. Poll /health and compare .commit to the SHA.
$live = ""
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -eq $sha) { break }
    } catch {
        $live = "(no answer)"
    }
    Write-Host "  serving $live, waiting for $sha ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if ($live -eq $sha) {
    Write-Host "  Railway is serving $sha" -ForegroundColor Green
} else {
    Warn "Still serving '$live' after 8 minutes. Check the Railway build log before running the smoke."
}

# --------------------------------------------------------------------------- #
Step "6. Smoke (read-only)"
# --------------------------------------------------------------------------- #
$smoke = Join-Path $RepoRoot "scripts\smoke\smoke-money-safety.ps1"
if (-not $env:IW_AGENT_KEY) {
    Warn "IW_AGENT_KEY not set - the smoke falls back to the committed key."
}
& $smoke -Sha $sha -BaseUrl $BaseUrl
$smokeExit = $LASTEXITCODE

Write-Host ""
Write-Host "  --- also re-run the suites this PR could disturb ---" -ForegroundColor Cyan
foreach ($s in @('smoke-p0.ps1', 'smoke-c3.ps1')) {
    $path = Join-Path $RepoRoot "scripts\smoke\$s"
    if (Test-Path $path) {
        Write-Host ""
        Write-Host "  --- $s ---" -ForegroundColor Cyan
        & $path -Sha $sha -BaseUrl $BaseUrl
        if ($LASTEXITCODE -ne 0) { Warn "$s reported failures - read the output above" }
    } else {
        Warn "$s not found, skipped"
    }
}

# --------------------------------------------------------------------------- #
Step "7. Done"
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  WHAT TO CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "    - M1.1 positions still sum to NAV, M1.2 one row per ticker" -ForegroundColor Gray
Write-Host "    - M2.1 will still SKIP: the cost-basis gap is PR 2 and PR 3, not this one" -ForegroundColor Gray
Write-Host "    - M6.1 SKIPs by default. To prove it end to end against the live book:" -ForegroundColor Gray
Write-Host "        .\scripts\smoke\smoke-money-safety.ps1 -Sha $sha -AllowExecute" -ForegroundColor Gray
Write-Host "      That ACCEPTS one real card. Read M3's NOTE lines first so you know" -ForegroundColor Gray
Write-Host "      which card is at the top of the board." -ForegroundColor Gray
Write-Host ""
Write-Host "  DO NOT reconcile the book yet. PR 3 does that, and it needs PR 2's" -ForegroundColor Yellow
Write-Host "  ledger in place or the correction goes unrecorded." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then update STATUS.md." -ForegroundColor Cyan
Write-Host ""
if ($smokeExit -ne 0) { exit 1 }
exit 0
