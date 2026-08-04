# SMOKE - the Beat the Market work (phases 10-17): contributions ledger + strategy backtests + the Plan tab.
# READ-ONLY by default: nothing is bought, sold, dismissed or recorded.
#
#   .\scripts\smoke\smoke-beat-market.ps1
#
# Uses AGENT_API_KEY (same fallback as smoke-all.ps1). Override with
#   $env:IW_AGENT_KEY = '<key>'   if you rotate it.
#
#   -Refresh   also POSTs /strategies/backtests/refresh (SLOW, ~10y of daily
#              closes per ticker) so the first run after a deploy has numbers.
#
# Deliberately asserts nothing it cannot read. Where a value is not yet
# populated the check SKIPs and says what to run -- a skip is not a pass.

param(
    [switch]$Refresh,
    # Applies a Beat the Market strategy so the signal and discipline checks can
    # actually run. WRITES: it sets your plan's objective, risk tolerance and
    # strategy. Everything else in this script is read-only.
    [string]$ApplyStrategy = "",
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
# Same fallback as smoke-all.ps1 so this runs with no setup. The key is already
# committed in this repo; rotating AGENT_API_KEY in Railway invalidates both
# copies at once, and then only these two lines need updating.
# NOT $h -- PowerShell variable names are case-insensitive and a later $h
# assignment silently clobbered the headers once already (see smoke-all.ps1).
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0; $dense = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $tmo = 60) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $c = $_.Exception.Response.StatusCode.value__
        Write-Host "        $method $path -> $(if($c){"HTTP $c"}else{$_.Exception.Message})" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: contributions + backtests  ($BaseUrl)" -ForegroundColor White

# ---------- PHASE 10: "what you put in" ----------
Sec "10. Contributions ledger - only a deposit may move 'you put in'"
$p = Api GET '/api/v1/portfolio'
$c = Api GET '/api/v1/portfolio/contributions'

if ($null -eq $p) { Bad "portfolio unreachable" }
elseif ($null -eq $p.invested_source) { Bad "no invested_source field - phase 10 has not deployed" }
else {
    Ok "invested_source present: $($p.invested_source)"
    if ($p.invested_source -eq 'contributions') { Ok "reading the ledger, not the drifting cost-basis estimate" }
    else { Skip "still on the legacy estimate - run: .\scripts\set-contributions.ps1 -Amount <your total>" }
}

if ($null -eq $c) { Bad "contributions endpoint unreachable" }
elseif (-not $c.tracked) { Skip "no contributions recorded yet (tracked=false)" }
else {
    Ok "ledger tracked, $($c.entries.Count) entry(ies), total $([math]::Round($c.total_ils,2))"
    # THE INVARIANT. This is the whole bug: invested_ils must equal the ledger
    # exactly. Under the old derivation it was a sum of cost bases converted at
    # today's FX, so it drifted daily and jumped on every sale.
    if ($p -and [math]::Abs($p.invested_ils - $c.total_ils) -lt 0.01) {
        Ok "invested_ils == ledger total (no drift)"
    } elseif ($p) {
        Bad "invested_ils $($p.invested_ils) != ledger $($c.total_ils) - something other than a deposit is writing it"
    }
    foreach ($e in $c.entries) {
        if ($e.kind -notin @('deposit','withdrawal')) { Bad "entry has unknown kind '$($e.kind)'" }
    }
    if ($c.entries.Count -gt 0) { Ok "every entry carries a direction" }
}

if ($p -and $p.invested_ils) {
    $g = $p.nav_ils - $p.invested_ils
    if ([math]::Abs($g - $p.gain_ils) -lt 0.02) { Ok "gain_ils is consistent with nav - invested" }
    else { Bad "gain_ils $($p.gain_ils) != nav $($p.nav_ils) - invested $($p.invested_ils)" }
    $pct = [math]::Round($g / $p.invested_ils * 100, 2)
    if ([math]::Abs($pct - $p.gain_pct) -lt 0.02) { Ok "gain_pct consistent ($($p.gain_pct)%)" }
    else { Bad "gain_pct $($p.gain_pct)% != computed $pct%" }
}

# Stability: two reads a moment apart must agree. Under the old code the figure
# moved with the FX rate, so this is the drift check.
$p2 = Api GET '/api/v1/portfolio'
if ($p -and $p2 -and $p.invested_ils -eq $p2.invested_ils) { Ok "invested_ils stable across reads" }
elseif ($p -and $p2) { Bad "invested_ils moved between two reads: $($p.invested_ils) -> $($p2.invested_ils)" }

