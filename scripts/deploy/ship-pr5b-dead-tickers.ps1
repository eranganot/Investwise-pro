# SHIP PR 5b - stop recommending four ETNs that stopped trading in 2023.
#
#   .\scripts\deploy\ship-pr5b-dead-tickers.ps1
#   .\scripts\deploy\ship-pr5b-dead-tickers.ps1 -SkipTests
#
# Small: catalogue data, one screener guard, one abstain, tests, and a backlog
# entry. No new endpoints and no migration.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml. AND run the Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/commodities.py",
    "app/agents/screener_agent.py",
    "app/services/recommendations.py",
    "tests/test_live_candidates.py",
    "tests/test_screener.py",
    "STATUS.md",
    "scripts/deploy/ship-pr5b-dead-tickers.ps1"
)

$Message = @(
    "Stop recommending four ETNs that stopped trading in 2023",
    "",
    "Eran: 'I'm always getting the COW recommendation, though the COW price is",
    "irrelevant and not updating for more than 6 months.'",
    "",
    "Both halves of that were true, and the second explains the first. It was not",
    "bad luck that it was always COW.",
    "",
    "TWO INDEPENDENT DEFECTS THAT HAPPENED TO COMPOSE.",
    "",
    "1. FOUR DELISTED ETNs SAT IN THE BUY CATALOGUE. JJN, JO, NIB and COW are",
    "iPath ETNs Barclays has redeemed. Checked against Yahoo on 2026-08-25: the",
    "other 25 catalogue entries returned 251 daily bars ending on the previous",
    "close; those four returned NONE. commodities.py opens with the words 'real,",
    "live-priced, retail-investable instruments' -- a promise nothing verified.",
    "",
    "These are the same four symbols that opened the history circuit breaker in",
    "PR 5, so one dead-ticker problem was producing two outages that looked",
    "unrelated. strategy_profile.py has carried a comment saying COW is 'a",
    "commodity, and delisted' since before any of this; the catalogue went on",
    "offering it anyway, which is the part worth remembering.",
    "",
    "2. MISSING DATA SCORED AS STRENGTH. rank_commodities scores 2:1 trend",
    "against cost. _trend_pct returns None with no history. And _mean DROPS the",
    "Nones rather than penalising them -- so a candidate with no price data is",
    "scored ON ITS EXPENSE RATIO ALONE. COW's 0.45% is among the cheapest in the",
    "catalogue, so a dead ETN beat every live commodity that happened to be",
    "falling, deterministically, on every scan.",
    "",
    "IT REACHED HIM BY TWO DIFFERENT CARDS. The commodities-sleeve card via",
    "_commodity_recs, and the redeploy-idle-cash card, which fills an empty asset",
    "class from _buy_ideas. Fixing them one at a time would have left whichever",
    "path gets written next, so the property is asserted at the single source:",
    "universe.commodity_candidates() and full_universe() can never contain a",
    "delisted ticker.",
    "",
    "WHAT THIS CHANGES.",
    "",
    "The four are removed from CATALOG and recorded in a DELISTED map with",
    "reasons, guarded by tests in two files so re-adding one fails rather than",
    "surprises. Someone restoring 'Cocoa' to the agriculture list a year from now",
    "has no other way to learn that NIB is dead.",
    "",
    "screen_commodities DROPS candidates the provider cannot price instead of",
    "scoring them, so the next ETN to die is caught without waiting for someone",
    "to notice it on a card. A score computed from the factors that happen to be",
    "present is not comparable to one computed from all of them -- and here the",
    "missing factor was the one worth two thirds.",
    "",
    "_commodity_recs ABSTAINS when nothing can be ranked. It used to fall back to",
    "a hardcoded 'DBC', so a card headed 'Add a commodities sleeve' could name a",
    "ticker chosen by no process at all while reading exactly like the screened",
    "recommendation it usually is.",
    "",
    "test_screener.py asserted {'PALL','URA','LIT','JO','GDX','BNO'} <= tickers --",
    "that is, it asserted the app still offers a delisted product, and would have",
    "gone red the moment anyone checked. Fixed with the reason inline.",
    "",
    "TWO THINGS DELIBERATELY NOT FIXED HERE, both now in STATUS.md:",
    "",
    "  Liveness is checked NOWHERE, for any asset class. The equity universe has",
    "  the same exposure: 58 hand-maintained tickers, no freshness check. The real",
    "  fix is one rule at the boundary -- no card may name a ticker without a",
    "  close within N trading days.",
    "",
    "  There are no Fixed Income candidates AT ALL. full_universe() returns 58",
    "  Equities, 25 Commodities and zero bonds, so a Grow plan targeting 10% bonds",
    "  can never place that leg on any book, and the redeploy card reports an",
    "  unplaceable remainder every time. Found from the same screenshot.",
    "",
    "  _mean dropping None is a scoring bug wherever it appears. rank_equities",
    "  uses the same helper across fundamentals that are routinely missing.",
    "  Whether the answer is a neutral penalty, exclusion, or a per-candidate",
    "  confidence weight changes every equity score in the app, so it does not get",
    "  made inside a bug fix.",
    "",
    "  strategy_profile._COMMODITY still lists COW ON PURPOSE. Holding a dead ETN",
    "  and being SOLD one are different claims: a legacy position must still",
    "  classify as a commodity for allocation and risk, or it gets risk-scored as",
    "  a 32%-volatility single name. Tested."
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite -Focus @("tests/test_live_candidates.py", "tests/test_screener.py", "tests/test_commodities.py") }

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "4. Push, and let CI gate it"
Push-AndWatch

