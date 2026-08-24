# SMOKE - PR 4: cards you can actually see.
#
#   .\scripts\smoke\smoke-cards.ps1 -Sha <sha>     # READ-ONLY (default)
#   .\scripts\smoke\smoke-cards.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-cards.ps1 -Restore       # WRITES: un-mutes buried cards
#
# READ-ONLY by default. -Restore POSTs /recommendations/restore-completed and
# /restore, which clear the server-side mute lists. Nothing else writes.
#
# WHAT THIS EXISTS TO CATCH. On 2026-08-23 the app showed no buy cards and no
# holding cards and reported itself healthy. Five separate mechanisms, and the
# 90-day mute everyone suspected was the least of them:
#
#   * _reconcile merged the rebalance legs and kept rebal[0]'s id - the SELL leg.
#     So completing "Rebalance toward your target mix" filed the BUY leg riding
#     inside it. You said you had never hidden a buy recommendation. You hadn't.
#   * rebalance_actions[:2] never GENERATED the third leg. A card that was never
#     built cannot be restored and nothing reports it missing.
#   * The history circuit was OPEN. Three agents returned empty lists WITHOUT
#     raising, so nothing appended to degraded[] and the response said healthy.
#     This is the dominant cause and the one C2 below proves.
#   * _rid("tax") and friends take no variable part, so one Accept muted the
#     whole family until the TTL expired.
#   * The rule self-heal acknowledged every re-firing within a second, so the
#     red banner rendered empty while V sat at 38% against a 30% cap.
#
# C2 IS THE POINT OF THIS SCRIPT. If the history breaker is OPEN and degraded[]
# is empty, PR 4 did not land. If the breaker is CLOSED, C2 can only SKIP - it
# cannot prove the reporting works without a failure to report, and a SKIP is
# never a PASS.
#
# House rules: a SKIP is never a PASS. Read paired state in one call. Print the
# exception TYPE on the first failure. Headers are $ApiHeaders, never $h.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [switch]$Restore,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 90) {
    try {
        if ($null -ne $body) {
            $json = ($body | ConvertTo-Json -Depth 6)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $json -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host ""
Write-Host "SMOKE - cards you can actually see (PR 4)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray

# --------------------------------------------------------------------------- #
Sec "C0. The build under test is the one serving"
# --------------------------------------------------------------------------- #
$health = Api GET "/health"
if ($null -eq $health) {
    Bad "C0.1 /health did not answer - nothing below is meaningful"
} else {
    Ok "C0.1 /health answered, serving $($health.commit)"
    if ($SkipShaCheck) { Skip "C0.2 -SkipShaCheck set" }
    elseif (-not $Sha) { Skip "C0.2 no -Sha given" }
    elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) { Ok "C0.2 serving the commit under test" }
    else { Bad "C0.2 serving $($health.commit), expected $Sha - STOP, everything below is about the old build" }
}

# --------------------------------------------------------------------------- #
Sec "C1. Optionally un-mute what the old TTL buried (WRITES - opt in)"
# --------------------------------------------------------------------------- #
if (-not $Restore) {
    Skip "C1.1 not run - pass -Restore to clear the server-side mute lists"
} else {
    $r1 = Api POST "/api/v1/recommendations/restore-completed"
    $r2 = Api POST "/api/v1/recommendations/restore"
    if ($null -eq $r1 -and $null -eq $r2) {
        Bad "C1.1 neither restore endpoint answered"
    } else {
        Ok ("C1.1 restored {0} completed and {1} ignored" -f $(if($r1){$r1.restored}else{0}), $(if($r2){$r2.restored}else{0}))
    }
}

# --------------------------------------------------------------------------- #
Sec "C2. An open circuit names its agents (THE ONE THAT MATTERS)"
# --------------------------------------------------------------------------- #
$ph = Api GET "/api/v1/providers/health"
$recs = Api GET "/api/v1/recommendations"

if ($null -eq $ph -or $null -eq $recs) {
    Bad "C2.0 could not read provider health and recommendations together"
} else {
    $openNames = @()
    foreach ($p in $ph.PSObject.Properties) {
        if ($p.Value -and $p.Value.PSObject.Properties['circuit_state'] -and $p.Value.circuit_state -eq 'OPEN') {
            $openNames += $p.Name
        }
    }
    $deg = @($recs.degraded)
    Write-Host ("        open circuits: {0}" -f $(if ($openNames.Count) { $openNames -join ', ' } else { '(none)' })) -ForegroundColor DarkGray
    Write-Host ("        degraded:      {0}" -f $(if ($deg.Count) { $deg -join ', ' } else { '(empty)' })) -ForegroundColor DarkGray

    if ($openNames -contains 'history') {
        $want = @('holding_verdicts', 'momentum', 'war_room')
        $missing = @($want | Where-Object { $deg -notcontains $_ })
        if ($missing.Count -eq 0) {
            Ok "C2.1 history circuit is OPEN and every agent that depends on it is named in degraded"
        } else {
            Bad ("C2.1 history circuit is OPEN but degraded does not name: {0}. This is EXACTLY the 08-23 failure - the app claiming health while three agents are dark." -f ($missing -join ', '))
        }
    } elseif ($openNames.Count -gt 0) {
        Skip ("C2.1 history is closed; other circuits open ({0}). Cannot prove the history path from here." -f ($openNames -join ', '))
    } else {
        Skip "C2.1 no circuit is open, so there is no degradation to report. This does NOT prove the reporting works - it proves there was nothing to report. Re-run when a provider is down, or trust the unit test (test_pr4_cards.py::test_e)."
    }

    # The inverse. A false degraded is its own lie, and it is the one that makes
    # the field unreadable - which is how degraded:[] got ignored for a week.
    if ($openNames.Count -eq 0 -and $deg.Count -gt 0) {
        $spurious = @($deg | Where-Object { $_ -notin @('rule_suggestions', 'backtest', 'buy_ideas') })
        if ($spurious.Count -gt 0) {
            Bad ("C2.2 nothing is open but degraded names {0} - a false alarm teaches you to ignore the field" -f ($spurious -join ', '))
        } else {
            Ok "C2.2 degraded names only agents with their own non-circuit reasons"
        }
    } else {
        Ok "C2.2 degraded is consistent with the circuit states"
    }
}

# --------------------------------------------------------------------------- #
Sec "C3. Buy cards and holding cards actually appear"
# --------------------------------------------------------------------------- #
if ($null -eq $recs) {
    Bad "C3.0 /api/v1/recommendations did not answer"
} else {
    $all = @($recs.recommendations)
    Write-Host ("        {0} live | {1} dismissed | {2} completed" -f $all.Count, $recs.dismissed_count, $recs.completed_count) -ForegroundColor DarkGray
    foreach ($c in $all) { Write-Host ("        [{0}] {1}  ({2})" -f $c.severity, $c.title, $c.id) -ForegroundColor DarkGray }

    $dims = @($all | ForEach-Object { $_.dimension } | Sort-Object -Unique)
    Write-Host ("        dimensions: {0}" -f ($dims -join ', ')) -ForegroundColor DarkGray

    # Symptom 1: nothing about new assets to buy.
    $buys = @($all | Where-Object { $_.apply -and $_.apply.kind -match 'buy|redeploy|commodity' })
    if ($buys.Count -gt 0) {
        Ok ("C3.1 {0} card(s) propose putting money to work" -f $buys.Count)
    } elseif (@($recs.degraded).Count -gt 0) {
        Skip ("C3.1 no buy cards, and degraded is non-empty ({0}) - which is the honest answer: with those agents dark the app does not know. That is the fix working, not the symptom." -f (@($recs.degraded) -join ', '))
    } else {
        Skip "C3.1 no buy cards and nothing degraded. Either your book genuinely needs no purchase, or a buy leg is being generated and dropped. Check C3.3 for unplaced legs."
    }

    # Symptom 2: nothing about existing holdings.
    $holdings = @($all | Where-Object { $_.dimension -in @('holding', 'signal', 'rule', 'concentration') })
    if ($holdings.Count -gt 0) {
        Ok ("C3.2 {0} card(s) speak to holdings you already own" -f $holdings.Count)
    } else {
        Skip "C3.2 no holding cards. If the history circuit is open this is expected and C2.1 already said so."
    }

    # The merged rebalance card must not carry a leg's id any more.
    $merged = @($all | Where-Object { $_.title -eq 'Rebalance toward your target mix' })
    if ($merged.Count -eq 0) {
        Skip "C3.3 no merged rebalance card on the book right now"
    } else {
        $legs = @($all | Where-Object { $_.apply -and $_.apply.kind -eq 'rebalance_to_objective' -and $_.title -ne 'Rebalance toward your target mix' })
        $clash = @($legs | Where-Object { $_.id -eq $merged[0].id })
        if ($clash.Count -gt 0) {
            Bad ("C3.3 the merged card shares id {0} with '{1}' - accepting one mutes the other" -f $merged[0].id, $clash[0].title)
        } else {
            Ok ("C3.3 the merged rebalance card has its own id ({0})" -f $merged[0].id)
        }
        Write-Host ("        merged action: {0}" -f $merged[0].action) -ForegroundColor DarkGray
    }
}

# --------------------------------------------------------------------------- #
Sec "C4. A rule that keeps firing keeps showing"
# --------------------------------------------------------------------------- #
$ev = Api GET "/api/v1/rules/events"
$rules = Api GET "/api/v1/rules"

if ($null -eq $ev -or $null -eq $rules) {
    Bad "C4.0 could not read rules and their events together"
} else {
    $armed = @($rules.rules | Where-Object { $_.active })
    $fired = @($rules.rules | Where-Object { $_.triggered })
    Write-Host ("        {0} armed, {1} currently triggered" -f $armed.Count, $fired.Count) -ForegroundColor DarkGray
    foreach ($r in $fired) { Write-Host ("        {0} {1} @ {2} - last fired {3}" -f $r.ticker, $r.rule_type, $r.level, $r.last_triggered_at) -ForegroundColor DarkGray }

    if ($fired.Count -eq 0) {
        Skip "C4.1 no rule is currently triggered, so there is nothing to auto-acknowledge"
    } else {
        # Every triggered rule must have a card OR appear in the banner. A rule
        # that is triggered, has no card, and is not in the banner is the
        # deadlock: work you can see the count of and cannot click.
        $cardIds = @()
        if ($null -ne $recs) { $cardIds = @($recs.recommendations | Where-Object { $_.id -like 'rule_*' } | ForEach-Object { $_.id }) }
        $banner = @()
        if ($null -ne $recs -and $recs.rule_banner) { $banner = @($recs.rule_banner.triggered) }
        Write-Host ("        rule cards: {0} | banner: {1}" -f $cardIds.Count, $banner.Count) -ForegroundColor DarkGray

        if ($cardIds.Count -eq 0 -and $banner.Count -eq 0) {
            Bad ("C4.1 {0} rule(s) triggered but neither a card nor a banner entry exists - the exact deadlock where a re-firing is acknowledged one second after it fires" -f $fired.Count)
        } elseif ($cardIds.Count -ge $fired.Count) {
            Ok ("C4.1 all {0} triggered rule(s) have a card to act on" -f $fired.Count)
        } else {
            Skip ("C4.1 {0} triggered, {1} card(s), {2} in the banner - partial. Check that the shortfall is a rule whose condition cleared, not one that was auto-acknowledged." -f $fired.Count, $cardIds.Count, $banner.Count)
        }
    }

    $recent = @($ev.events | Select-Object -First 12)
    $insta = @($recent | Where-Object {
        $_.outcome -eq 'acknowledged' -and $_.outcome_at -and $_.triggered_at -and
        ([datetime]$_.outcome_at - [datetime]$_.triggered_at).TotalSeconds -lt 5 -and
        $_.notified -ne $true
    })
    if ($recent.Count -eq 0) {
        Skip "C4.2 no rule events recorded yet"
    } elseif ($insta.Count -gt 0) {
        Bad ("C4.2 {0} of the last {1} events were acknowledged within 5s of firing - nothing was sent and nobody clicked, so 'handled' is not true" -f $insta.Count, $recent.Count)
    } else {
        Ok ("C4.2 none of the last {0} events were auto-acknowledged on arrival" -f $recent.Count)
    }
}

# --------------------------------------------------------------------------- #
Sec "C5. Commodity ETFs are commodities"
# --------------------------------------------------------------------------- #
# classify() is not exposed directly, so read it where it is USED:
# /mix runs current_mix(), which classifies every row. A commodity ETF held
# without an explicit asset_class in its meta lands in whatever classify() says.
$mix = Api GET "/api/v1/mix"
$pf = Api GET "/api/v1/portfolio"

if ($null -eq $mix -or $null -eq $pf) {
    Bad "C5.0 could not read the mix and the positions together"
} elseif ($mix.PSObject.Properties['message']) {
    Skip "C5.1 no holdings, so nothing to classify"
} else {
    $cm = @('DBC','DBA','DBB','DBE','DBO','DBP','DBS','GLD','IAU','SLV','PPLT','PALL',
            'USO','UNG','BNO','UGA','CORN','WEAT','SOYB','CANE','COW','NIB','JO',
            'PDBC','COMT','GSG','FTGC','BCI')
    $held = @($pf.positions | Where-Object { $cm -contains "$($_.ticker)".ToUpper() })
    $cur = $mix.current_allocation

    if ($held.Count -eq 0) {
        Skip ("C5.1 you hold no commodity ETF right now, so classify() cannot be observed from live data. Current mix: {0}" -f (($cur.PSObject.Properties | ForEach-Object { "$($_.Name) $([Math]::Round($_.Value * 100, 1))%" }) -join ', '))
    } else {
        $names = @($held | ForEach-Object { $_.ticker })
        Write-Host ("        commodity ETFs held: {0}" -f ($names -join ', ')) -ForegroundColor DarkGray
        $cw = 0.0
        if ($cur.PSObject.Properties['Commodities']) { $cw = [double]$cur.Commodities }
        if ($cw -gt 0) {
            Ok ("C5.1 the mix reports {0:N1}% Commodities, so {1} is not being filed as Equities" -f ($cw * 100), ($names -join ', '))
        } else {
            Bad ("C5.1 you hold {0} and the mix reports 0% Commodities - a commodities leg can never be placed while these classify as Equities" -f ($names -join ', '))
        }
    }
}

Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "    * Today should show MORE cards than yesterday, and at least one" -ForegroundColor Cyan
Write-Host "      about a holding or a purchase - unless C2.1 says agents are dark," -ForegroundColor Cyan
Write-Host "      in which case an empty screen is now an HONEST empty screen." -ForegroundColor Cyan
Write-Host "    * The rebalance card should name every leg, not the first two." -ForegroundColor Cyan
Write-Host "    * If V or MSFT is over its cap, the red rule banner should say so" -ForegroundColor Cyan
Write-Host "      and stay saying so until you act on it." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE (later PRs own these):" -ForegroundColor DarkYellow
Write-Host "    * Sleeves still read '64% of the book' when 15% is held (PR 5)." -ForegroundColor DarkYellow
Write-Host "    * No strategy manages the core; core is still null (PR 6)." -ForegroundColor DarkYellow
Write-Host "    * The history provider itself is not fixed here - PR 4 makes the" -ForegroundColor DarkYellow
Write-Host "      outage VISIBLE. smoke-p0 and smoke-beat-market fail on a" -ForegroundColor DarkYellow
Write-Host "      non-empty degraded, so expect them to go red now. Fix the" -ForegroundColor DarkYellow
Write-Host "      provider, never the assertion." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0