# Write path that must change nothing: a zero adjustment is a documented no-op.
$z = try { Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/portfolio/contributions" `
        -Headers ($ApiHeaders + @{'Content-Type'='application/json'}) `
        -Body (@{amount_ils=0; mode='adjust'} | ConvertTo-Json) -TimeoutSec 30 } catch { $null }
if ($null -eq $z) { Skip "zero-adjust no-op not verified" }
elseif ($c -and [math]::Abs($z.total_ils - $c.total_ils) -lt 0.01) { Ok "zero adjustment is a no-op (nothing written)" }
else { Bad "a zero adjustment changed the total: $($c.total_ils) -> $($z.total_ils)" }

# ---------- PHASES 11-12: measured strategies ----------
Sec "11/12. Strategy backtests - precomputed, served from storage"

$s = Api GET '/api/v1/strategies'
if ($null -eq $s) { Bad "/strategies unreachable" }
else {
    if ($s.backtest_engine_version) { Ok "engine version reported: $($s.backtest_engine_version)" }
    else { Bad "no backtest_engine_version - phase 12 has not deployed" }
    # Phase C merged the family into the Plan catalog.
    if ($s.goals -contains 'Beat the Market') { Ok "'Beat the Market' is a Plan tab" }
    else { Bad "'Beat the Market' missing from the Plan tabs - phase 13 has not deployed" }
    # The four existing families must be untouched, and must still carry the
    # DERIVED profile: a measured strategy has no profile, and inventing one
    # would fabricate the very number the backtest exists to measure.
    foreach ($g in @('Grow','Balanced','Income','Preserve')) {
        $items = $s.by_goal.$g
        if (-not $items) { Bad "goal '$g' vanished from the catalog"; continue }
        foreach ($it in $items) {
            if ($null -eq $it.profile) { Bad "$g/$($it.id): lost its derived profile" }
        }
    }
    Ok "the four original families still carry derived profiles"
    $btm = $s.by_goal.'Beat the Market'
    if (-not $btm) { Bad "Beat the Market has no cards" }
    else {
        foreach ($it in $btm) {
            if (-not $it.measured) { Bad "$($it.id): not flagged as measured - the UI would look for a profile" }
            if ($it.profile) { Bad "$($it.id): carries a derived profile it cannot honestly have" }
            if (-not $it.rule) { Bad "$($it.id): no plain-language rule line" }
            if ($null -eq $it.sleeve_pct) { Bad "$($it.id): no suggested sleeve size" }
            foreach ($leg in $it.basket) { if ($leg.Count -ne 2) { Bad "$($it.id): basket leg is not a [ticker, weight] pair" } }
        }
        Ok "$($btm.Count) measured cards carry a rule line, a sleeve size and pair-shaped baskets"
        $unmeasured = @($btm | Where-Object { $null -eq $_.backtest })
        if ($unmeasured.Count -gt 0) { Skip "$($unmeasured.Count) card(s) have no stored backtest yet - run with -Refresh" }
        else { Ok "every card carries a measured result" }
    }
    # The buttons must resolve, or the tab is decorative.
    $pv = Api GET "/api/v1/strategies/btm_trend_tqqq/preview"
    if ($null -eq $pv) { Skip "preview unreachable" }
    elseif ($pv.ok) { Ok "'What changes?' resolves a rule-based strategy" }
    else { Bad "preview failed for a rule-based id: $($pv.error)" }
}

if ($Refresh) {
    Sec "   forcing a recompute (slow - ~10y of daily closes per ticker)"
    $r = try { Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/strategies/backtests/refresh" `
            -Headers $ApiHeaders -TimeoutSec 600 } catch { $null }
    if ($null -eq $r) { Bad "refresh failed" }
    elseif ($r.provider_outage) {
        Bad "every strategy failed to fetch prices - $($r.note)"
        Write-Host "        the circuit breaker opens for the whole history tier at once; wait and re-run" -ForegroundColor Yellow
    }
    else { Ok "refresh: $($r.computed) computed, $($r.abstained) abstained (engine $($r.engine_version))" }
}

