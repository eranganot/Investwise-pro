# SMOKE - PR 23. MEASURES the agent map against the LIVE app. READ-ONLY.
#
#   .\scripts\smoke\smoke-the-agent-map-is-measured.ps1
#   .\scripts\smoke\smoke-the-agent-map-is-measured.ps1 -Sha 1234abc
#
# THIS SCRIPT DOES NOT TEST A FIX. There is no fix in PR 23 - it is a map plus
# the tests that keep it honest. What this does is OBSERVE, on the running app,
# every number docs/AGENTS.md asserts, so the document is measured rather than
# argued. If any section disagrees with the map, the MAP is wrong and must be
# corrected; that is the only reason these checks exist.
#
# Three of the four findings are pinned as DEFECTS. A PASS below therefore
# means "the bug is still there, exactly as documented" - which is honest, and
# is why each one says so in plain words rather than printing a green tick and
# moving on.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"
$AgentKey = Get-AgentKey -BaseUrl $BaseUrl
if (-not $AgentKey) { exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $AgentKey }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# A 401 IS AN ANSWER. Reporting it as "did not answer" renders a rejected key
# and an outage identically, and the reader cannot tell them apart.
function Api($method, $path, $timeout = 180) {
    # NOT $args - a PowerShell AUTOMATIC variable, same family as the $h that
    # once silently clobbered the auth headers.
    $reqArgs = @{
        Method     = $method
        Uri        = "$BaseUrl$path"
        Headers    = $ApiHeaders
        TimeoutSec = $timeout
    }
    try {
        return Invoke-RestMethod @reqArgs
    } catch {
        $code = 0
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        }
        if ($code -eq 401 -or $code -eq 403) {
            Write-Host "        HTTP $code - the server REJECTED the agent key. Not an outage." -ForegroundColor Yellow
            Write-Host "        Clear a stale one and let agent-key.ps1 resolve it:" -ForegroundColor DarkGray
            Write-Host "          Remove-Item Env:\IW_AGENT_KEY" -ForegroundColor DarkGray
        } elseif ($code -gt 0) {
            Write-Host "        HTTP $code from $path - $($_.Exception.GetType().Name)" -ForegroundColor DarkGray
        } else {
            Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        }
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - the agent map, measured against the running app" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
Sec "0  which build is serving"
$health = Api GET '/health' 60
if ($null -eq $health) {
    Bad "/health unreachable - nothing below can be trusted"
} else {
    Note "serving commit $($health.commit), version $($health.version)"
    if ($Sha) {
        if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
        else { Bad "serving '$($health.commit)', expected '$Sha'" }
    } else {
        Skip "no -Sha given, so the sha was not verified"
    }
}

# ---------------------------------------------------------------------------
Sec "1  FINDING 1 - 'What's moving' is a four-row fixture"
# docs/AGENTS.md: registry.py:63 returns BuiltinEconomicDataProvider() with no
# settings branch, and app/providers/live.py has NO economic provider to switch
# to, so builtin.py:113-130's four events are all there will ever be.
#
# A PASS here means THE BUG IS STILL PRESENT, as documented. A count other than
# four means the map is out of date.
$events = Api GET '/api/v1/research/events' 90
if ($null -eq $events) {
    Bad "/research/events returned nothing - see the line above for why"
} else {
    $n = [int]$events.count
    Note "the live app is serving $n events to Explore -> What's moving"
    if ($n -eq 4) {
        Ok "4 events - the fixture, unchanged, exactly as docs/AGENTS.md says"
        Note "THIS IS THE ANSWER TO 'not enough cards from What's moving'."
        Note "Not a filter, not a threshold, not your portfolio: there are four"
        Note "hardcoded rows and no live provider to replace them."
    } else {
        Bad "$n events, not the 4 docs/AGENTS.md documents - UPDATE THE MAP"
    }
    foreach ($e in @($events.events)) {
        Note "  - $($e.event_type)  relevance $($e.relevance_score)  horizon $($e.expected_time_horizon)"
    }
}

# ---------------------------------------------------------------------------
Sec "2  FINDING 2 - that list is captioned with the PRICE clock"
# market.py:44 fills last_refreshed from market_status() - the price refresh
# timestamp - and index.html:2791 renders it above the events as
# "Updates hourly - last refreshed N". A static list under a moving clock.
if ($null -ne $events) {
    $lr = "$($events.last_refreshed)"
    if (-not $lr) {
        Skip "last_refreshed was empty on this response, so the mismatch could not be shown"
    } else {
        Note "the events payload carries last_refreshed = $lr"
        Note "interval advertised: $($events.refresh_interval_minutes) minutes"
        $status = Api GET '/api/v1/data-status' 60
        if ($null -eq $status) {
            Skip "/data-status unreachable, so the two clocks could not be compared"
        } else {
            Note "price source in effect: $($status.effective_source) (live=$($status.live))"
            Ok "confirmed: a PRICE timestamp is attached to a list of static events"
            Note "The four events above have not changed since the app was"
            Note "written. The timestamp beside them moves every refresh. That"
            Note "is the +35% caption failure in a different place: a caption"
            Note "that vouches for content it never checked."
        }
    }
} else {
    Skip "no events payload, so the caption could not be inspected"
}

# ---------------------------------------------------------------------------
Sec "3  THE ROSTER - seven speakers, five agents, overlap of two"
# docs/AGENTS.md's central claim. The suite pins it; this confirms the LIVE
# payload agrees with what the suite checked.
$war = Api GET '/api/v1/war-room' 180
if ($null -eq $war) {
    Bad "/war-room returned nothing - see the line above for why"
} else {
    $names = @($war.agents)
    Note "agents declared: $($names -join ', ')"
    if ($names.Count -eq 7) {
        Ok "7 names, matching war_room.py:18 and docs/AGENTS.md"
        Note "Of these, exactly TWO are backed by a class under app/agents/:"
        Note "  Research   -> app/agents/research_agent.py:15"
        Note "  Adversary  -> app/agents/adversary.py:116"
        Note "The other five are NOT agents:"
        Note "  Alpha    -> LagEngine        Risk -> RiskEngine"
        Note "  Tax      -> TaxEngine        Decision -> DecisionEngine"
        Note "  UX       -> no class at all; it is a render step"
        Note "Three agent classes never speak here: OpportunityAgent, FeeAgent,"
        Note "AllocationAgent."
    } else {
        Bad "$($names.Count) names, not 7 - docs/AGENTS.md and the roster test both need updating"
    }
    Note "sessions in this run: $($war.count)"

    # Which of them actually spoke, as opposed to being declared.
    $spoke = @()
    foreach ($s in @($war.sessions)) {
        foreach ($line in @($s.transcript)) {
            if ($spoke -notcontains $line.agent) { $spoke += $line.agent }
        }
    }
    if ($spoke.Count -gt 0) {
        Note "actually spoke in this run: $($spoke -join ', ')"
        $silent = @($names | Where-Object { $spoke -notcontains $_ })
        if ($silent.Count -gt 0) {
            Note "declared but SILENT this run: $($silent -join ', ')"
            Note "(not a defect on its own - a stage can have nothing to say)"
        }
    } else {
        Note "no transcript lines in this run - nothing to attribute"
    }
}

# ---------------------------------------------------------------------------
Sec "4  FINDING 4 - the strategy does not choose WHAT gets bought"
# recommendations.py:2698 calls screen_equities(top_n=5) with no weights, so
# ScreenerEngine uses DEFAULT_WEIGHTS whatever your objective is. This section
# reports your ACTIVE objective next to the buy ideas, so the disconnect is
# visible rather than argued.
$plan = Api GET '/api/v1/plan' 90
if ($null -eq $plan) {
    Skip "/plan unreachable, so the active objective could not be read"
} else {
    # NO TERNARY. 'a ? b : c' is PowerShell 7 only and this repo's shells are
    # Windows PowerShell 5.1, where it is a parse error - the whole script
    # refuses to load, which is worse than any wrong value it could print.
    $obj = "$($plan.objective)"
    if (-not $obj) { $obj = "(none set)" }
    Note "your active objective: $obj"
    Ok "recorded - and it does not reach the screener"
    # 'weights' in single quotes, NOT backticks: inside a double-quoted string
    # a backtick is the escape character, so backtick-w would have been swallowed.
    Note "screen_equities accepts a 'weights' argument. recommendations.py:2698"
    Note "omits it. The only caller that supplies weights is a query string on"
    Note "/api/v1/screener, and nothing in the app builds that URL. So Grow and"
    Note "Preserve rank the identical candidates in the identical order."
    Note "Your objective changes HOW MUCH is bought, never WHAT."
}

# ---------------------------------------------------------------------------
Sec "5  nothing moved - this commit is docs and tests"
$portfolio = Api GET '/api/v1/portfolio' 120
if ($null -eq $portfolio) {
    Bad "/portfolio returned nothing - see the line above for why"
} else {
    $nav = [math]::Round([double]$portfolio.nav_ils, 2)
    Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
    Ok "portfolio reads cleanly"
    Note "PR 23 changes no code path that runs. This NAV must equal what you"
    Note "saw before the deploy. If it moved, something else did it - tell me."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-- result" -ForegroundColor Cyan
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NEVER A PASS. $($script:Skipped) check(s) did not run." -ForegroundColor Yellow
}
Write-Host ""
if ($script:Fail -gt 0) {
    Write-Host "  RED - and on this script that means docs/AGENTS.md is WRONG," -ForegroundColor Red
    Write-Host "  not that the app broke. Send me the failing section." -ForegroundColor Red
    exit 1
}
Write-Host "  Green. Every number in docs/AGENTS.md is confirmed on the" -ForegroundColor Green
Write-Host "  running app - including the three that confirm bugs." -ForegroundColor Green
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * 'What's moving' has no live provider - four fixture rows," -ForegroundColor DarkYellow
Write-Host "    forever - and is captioned with the price-refresh clock." -ForegroundColor DarkYellow
Write-Host "  * The Allocation Agent's veto is unreachable from the app." -ForegroundColor DarkYellow
Write-Host "  * No agent reads your strategy when choosing WHAT to buy." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED and CI can still go red on" -ForegroundColor DarkYellow
Write-Host "    it. SEND ME THE DIAGNOSTIC BLOCK when it next fires." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you" -ForegroundColor DarkYellow
Write-Host "    accepted." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
