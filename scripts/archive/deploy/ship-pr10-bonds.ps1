# SHIP PR 10 - the app could not buy a bond, on any book, ever.
#
#   .\scripts\deploy\ship-pr10-bonds.ps1
#   .\scripts\deploy\ship-pr10-bonds.ps1 -SkipTests
#
# No migration. Section 3 records what the live book is being told BEFORE the
# push, because the thing being fixed is a sentence that has appeared on every
# scan since the app was written.
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
    "app/services/fixed_income.py",
    "app/services/universe.py",
    "app/services/allocation_mix.py",
    "app/services/recommendations.py",
    "app/agents/screener_agent.py",
    "app/engines/screener_engine.py",
    "tests/test_bonds_are_buyable.py",
    "scripts/smoke/smoke-bonds.ps1",
    "scripts/deploy/ship-pr10-bonds.ps1",
    "STATUS.md"
)

$Message = @(
    "The app could not buy a bond, on any book, ever",
    "",
    "universe.full_universe() returned 83 candidates: 64 Equities, 19",
    "Commodities and ZERO Fixed Income - on an app whose objective presets have",
    "wanted bonds since the first commit:",
    "",
    "    Grow      80 / 10 / 10        Preserve  30 / 60 / -",
    "    Balanced  60 / 30 / 10        Income    40 / 50 / 10",
    "",
    "So _redeploy_cash_recs searched _buy_ideas for a candidate whose class was",
    "'Fixed Income', never found one, and filed the money under 'unplaced' with",
    "the reason 'you hold no Fixed Income and no candidate was available' -",
    "accurately, on every scan, for every book.",
    "",
    "PR 9 is what made it urgent rather than merely wrong. The live core targets",
    "40% Fixed Income of a 36% core = 14.4% of the book, about 3,054 ILS, and the",
    "rebalance card finally asked for the right number while the app still could",
    "not name one thing to buy.",
    "",
    "Now: 'Redeploy 3,115 of idle cash -> 1,917 into BND; 1,198 into CANE', with",
    "no unplaced leg left. Verified end to end on a book rebuilt to match his.",
    "",
    "WHAT A BOND CATALOGUE CARRIES THAT AN EQUITY ONE DOES NOT.",
    "",
    "DURATION, because it is the entire risk: SGOV moves ~0.1% when rates move 1%,",
    "TLT moves ~16%. Ranking them together on price alone is meaningless. CREDIT,",
    "separately, because credit risk is EQUITY risk wearing a bond's name - high",
    "yield fell WITH stocks in 2008, 2020 and 2022, so a book buying ballast",
    "against a 64% leveraged-equity sleeve and getting handed HYG has bought more",
    "of what it was hedging. And CAVEATS: every fund is USD-denominated, so on an",
    "ILS book the safe asset carries FX risk.",
    "",
    "All 24 entries verified against the live Yahoo provider on 2026-08-25: 251",
    "daily bars each, ending on the previous close. Stated durations correlate",
    "with measured annualised volatility at r = 0.94 - which is the evidence the",
    "metadata is sane, and the reason it is still never quoted AS a measurement.",
    "",
    "THREE BUGS FOUND BY BUILDING ON THE EXISTING CODE.",
    "",
    "1. ELEVEN BOND ETFs WOULD HAVE BEEN COUNTED AS EQUITIES. classify() falls",
    "through to 'Equities' for anything it does not recognise, and",
    "FIXED_INCOME_TICKERS was missing VGSH, VGIT, VGLT, USFR, SCHP, VTIP, VCSH,",
    "VCIT, USHY, EMB and VTEB - every Vanguard and Schwab ticker, which is to say",
    "all the cheap ones. The leg would have looked filled while the gap it was",
    "meant to close went untouched. The commodity classify bug PR 4 fixed, one",
    "asset class over.",
    "",
    "2. _pct_ranks DID NOT HANDLE TIES, AND IT IS SHARED BY EVERY SCREEN. sorted()",
    "breaks ties by list position, so eight funds all charging 0.03% were handed",
    "percentiles from 100 down to 70 BY THEIR ORDER IN A DATA FILE. Moving a line",
    "changed the recommendation. Now each run of equal values takes the mean of",
    "the ranks it spans; rank_equities and rank_commodities inherit the fix.",
    "",
    "3. A RATE-LIMITED CALL WAS TREATED AS A DELISTED TICKER. PR 5b's liveness fix",
    "dropped any candidate whose history call raised - but CircuitOpenError and",
    "RateLimitedError say nothing about the ticker. Reproduced while building",
    "this: ranking bonds three times in one process rate-limited the later calls,",
    "twenty of twenty-four candidates silently vanished, and all three rankings",
    "came back IDENTICAL and wrong, including a 6-year fund recommended for a",
    "0.3-year mandate. The ranking changed with transport luck and nothing said",
    "so. The resilience tier already draws the line (is_no_data - a ValueError",
    "means the provider ANSWERED); this stops throwing that answer away, in both",
    "screens.",
    "",
    "THE RANKING, AND WHY IT IS NOT THE COMMODITY RANKING.",
    "",
    "rank_commodities scores 2:1 on trailing momentum. Copying it was the obvious",
    "move and it is backwards: a bond fund's trailing return is almost entirely",
    "'what rates did', so ranking on it buys the MOST duration right after the",
    "move that rewarded it - maximum sensitivity, worst entry.",
    "",
    "The first attempt scored duration fit against cost as percentiles, 2:1. It",
    "failed on a real case: SGOV, a near-exact match for a cash-like mandate, lost",
    "to a fund with NINETEEN TIMES its rate sensitivity on six basis points of",
    "fee, because percentile ranks discard magnitude. Every weight ratio that",
    "fixed it was reverse-engineered from the answer I wanted.",
    "",
    "The structure was wrong, not the weights. Missing the duration is a RISK",
    "error; paying more is a RETURN error; for ballast the risk error is the whole",
    "point of holding the thing. So duration is a GATE and cost ranks what",
    "survives it, with fit as the tiebreak:",
    "",
    "    ~6y (his core) -> BND / AGG      the aggregate funds, 0.03%",
    "    ~2y            -> VGSH           short Treasury, not a 6-year fund",
    "    ~0.3y          -> SGOV           the case that forced the redesign",
    "    ~15y           -> VGLT           same job as TLT at 0.03% vs 0.15%",
    "",
    "A mandate nothing matches widens the band and flags every pick",
    "'outside-mandate-band' rather than silently returning a 6-year fund for a",
    "30-year ask.",
    "",
    "US MUNICIPALS ARE WITHHELD FROM A NON-USD BOOK. VTEB charges 0.03% at 6.5",
    "years, so it beats most Treasuries on every factor this ranker measures. What",
    "it sells is exemption from US federal income tax - and that exemption is",
    "PRICED INTO THE YIELD, so a non-US holder takes the lower yield and gets",
    "nothing back. Gated on plan.currency, not on an assumption about who is",
    "reading.",
    "",
    "NOT FIXED HERE, now in STATUS.md: the commodity screen is still trend-ranked",
    "and knows nothing about the core's basket, so with All-Weather chosen (IAU",
    "15% + DBC 10%) the redeploy card offered CANE. Sugar. Nothing is",
    "malfunctioning - the screen ranks momentum across 19 candidates and sugar had",
    "the trend. The DESIGN is wrong: it answers 'which commodity has momentum'",
    "when the question was 'what fills my core's commodity allocation'. PR 10",
    "solved that for bonds by making the mandate a gate; the equivalent here",
    "changes what the commodity card is FOR, so it gets its own change."
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record the sentence this deploy is meant to delete"
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
try {
    $pre = Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -Headers $ApiHeaders -TimeoutSec 300
    $fi = 0.0
    if ($pre.book_target -and $pre.book_target.target) { $fi = [double]($pre.book_target.target.'Fixed Income') }
    Write-Host "  Fixed Income target right now: $([Math]::Round($fi*100,1))% of the book" -ForegroundColor DarkGray
    foreach ($cd in @($pre.recommendations)) {
        foreach ($u in @($cd.meta.unplaced)) {
            Write-Host "  UNPLACED $($u.asset_class): $([Math]::Round([double]$u.amount_ils)) - $($u.reason)" -ForegroundColor DarkGray
        }
    }
    $ideas = @(@($pre.buy_ideas) | Where-Object { $_.sector -eq 'Fixed Income' })
    Write-Host "  bond candidates offered right now: $($ideas.Count)" -ForegroundColor DarkGray
} catch {
    Warn "could not read Today first: $($_.Exception.GetType().Name)"
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Wait for Railway to actually serve it"
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

Step "7. Verify"
& "$PSScriptRoot\..\smoke\smoke-bonds.ps1" -BaseUrl $BaseUrl -Sha $sha
$fail = $LASTEXITCODE

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today: the redeploy card should name a BOND for the Fixed" -ForegroundColor Cyan
Write-Host "    Income leg - most likely BND or AGG - instead of reporting it" -ForegroundColor Cyan
Write-Host "    as unplaceable. That sentence has been on every scan since the" -ForegroundColor Cyan
Write-Host "    app was written." -ForegroundColor Cyan
Write-Host "  * The bond it names should be an aggregate or Treasury fund, NOT" -ForegroundColor Cyan
Write-Host "    high yield. If you see HYG or JNK offered as ballast, the" -ForegroundColor Cyan
Write-Host "    credit gate has regressed - that is equity risk in a bond" -ForegroundColor Cyan
Write-Host "    wrapper and it falls when your sleeves fall." -ForegroundColor Cyan
Write-Host "  * You should never see MUB or VTEB. Your book is in ILS and" -ForegroundColor Cyan
Write-Host "    their US tax break is priced into the yield." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL WRONG ON PURPOSE: the commodity leg may still offer" -ForegroundColor DarkYellow
Write-Host "  something odd like CANE (sugar). That screen ranks 6-month" -ForegroundColor DarkYellow
Write-Host "  momentum and has no idea your core names IAU and DBC. Backlog." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