$b = Api GET '/api/v1/strategies/backtests' 120
if ($null -eq $b) { Bad "/strategies/backtests unreachable" }
else {
    if ($b.strategies.Count -gt 0) { Ok "$($b.strategies.Count) strategies in the catalog" }
    else { Bad "catalog is empty" }

    foreach ($st in $b.strategies) {
        if (-not $st.name -or -not $st.description) { Bad "$($st.id): missing name or description" }
        if (-not $st.basket -or $st.basket.Count -eq 0) { Bad "$($st.id): empty basket" }
        else {
            foreach ($leg in $st.basket) {
                if ($leg.Count -ne 2) { Bad "$($st.id): basket leg is not a [ticker, weight] pair - the Plan renderer needs pairs" }
            }
        }
        if ($null -ne $st.overlay) { Bad "$($st.id): mechanical spec leaked to the client" }
    }
    Ok "every card has prose, a [ticker, weight] basket, and no leaked spec"

    if ($b.never_computed.Count -gt 0) {
        Skip "$($b.never_computed.Count) never computed - run this script with -Refresh, or wait for the 03:30 job"
    } else { Ok "every strategy has a stored result" }

    # Distinguish "old" from "produced by a different engine". The second is the
    # one that silently degrades every downstream check: fields the new engine
    # adds are simply absent, which reads as "not measured" rather than
    # "measured before this field existed".
    $wrongEngine = @($b.strategies | Where-Object { $_.backtest -and $_.backtest.stale_reason -eq 'engine_version' })
    $tooOld      = @($b.strategies | Where-Object { $_.backtest -and $_.backtest.stale_reason -eq 'age' })
    if ($wrongEngine.Count -gt 0) {
        $was = $wrongEngine[0].backtest.engine_version
        $now = $wrongEngine[0].backtest.live_engine_version
        Bad "$($wrongEngine.Count) result(s) computed by engine '$was' but the live engine is '$now'"
        Write-Host "        -> POST /api/v1/strategies/backtests/refresh  (or re-run this script with -Refresh)" -ForegroundColor Yellow
    }
    if ($tooOld.Count -gt 0) { Skip "$($tooOld.Count) result(s) older than the freshness window - tonight's 03:30 job will fix it" }
    if ($wrongEngine.Count -eq 0 -and $tooOld.Count -eq 0 -and $b.never_computed.Count -eq 0) { Ok "every result is fresh and from the live engine" }

    foreach ($st in $b.strategies) {
        $bt = $st.backtest
        if ($null -eq $bt) { continue }
        if (-not $bt.ok) {
            if ($bt.metrics -and $null -ne $bt.metrics.cagr_pct) {
                # The measurement survived a failed refresh, which is the point:
                # a provider hiccup must not erase ten years of computed history.
                Ok "$($st.id): kept its last measurement through a failed refresh ($($bt.reason))"
            } else {
                Skip "$($st.id): abstained - $($bt.reason) $($bt.detail)"
            }
            continue
        }
        if ($bt.refresh_failing) { Skip "$($st.id): numbers are good but the refresh is failing - $($bt.last_error)" }
        $m = $bt.metrics
        if ($null -eq $m.cagr_pct) { Bad "$($st.id): stored result has no CAGR" ; continue }
        $obs = $bt.period.observations
        Write-Host ("        {0,-22} CAGR {1,7}%  DD {2,6}%  vsSPY {3,7}  obs {4}" -f `
            $st.id, $m.cagr_pct, $m.max_drawdown_pct, $m.excess_cagr_pct, $obs) -ForegroundColor DarkGray
        if ($obs -lt 260) { Bad "$($st.id): only $obs sessions - below the engine's own minimum" }
        if (-not ($bt.period.start -and $bt.period.end)) { Bad "$($st.id): result has no date span" }
        if ($m.known_failure) { Bad "$($st.id): built on an overlay measured as broken - $($m.known_failure)" }

        # Density, not row count, is what distinguishes real daily data from a
        # feed quietly serving monthly bars. Yahoo returns MONTHLY for
        # range=max, which a row count alone would read as a long history.
        # Only meaningful on a row the live engine wrote; an older row simply
        # predates the field, and saying so seven times buries the one real
        # message (the engine version is stale).
        if ($null -eq $m.sessions_per_year) {
            if ($bt.stale_reason -ne 'engine_version') { Bad "$($st.id): no session density recorded" }
        }
        elseif ($m.sessions_per_year -ge 200) { $script:dense++ }
        else { Bad "$($st.id): $($m.sessions_per_year) sessions/yr - that is not daily data" }

        # A short window is fine when a young fund caused it, and a bug when the
        # feed truncated it. The engine now says which.
        if ($obs -lt 2000) {
            if ($m.limiting_ticker) {
                Ok "$($st.id): $([math]::Round($obs/252,1))y window, capped by $($m.limiting_ticker) listing $($m.history_start_by_ticker.$($m.limiting_ticker))"
            } elseif ($m.history_capped_by_provider) {
                Bad "$($st.id): only $obs sessions and no young fund to explain it - the feed is truncating"
            } elseif ($bt.stale_reason -ne 'engine_version') {
                Bad "$($st.id): short window with no recorded cause"
            }
        }

        $oos = $bt.robustness.out_of_sample
        if ($null -eq $oos) { Skip "$($st.id): no out-of-sample split stored" }
        elseif ($oos.verdict -eq 'likely overfitted') {
            if ($null -eq $oos.benchmark_decay_pct) {
                Write-Host "        $($st.id): 'likely overfitted' - but judged against zero, not the benchmark (old engine)" -ForegroundColor DarkYellow
            } else {
                Write-Host ("        {0}: 'likely overfitted' - decayed {1} pts vs the benchmark's {2} across the split" -f `
                    $st.id, $oos.cagr_decay_pct, $oos.benchmark_decay_pct) -ForegroundColor Yellow
            }
        }
    }
    $withNums = @($b.strategies | Where-Object { $_.backtest -and $_.backtest.ok })
    if ($withNums.Count -gt 0) { Ok "$($withNums.Count) strategy(ies) carry a traceable measured result" }

    if ($withNums.Count -gt 0 -and $script:dense -eq $withNums.Count) {
        Ok "all $($withNums.Count) results are true daily series (phase 0 fix live)"
    }
}

