# SHIP PR 25 - the equity screener starts returning stocks again.
#
#   .\scripts\deploy\ship-pr25-the-screener-can-see-again.ps1
#   .\scripts\deploy\ship-pr25-the-screener-can-see-again.ps1 -SkipTests
#
# THIS RESTORES A FEATURE THAT HAS BEEN DEAD IN PRODUCTION. Your buy ideas have
# been three commodities and three bonds. No stocks, on any scan.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags; run BOTH backends.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/providers/live.py",
    "tests/test_yahoo_fundamentals_are_gated.py",
    "scripts/smoke/smoke-the-screener-can-see-again.ps1",
    "scripts/deploy/ship-pr25-the-screener-can-see-again.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 25: the equity screener had been returning nothing, for months",
    "",
    "Found by the counter PR 24 added, on the first production scan after it",
    "deployed:",
    "",
    "    equity candidates the provider says have no fundamentals: AAPL, ABBV,",
    "    ADBE, AMD, AMZN, AVGO, BA, BAC, BRK-B, CAT, CHKP, COST, CRM, CVX, DIS,",
    "    ESLT, GE, GLOB, GOOGL, HD, HON, JNJ, JPM, KO, LLY, MA, MCD, META,",
    "    MNDY, MRK, MSFT, NEE, NICE, NKE, NVDA, ORCL, PEP, PFE, PG, QQQ, SCHD,",
    "    SMH, T, TEVA, UNH, V, VNQ, VOO, VTI, VXUS, VYM, VZ, WIX, WMT, XLE,",
    "    XLF, XLV, XOM",
    "",
    "All 58, every scan. Eran's buy ideas were three commodities and three",
    "bonds. Not one stock.",
    "",
    "ROOT CAUSE, PROVEN. Yahoo gated /v10/finance/quoteSummary behind a session",
    "cookie plus a matching crumb:",
    "",
    "    GET /v10/finance/quoteSummary/AAPL -> 401 {'code':'Unauthorized',",
    "                                               'description':'Invalid Crumb'}",
    "    GET /v8/finance/chart/AAPL         -> 200",
    "",
    "Prices come from chart, which is NOT gated. That is why NAV, the refresh",
    "job and every price on the screen looked perfectly healthy while the",
    "screener produced nothing at all.",
    "",
    "RULED OUT, by observation rather than reasoning: the rate limiter (a bucket",
    "rejection logs as UNREACHABLE and there were zero such lines); the circuit",
    "breaker (CLOSED, 0 failures, 61 succeeding keys); anything ticker-specific",
    "(AAPL, MSFT and NVDA fail identically).",
    "",
    "WHY IT SURVIVED SO LONG -- and this is the half worth reading.",
    "",
    "get_fundamentals returned None for a gated endpoint, a network error, AND a",
    "symbol Yahoo genuinely has nothing for. Its own docstring said so: 'Returns",
    "None (rather than raising) when the data is unavailable or the endpoint is",
    "gated, so the screener can simply skip the name.' _fundamentals_for then",
    "dropped the candidate either way, with no counter and no log, until PR 24.",
    "The app was DESIGNED to fail quietly here, and it did.",
    "",
    "It also had no test. get_fundamentals was added 2026-06-22; every fixture",
    "in test_market_data_live.py is a 'chart' payload, and test_screener.py",
    "exercises BuiltinMarketDataProvider, whose fundamentals are a sha1 of the",
    "ticker. The live path shipped untested and stayed untested. This PR is the",
    "first test it has ever had.",
    "",
    "TWO THINGS ARE FIXED AND THE SECOND MATTERS MORE.",
    "",
    "1. The cookie + crumb handshake. Fetched once and reused across a scan,",
    "re-fetched only on a 401, so a rotated crumb costs one retry rather than a",
    "whole scan of empty results.",
    "",
    "2. A TRANSPORT FAILURE AND AN EMPTY SYMBOL STOP BEING THE SAME ANSWER. Auth",
    "and transport failures now RAISE -- the screener records them as",
    "'unreachable' and logs the entire set -- and None means only 'Yahoo",
    "answered, there is nothing here'. Without this, (1) works until the day",
    "Yahoo changes the handshake again and then fails exactly as silently as",
    "before.",
    "",
    "VERIFIED LIVE against Yahoo, through the real provider code:",
    "",
    "    AAPL     pe=36.62  pb=43.44  roe=148.75  sector=Technology",
    "    TEVA     pe=60.73  pb=5.48   roe=9.69    sector=Healthcare",
    "    CHKP     pe=13.65  pb=5.20   roe=37.64   sector=Technology",
    "    NICE     pe=15.29  pb=42.36  roe=11.33   sector=Technology",
    "    QQQ      pe=30.63",
    "    TEVA.TA  pe=60.60",
    "",
    "FMP WAS EVALUATED AND REJECTED ON EVIDENCE, not preference. Eran supplied a",
    "key and asked for a registry split -- Yahoo prices, FMP fundamentals. Its",
    "free tier answers 'Special Endpoint : This value set for symbol is not",
    "available under your current subscription' for TEVA, CHKP and QQQ, and",
    "covers 13 of 15 US mega-caps (AVGO and PG blocked). It cannot see one",
    "Israeli name, one ETF, or one Tel Aviv listing. Yahoo covers all three.",
    "The key is NOT committed anywhere and belongs in Railway env only.",
    "",
    "NOT FIXED HERE. The other half of item 3: there is still not one Tel Aviv",
    "LISTING in the candidate universe -- all seven 'TASE_STOCKS' entries are",
    "NYSE/NASDAQ. And a caution measured while testing, which must be handled",
    "BEFORE .TA listings are added: P/B for a Tel Aviv listing is off by FX x",
    "100 (TEVA 5.48 vs TEVA.TA 1657.9). P/E survives because it is a",
    "same-currency ratio; P/B does not.",
    "",
    "Gate: SQLite 1428 passed / 0 skipped / 0 warnings; Postgres 1428 passed / 0",
    "skipped / 0 warnings; ruff check app tests clean. Both backends in a venv",
    "built from requirements.txt + requirements-dev.txt.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  YOUR BUY IDEAS GET STOCKS BACK." -ForegroundColor Green
