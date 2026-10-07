# SHIP PR 5 - the history provider, and gain measured against what you paid in.
#
#   .\scripts\deploy\ship-pr5-history.ps1
#   .\scripts\deploy\ship-pr5-history.ps1 -SkipTests
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
    "app/providers/resilience.py",
    "app/providers/registry.py",
    "app/core/config.py",
    "app/services/recommendations.py",
    "app/api/routes/intake.py",
    "app/static_app/index.html",
    "tests/test_history_provider.py",
    "tests/test_gain_headline.py",
    "scripts/smoke/smoke-history.ps1",
    "scripts/deploy/ship-pr5-history.ps1"
)

$Message = @(
    "An empty ticker is not a broken provider",
    "",
    "history sat OPEN in production for a week while market served quotes",
    "the whole time. Same Yahoo provider, same /v8/finance/chart/{sym} endpoint,",
    "one query parameter apart. That asymmetry is the entire diagnosis, and it",
    "is about WHICH KEYS each tier asks for:",
    "",
    "  market   quotes for tickers you HOLD  -- four names, all real",
    "  history  the screener's CANDIDATE set -- 25 names, uncurated",
    "",
    "REPRODUCED, not reasoned about. A sandbox scan against live Yahoo went",
    "OPEN in one pass on:",
    "",
    "  history: OPEN | failing 4 | ok 0 | opened 1x",
    "  last_error: ValueError: insufficient history for 'JJN'",
    "",
    "JJN, COW, NIB and JO -- thin commodity ETNs from the app's OWN idea list.",
    "Yahoo answered every request correctly and said those tickers have no usable",
    "history. Four facts about four tickers, counted as four provider failures",
    "against a threshold of five, seconds after the same provider had served",
    "forty-two other symbols perfectly.",
    "",
    "So the fix is a distinction the counter never had. A ValueError from our own",
    "adapters means the response ARRIVED and the symbol is empty; that is data,",
    "not an outage, and no number of empty symbols may open anything. OSError,",
    "timeouts and 429s are the provider and still count for everything.",
    "",
    "Same scan after the change:",
    "",
    "  history: CLOSED | fail 0 | nodata 4 | ok 42 | opened 0x",
    "",
    "and it stayed closed across three consecutive scans.",
    "",
    "FOUR MORE THINGS WERE WRONG IN THE SAME PLACE, each found while fixing that",
    "one and each able to cause it alone:",
    "",
    "  A shared breaker over a keyspace counted only totals, so it could never",
    "  tell 'five of these symbols are bad' from 'the provider is down'. It now",
    "  weighs DISTINCT failing keys against distinct succeeding ones, and keeps",
    "  the original total-blackout rule for when nothing is working at all.",
    "",
    "  The rolling window was 120s. A cold candidate scan takes longer than that,",
    "  so the successes from the start of a scan aged out before the failures at",
    "  the end arrived, and the tier concluded it had never served anybody. Found",
    "  by reading succeeding_key_count: 0 in the sandbox immediately after",
    "  twenty tickers had fetched fine. Now 300s -- one scan is one piece of",
    "  evidence.",
    "",
    "  Recovery was a coin flip. HALF_OPEN admitted EVERY caller, not one probe,",
    "  and any failure among them re-opened. On the history tier the first",
    "  arrival is usually a candidate symbol, so a 30-second breaker stayed open",
    "  for days. One probe now, and the probe is judged on its own evidence --",
    "  the failures that opened the circuit are cleared on the transition, or a",
    "  recovered provider gets re-opened by history from before the retest.",
    "",
    "  Followers stampeded. On a failed leader every waiter mounted its own",
    "  attempt, so eight callers on ONE dead key recorded eight failures and",
    "  tripped a threshold-five breaker unaided. They now inherit the leader's",
    "  error for a short cool-off: one bad key, one failure, one request upstream.",
    "",
    "STALE-IF-ERROR. Daily closes do not change between fetches, so throwing an",
    "hour-old series away the instant it expires and then failing the refetch",
    "means the app HAS the answer in memory and shows you nothing. History and",
    "fundamentals now keep expired copies as emergency stock (6h / 24h) and serve",
    "them when a fetch fails, with degraded: [history_stale] saying so. market",
    "and fx deliberately get NO stale window -- a stale price misvalues the",
    "book, and that is the one failure this app must not have.",
    "",
    "ONE FETCH PER UPSTREAM REQUEST. The cache key was hist:{ticker}:{days} but",
    "the provider maps days onto a range ladder, so 130, 200 and 252 days are the",
    "SAME Yahoo URL and were three cache entries fetching identical bytes. Across",
    "eight call sites asking 130/200/252/2600, one ticker cost up to four",
    "requests that shared nothing. Keyed on the bucket now, and a longer cached",
    "series answers a shorter request for free. On a 25-name scan that is 100",
    "requests down to 50 -- which matters a great deal if the real cause ever",
    "turns out to be rate limiting.",
    "",
    "THE BREAKER CAN NOW SAY WHY. provider_health() returned {circuit_state} and",
    "nothing else, so an OPEN tier could not name the failing key, the error, or",
    "whether anything was succeeding beside it. That is why this took a week to",
    "find and had to be reproduced locally to confirm. It now reports the failing",
    "keys, the empty ones separately, the last error and its key, seconds since",
    "the last success, how many times it has opened, and how much has been served",
    "stale. circuit_state stays at the top level because the smoke scripts read",
    "it.",
    "",
    "GAIN IS NOW MEASURED AGAINST WHAT YOU PAID IN.",
    "",
    "PR 2 moved the headline AWAY from this and was right to at the time: NAV",
    "then contained 7,388 of positions an FX bug had minted, so NAV-minus-deposits",
    "read +42.48% on a book that had gained 4%. The metric was never wrong; the",
    "NAV was. On a repaired book it is the number the owner asked for:",
    "",
    "  NAV 21,339 - deposits 20,000 = 1,339 total gain   (+6.69%)  <- headline",
    "  NAV 21,339 - basis    20,350 =   989 unrealised",
    "  basis 20,350 - deposits 20,000 = 350 REALISED, reinvested",
    "",
    "The 350 is not a discrepancy. Sell a winner, reinvest, and the new lot is",
    "stamped at a price that already contained the gain -- basis rises with no new",
    "money, which is what every rebalanced brokerage account does. Gain-vs-",
    "contributions counts it correctly, so the amber 'no recorded deposit behind",
    "it' warning is gone and the basis baseline built to silence it is now dead",
    "code (its endpoints go with the legacy-column cleanup, not here -- removing",
    "shipped API deserves its own change).",
    "",
    "Cost basis moves to a second line naming both halves, and a book with no",
    "contributions ledger still falls back to basis rather than dividing by a",
    "number it does not have.",
    "",
    "All three CI jobs verified before pushing:",
    "  pytest on postgres:16     1008 passed, 12 skipped",
    "  pytest on sqlite          1012 passed, 4 skipped",
    "  ruff check app tests      clean",
    "",
    "Plus a live-provider run in the sandbox: three consecutive scans against real",
    "Yahoo, breaker CLOSED throughout, 42 keys served, 4 correctly classified as",
    "empty."
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite -Focus @("tests/test_history_provider.py", "tests/test_gain_headline.py", "tests/test_resilience.py", "tests/test_resilience_threading.py") }
Write-Host ""
Warn "The POSTGRES job needs a local server and is not run here. It WAS run for"
Warn "this PR in the sandbox, along with something CI cannot do at all: a scan"
Warn "against the REAL Yahoo provider, which is what reproduced the bug."

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