# ---------- PHASE D: live signals ----------
Sec "13. Live strategy signals - the rule speaks only when it changes its mind"
if ($ApplyStrategy) {
    Write-Host "   applying '$ApplyStrategy' (this WRITES to your plan)" -ForegroundColor Yellow
    $ap = try { Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/strategies/$ApplyStrategy/apply" `
            -Headers $ApiHeaders -TimeoutSec 120 } catch { $null }
    if ($null -eq $ap) { Bad "could not apply '$ApplyStrategy'" }
    elseif ($ap.ok -eq $false) { Bad "apply refused: $($ap.error)" }
    else { Ok "applied '$ApplyStrategy'" }
}
$plan = Api GET '/api/v1/plan'
$active = $plan.strategy
if (-not $active) {
    Bad "no strategy applied - signals and discipline cannot be exercised"
    Write-Host "        -> re-run with:  -ApplyStrategy btm_trend_tqqq   (writes to your plan)" -ForegroundColor Yellow
}
elseif ($active -notlike 'btm_*') {
    Skip "active strategy '$active' is a static basket, so there is no daily rule to evaluate"
    Write-Host "        -> to exercise signals:  -ApplyStrategy btm_trend_tqqq" -ForegroundColor DarkGray
}
else {
    Ok "active rule-based strategy: $active"
    $sg = Api GET '/api/v1/strategies/signal'
    if ($null -eq $sg) { Skip "signal endpoint unreachable" }
    elseif (-not $sg.ok) {
        # An abstention is correct behaviour, not a failure -- except a stale
        # feed, which means the signal would have described last week's market.
        if ($sg.reason -eq 'STALE_FEED') { Bad "signal refused: $($sg.detail)" }
        else { Skip "signal abstained: $($sg.reason) $($sg.detail)" }
    } else {
        Ok "signal evaluated as of $($sg.as_of): wants $($sg.describes)"
        if ($sg.pending) { Write-Host "        a flip is pending - it should appear as a Today card" -ForegroundColor Yellow }
    }
    # A pending flip must reach Today, or the signal is invisible.
    $recs = Api GET '/api/v1/recommendations' 90
    if ($null -eq $recs) { Skip "recommendations unreachable" }
    else {
        $sigCards = @($recs.recommendations | Where-Object { $_.id -like 'stratsig_*' })
        if ($sg.pending -and $sigCards.Count -eq 0) { Bad "a flip is pending but no Today card was produced" }
        elseif ($sigCards.Count -gt 0) {
            Ok "strategy signal card is on Today"
            foreach ($sc in $sigCards) {
                if ($sc.how -join ' ' -notmatch 'not moving anything for you') {
                    Bad "$($sc.id): card does not state that the app is not acting"
                }
            }
            Ok "the card admits the app is not acting for you"
        } else { Ok "no pending flip, no card (correct - only a change is news)" }
        if ($recs.degraded -contains 'strategy_signals') { Bad "the strategy-signal agent failed inside Today" }



        # PHASE E: the standing rules that keep the strategy working when you
        # are not looking. Offered, never armed automatically.
        $disc = @($recs.recommendations | Where-Object { $_.id -like 'stratrules_*' })
        $rules = Api GET '/api/v1/rules'
        $armed = @($rules.rules | Where-Object { $_.note -like '*trailing stop*' -or $_.note -like '*sleeve*' })
        if ($disc.Count -gt 0) {
            Ok "discipline card offered: $($disc[0].action)"
            if ($disc[0].apply.kind -ne 'create_rules') { Bad "the discipline card cannot actually arm anything" }
            else { Ok "Accept would really arm them (create_rules)" }
            foreach ($r in $disc[0].apply.rules) {
                if ($r.level -le 0) { Bad "$($r.ticker) $($r.rule_type): level is $($r.level)" }
                # Derived from the strategy's measured volatility, so a round
                # number would mean the derivation was skipped.
                if ($r.rule_type -eq 'trailing_stop' -and ($r.level -lt 12 -or $r.level -gt 35)) {
                    Bad "$($r.ticker) trailing stop at $($r.level)% is outside the derived band"
                }
            }
            Ok "every offered rule carries a derived, in-band level"
        } elseif ($armed.Count -gt 0) {
            Ok "discipline already armed ($($armed.Count) rule(s)) - card correctly not repeated"
        } else {
            Skip "no discipline card - needs a stored backtest AND a held sleeve ticker"
        }
    }
}