Write-Host "  They have been three commodities and three bonds, on every scan," -ForegroundColor Green
Write-Host "  because Yahoo gated the fundamentals endpoint and the provider" -ForegroundColor Green
Write-Host "  swallowed the 401 into a None the screener silently skipped." -ForegroundColor Green
Write-Host ""
Write-Host "  Verified live through the real provider code:" -ForegroundColor Cyan
Write-Host "    AAPL pe=36.62   TEVA pe=60.73   CHKP pe=13.65" -ForegroundColor Cyan
Write-Host "    QQQ  pe=30.63   TEVA.TA pe=60.60" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PRICES ARE NOT TOUCHED. They come from a different Yahoo endpoint" -ForegroundColor Yellow
Write-Host "  that was never gated - which is exactly why this hid for so long." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Your FMP key is NOT in this commit and must never be. Yahoo needs" -ForegroundColor Magenta
Write-Host "  no key; FMP's free tier could not see TEVA, CHKP, QQQ or any Tel" -ForegroundColor Magenta
Write-Host "  Aviv listing, so it is not used." -ForegroundColor Magenta
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
python -m pytest -q tests/test_the_pins_are_installable_and_installed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  STOP. The versions installed here are not the versions" -ForegroundColor Red
    Write-Host "  requirements.txt pins, so nothing measured here can be" -ForegroundColor Red
    Write-Host "  quoted as a fact about the project." -ForegroundColor Red
    Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor Cyan
    exit 1
}
Write-Host "  This interpreter matches the pins." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. This PR restores a dead feature and"
    Warn "adds the first test its code path has ever had. Strange one to skip."
} else {
    Invoke-Suite -Focus @(
        "tests/test_yahoo_fundamentals_are_gated.py",
        "tests/test_market_data_live.py",
        "tests/test_screener.py")
    Write-Host ""
    Write-Host "  The new tests are OFFLINE - the handshake is mocked, so CI" -ForegroundColor Yellow
    Write-Host "  never depends on Yahoo being reachable or on the crumb format." -ForegroundColor Yellow
    Write-Host "  Then run test-postgres. Both backends must report 0 skipped." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, with 0 skipped? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Smoke - section 2 is the whole question"
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-the-screener-can-see-again.ps1 -Sha $sha" -ForegroundColor White
Write-Host ""
Write-Host "  Section 2 counts the EQUITY ideas. It has been 0. Anything above" -ForegroundColor Yellow
Write-Host "  zero means the screener can see stocks again." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today -> buy ideas. There should be STOCKS in there now, not" -ForegroundColor Cyan
Write-Host "    just commodities and bonds." -ForegroundColor Cyan
Write-Host "  * Your NAV must be unchanged. This ranks; it does not trade." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The crumb handshake is undocumented and Yahoo can change it." -ForegroundColor DarkYellow
Write-Host "    What is different now: a failure RAISES and is counted, so the" -ForegroundColor DarkYellow
Write-Host "    log names it on the first scan instead of hiding for months." -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe - the" -ForegroundColor DarkYellow
Write-Host "    other half of item 3." -ForegroundColor DarkYellow
Write-Host "  * P/B for a .TA listing is off by FX x 100 (TEVA 5.48 vs TEVA.TA" -ForegroundColor DarkYellow
Write-Host "    1657.9). Handle that BEFORE adding Tel Aviv listings." -ForegroundColor DarkYellow
Write-Host "  * The coverage pin rides on equity ideas, so it vanishes when" -ForegroundColor DarkYellow
Write-Host "    there are none. My bug, worth fixing next." -ForegroundColor DarkYellow
Write-Host "  * Item 2 has no factor tilt: nothing in the codebase derives one." -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is a live literal in 33 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