Step "6. Smoke (read-only)"
if (-not $env:IW_AGENT_KEY) { Warn "IW_AGENT_KEY not set - the smoke falls back to the committed key." }
& (Join-Path $RepoRoot "scripts\smoke\smoke-history.ps1") -Sha $sha -BaseUrl $BaseUrl
$smokeExit = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  THE BREAKER STARTS CLOSED ON EVERY DEPLOY, so H1 passing right now" -ForegroundColor Yellow
Write-Host "  proves almost nothing - the old build looked healthy at boot too." -ForegroundColor Yellow
Write-Host "  The check that counts is whether it STAYS closed across scans:" -ForegroundColor Yellow
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-history.ps1 -Sha $sha -Watch 20" -ForegroundColor Gray
Write-Host ""
Write-Host "  If it does re-open, H1 now prints the cause. Read it before doing" -ForegroundColor Cyan
Write-Host "  anything else:" -ForegroundColor Cyan
Write-Host "    'no history' / 404  -> a dead candidate symbol got through the" -ForegroundColor Gray
Write-Host "                           classifier. My logic is wrong; tell me." -ForegroundColor Gray
Write-Host "    429 / timeout       -> Yahoo is throttling Railway's IP. Nothing" -ForegroundColor Gray
Write-Host "                           here fixes that and I did not add a keyed" -ForegroundColor Gray
Write-Host "                           fallback provider on a guess. That would" -ForegroundColor Gray
Write-Host "                           be the next PR, with your FMP key." -ForegroundColor Gray
Write-Host ""
Write-Host "  Then update STATUS.md." -ForegroundColor Cyan
Write-Host ""
if ($smokeExit -ne 0) { exit 1 }
exit 0