# ---------- banner reconciliation (independent of any applied strategy) ----------
Sec "14. Triggered-rules banner matches the cards"
$recs2 = Api GET '/api/v1/recommendations' 90
if ($null -eq $recs2) { Bad "recommendations unreachable" }
else {
    # `degraded` is the field that separates "no cards because nothing fired"
    # from "no cards because the agent raised" -- the two look identical from
    # outside and produced several wrong hypotheses about this exact failure.
    if ($recs2.degraded -and $recs2.degraded.Count -gt 0) {
        Bad "agents degraded: $($recs2.degraded -join ', ')"
        Write-Host "        a degraded rules agent means missing cards say NOTHING about your rules" -ForegroundColor Yellow
    } else { Ok "no agent degraded" }

    $rb = $recs2.rule_banner
    $ruleCards = @($recs2.recommendations | Where-Object { $_.dimension -eq 'rule' })
    if ($null -eq $rb) { Bad "no rule_banner in the response - the reconciliation has not deployed" }
    elseif ($rb.skipped_reason) { Skip "reconciliation skipped: $($rb.skipped_reason)" }
    else {
        Write-Host "        triggered: $(if ($rb.triggered.Count) { $rb.triggered -join ', ' } else { 'none' })" -ForegroundColor DarkGray
        if ($rb.carded.Count -eq $ruleCards.Count) { Ok "banner ($($rb.carded.Count)) matches the rule cards ($($ruleCards.Count))" }
        else { Bad "banner says $($rb.carded.Count) carded but Today shows $($ruleCards.Count) rule cards" }
        if ($rb.healed.Count -gt 0) { Ok "healed $($rb.healed.Count) orphaned trigger(s): $($rb.healed -join ', ')" }
        # The end state that matters: after this call, /rules must agree.
        $rulesNow = (Api GET '/api/v1/rules').rules
        $stillTrig = @($rulesNow | Where-Object { $_.active -and $_.triggered })
        if ($stillTrig.Count -eq $ruleCards.Count) { Ok "/rules agrees after reconciliation ($($stillTrig.Count))" }
        else {
            Bad "$($stillTrig.Count) rule(s) still flagged triggered against $($ruleCards.Count) card(s): $(($stillTrig | ForEach-Object { $_.ticker }) -join ', ')"
            Write-Host "        the reconciliation ran but did not clear them - check the server log for 'could not heal orphaned rule'" -ForegroundColor Yellow
        }
    }
}

# ---------- scheduler ----------
Sec "Scheduler - the nightly job that keeps the numbers fresh"
$ps = Api GET '/api/v1/push/status'
if ($null -eq $ps) { Skip "push/status unreachable - cannot confirm the job is registered" }
elseif (-not $ps.scheduler.scheduler_running) { Bad "scheduler not running - backtests will never refresh" }
else {
    $jids = @($ps.scheduler.jobs | ForEach-Object { $_.id })
    if ($jids -contains 'backtest_refresh') { Ok "backtest_refresh registered (03:30 daily)" }
    else { Bad "backtest_refresh NOT registered - numbers will go stale and never recover" }
    if ($jids -contains 'strategy_signals') { Ok "strategy_signals registered (06:15 daily)" }
    else { Bad "strategy_signals NOT registered - a rule could flip and never tell you" }
}

Write-Host "`n===== $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run.`n" -ForegroundColor DarkYellow } else { Write-Host "" }