Step "5. Wait for Railway to actually serve it"
$live = ""
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -eq $sha) { break }
    } catch { $live = "(no answer)" }
    Write-Host "  serving $live, waiting for $sha ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if ($live -eq $sha) { Write-Host "  Railway is serving $sha" -ForegroundColor Green }
else { Warn "Still serving '$live' after 8 minutes. Check the Railway build log." }

Step "6. Verify the dead tickers are gone from the LIVE catalogue"
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$fail = 0
try {
    $cat = Invoke-RestMethod -Uri "$BaseUrl/api/v1/commodities" -Headers $ApiHeaders -TimeoutSec 60
    $tickers = @()
    foreach ($p in $cat.by_category.PSObject.Properties) { foreach ($c in $p.Value) { $tickers += $c.ticker } }
    Write-Host "  catalogue size: $($tickers.Count)" -ForegroundColor DarkGray
    $dead = @(@('COW','NIB','JO','JJN') | Where-Object { $tickers -contains $_ })
    if ($dead.Count -gt 0) {
        Write-Host "  FAIL  still offering: $($dead -join ', ')" -ForegroundColor Red
        $fail = 1
    } else {
        Write-Host "  PASS  no delisted ETN is offered ($($tickers.Count) live entries)" -ForegroundColor Green
    }

    $recs = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $ApiHeaders -TimeoutSec 240
    $named = @($recs.recommendations | Where-Object { $_.title -match 'COW|NIB|\bJO\b|JJN' })
    if ($named.Count -gt 0) {
        foreach ($n in $named) { Write-Host "  FAIL  card still names a dead ETN: $($n.title)" -ForegroundColor Red }
        $fail = 1
    } else {
        Write-Host "  PASS  no card names a delisted ETN" -ForegroundColor Green
    }
} catch {
    Write-Host "  FAIL  $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    $fail = 1
}

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES: neither the commodities-sleeve card nor" -ForegroundColor Cyan
Write-Host "  the redeploy-idle-cash card should mention COW. If the commodities" -ForegroundColor Cyan
Write-Host "  card is GONE entirely rather than naming something else, that is" -ForegroundColor Cyan
Write-Host "  the abstain working - it means nothing could be ranked, and the" -ForegroundColor Cyan
Write-Host "  honest answer to that is silence rather than a default." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE: the redeploy card will go on saying a" -ForegroundColor DarkYellow
Write-Host "  Fixed Income leg 'could not be placed'. There are no bond" -ForegroundColor DarkYellow
Write-Host "  candidates in the universe at all - 58 equities, 25 commodities," -ForegroundColor DarkYellow
Write-Host "  zero bonds. That is its own backlog item in STATUS.md." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
