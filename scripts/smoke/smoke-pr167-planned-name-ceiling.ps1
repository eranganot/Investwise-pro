# SMOKE - PR 167 (D150): a name the plan asks for may be held up to its plan
# weight, and the cash card sizes every name against that plan.
#
#   .\scripts\smoke\smoke-pr167-planned-name-ceiling.ps1 -Sha <short sha>
#
#   1  a cap the sleeve armed is not below the plan weight of its ticker
#      (sleeve + core share, the number the band rule quotes)
#   2  no "Trim X" card and no "nearly at your cap" heads-up for a name that
#      is at or under its plan weight
#   3  the cash card: every leg is a name the plan holds, and no leg takes a
#      name past the top of its own band
#   4  no card offers to buy a NEW name outside a basketed core (the realign
#      would sell it straight back)
#   5  a "take them together or neither" note only points at a card on Today
#   6  NOT CHANGED: Today builds, no degraded agent, the book target and the
#      band rules read as before (hand-set caps are listed, for comparison)
#
# Live book on 2026-11-16 (877d39f): QQQ plan 42.3% (35 sleeve + 7.3 core) capped
# at 35; ledger 18:27:47 realign bought 1,317 of QQQ, 18:27:59 "Trim QQQ" sold
# 1,299.59; the cash card then proposed VTI 678 / VXUS 361 / XLE 253, taking VTI
# to 15.2% against a band that trims above 14.7%; "Consider CVX" was on Today.
#
# The stored cap is raised by the rules check (every 30 minutes), not by this
# deploy. Until it has run once, section 1 reports the stale level as a NOTE if
# -AllowStaleCap is passed, a FAIL otherwise.
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [switch]$AllowStaleCap)
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 167"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(no migration in this change)"

$sl = Api GET "/api/v1/plan/sleeves"
$r = Api GET "/api/v1/recommendations"
$rules = Api GET "/api/v1/rules"
$pf = Api GET "/api/v1/portfolio"
$mix = Api GET "/api/v1/mix"
$nav = [double]$pf.nav_ils
if ($nav -le 0) { Bad "no NAV: nothing below can be measured"; Stop-Smoke }

# --- what is held, and what the plan asks for of each ticker (percent of the book)
$held = @{}
foreach ($p in @($pf.positions)) { $held["$($p.ticker)".ToUpper()] = [double]$p.value_ils }
$plan = @{}; $bandW = @{}
foreach ($x in @(@($rules.rules) | Where-Object { $_.rule_type -eq 'rebalance_band' -and $_.active -ne $false })) {
    $t = "$($x.ticker)".ToUpper()
    if ($null -ne $x.target) { $plan[$t] = [double]$x.target; $bandW[$t] = [math]::Abs([double]$x.level) }
}
$sleeveTk = @{}
foreach ($s in @($sl.sleeves)) { foreach ($t in @($s.tickers)) { $sleeveTk["$t".ToUpper()] = [double]$s.sleeve_pct } }
$basket = @{}
foreach ($c in @($sl.core_holdings)) { if ($c.in_plan) { $basket["$($c.ticker)".ToUpper()] = 100.0 * [double]$c.target_pct } }
# A planned name with no band rule: sleeve size + the core's share of it.
foreach ($t in @($sleeveTk.Keys)) { if (-not $plan.ContainsKey($t)) { $plan[$t] = $sleeveTk[$t] + $(if ($basket.ContainsKey($t)) { $basket[$t] } else { 0.0 }) } }
foreach ($t in @($basket.Keys)) { if (-not $plan.ContainsKey($t)) { $plan[$t] = $basket[$t] } }
$coreBasketed = ($basket.Count -gt 0)
function WeightPct($t) { if ($held.ContainsKey($t)) { return 100.0 * $held[$t] / $nav } return 0.0 }
Note ("NAV {0:N0}; plan: {1}" -f $nav, ((@($plan.Keys) | Sort-Object | ForEach-Object { "{0} {1:N1}%" -f $_, $plan[$_] }) -join ', '))

$cards = @(@($r.recommendations) + @($r.more_recommendations))
$heads = @($r.headsups)

Sec "1  a sleeve-armed cap is not below its ticker's plan weight"
$capSeen = 0
foreach ($x in @(@($rules.rules) | Where-Object { $_.rule_type -eq 'max_weight' -and $_.active -ne $false })) {
    $t = "$($x.ticker)".ToUpper()
    if (-not $sleeveTk.ContainsKey($t)) { continue }
    if ("$($x.strategy_id)" -ne 'sleeve') { Note ("{0}: cap {1:N1}% is hand-set, not the sleeve's -- left alone" -f $t, [double]$x.level); continue }
    $capSeen++
    $want = $plan[$t]
    if ([double]$x.level -ge $want - 0.1) { Ok ("{0} capped at {1:N1}% against a plan of {2:N1}%" -f $t, [double]$x.level, $want) }
    elseif ($AllowStaleCap) { Note ("{0} capped at {1:N1}% against a plan of {2:N1}% -- the rules check has not run since the deploy" -f $t, [double]$x.level, $want) }
    else { Bad ("{0} capped at {1:N1}%, below its plan of {2:N1}% (sleeve {3:N1} + core)" -f $t, [double]$x.level, $want, $sleeveTk[$t]) }
}
if ($capSeen -eq 0) { Skip "no sleeve-armed cap on a sleeve ticker today" }

Sec "2  nothing trims or warns about a name that is within its plan"
$trimBad = 0; $trimSeen = 0
foreach ($c in $cards) {
    if ("$($c.apply.kind)" -ne 'trim') { continue }
    $t = "$($c.apply.ticker)".ToUpper()
    if (-not $plan.ContainsKey($t)) { continue }
    if ("$($c.id)" -like 'rule_*') { continue }      # a rule's own firing is section 1's subject
    $trimSeen++
    $w = WeightPct $t
    if ($w -le $plan[$t] + 0.05) { Bad ("'{0}' sells {1} at {2:N1}% of the book, under its plan of {3:N1}%" -f $c.title, $t, $w, $plan[$t]); $trimBad++ }
    else { Ok ("'{0}': {1} is {2:N1}% against a plan of {3:N1}%" -f $c.title, $t, $w, $plan[$t]) }
}
if ($trimSeen -eq 0) { Ok "no trim card on a planned name" }
$nearBad = 0
foreach ($h in $heads) {
    if ("$($h.title)" -notlike '* is nearly at your *% cap') { continue }
    $t = "$($h.ticker)".ToUpper()
    if (-not $plan.ContainsKey($t)) { continue }
    $capPct = [double]$h.card.meta.cap_pct
    if ($capPct -lt $plan[$t] - 0.1) {
        if ($AllowStaleCap) { Note ("'{0}' -- the cap is still stale (section 1)" -f $h.title) }
        else { Bad ("'{0}', but the plan asks for {1:N1}% of {2}" -f $h.title, $plan[$t], $t); $nearBad++ }
    }
}
if ($nearBad -eq 0) { Ok "no near-cap heads-up measures a planned name against a cap under its plan" }

Sec "3  the cash card buys what the plan is short of"
$cash = @($cards | Where-Object { "$($_.apply.kind)" -eq 'redeploy_cash' })
if ($cash.Count -eq 0) { Skip ("no cash card today (cash {0:N0})" -f [double]$pf.cash_ils) }
foreach ($c in $cash) {
    Note ("'{0}': {1}" -f $c.title, ((@($c.apply.legs) | ForEach-Object { "{0} {1:N0}" -f $_.ticker, [double]$_.amount_ils }) -join ', '))
    foreach ($leg in @($c.apply.legs)) {
        $t = "$($leg.ticker)".ToUpper()
        $amt = [double]$leg.amount_ils
        if ($coreBasketed -and -not $plan.ContainsKey($t)) { Bad ("leg {0} {1:N0}: not a name the plan holds" -f $t, $amt); continue }
        if (-not $plan.ContainsKey($t)) { Skip ("leg {0}: no per-name plan on this book" -f $t); continue }
        $after = 100.0 * (($(if ($held.ContainsKey($t)) { $held[$t] } else { 0.0 })) + $amt) / $nav
        $top = $plan[$t] + $(if ($bandW.ContainsKey($t)) { $bandW[$t] } else { 0.0 })
        if ($after -le $top + 0.05) { Ok ("leg {0} {1:N0}: {2:N1}% after, plan {3:N1}% (trims above {4:N1}%)" -f $t, $amt, $after, $plan[$t], $top) }
        else { Bad ("leg {0} {1:N0} takes it to {2:N1}%, past the {3:N1}% top of its band (plan {4:N1}%)" -f $t, $amt, $after, $top, $plan[$t]) }
    }
}

Sec "4  no card buys a new name the core realign would sell"
if (-not $coreBasketed) { Skip "the core holds no basket: nothing sells an off-basket name" }
else {
    $offBad = 0
    $shown = @($cards + @($heads | ForEach-Object { $_.card }))
    foreach ($c in $shown) {
        if ("$($c.apply.kind)" -ne 'buy_funded') { continue }
        $t = "$($c.apply.ticker)".ToUpper()
        if ($plan.ContainsKey($t) -or $held.ContainsKey($t)) { continue }
        if ("$($c.id)" -like 'rule_*') { continue }
        # Only the screener's idea cards are in this PR; a war-room buy is BACKLOG C40.
        if ("$($c.dimension)" -ne 'opportunity') { Note ("'{0}' ({1}) buys {2}, outside the basket -- not this PR (C40)" -f $c.title, $c.dimension, $t); continue }
        Bad ("'{0}' buys {1}: not in the core basket, not in a sleeve" -f $c.title, $t); $offBad++
    }
    if ($offBad -eq 0) { Ok "no idea card opens a position outside the plan" }
}

Sec "5  a take-them-together note points at a card on Today"
$onToday = @{}; foreach ($c in $cards) { $onToday["$($c.id)"] = 1 }
$noteBad = 0; $pairs = 0
foreach ($k in @(@($r.contradictions) | Where-Object { $_.kind -eq 'buys_and_sells_one_class' })) {
    $pairs++
    foreach ($id in @("$($k.card)", "$($k.other)")) {
        $partner = $(if ($id -eq "$($k.card)") { "$($k.other)" } else { "$($k.card)" })
        $c = @($cards | Where-Object { "$($_.id)" -eq $id })
        if ($c.Count -eq 0) { continue }
        $n = @(@($c[0].notes) | Where-Object { "$_" -like 'Another card *' })
        if ($n.Count -gt 0 -and -not $onToday.ContainsKey($partner)) { Bad ("'{0}' says '{1}' -- the other card ({2}) is not on Today" -f $c[0].title, $n[0], $partner); $noteBad++ }
    }
}
if ($noteBad -eq 0) { Ok ("{0} buy/sell pair(s); every note points at a visible card" -f $pairs) }

Sec "6  NOT CHANGED"
if ($r -and $null -ne $r.recommendations) { Ok ("Today builds: {0} card(s), {1} heads-up(s)" -f @($r.recommendations).Count, $heads.Count) } else { Bad "Today did not build" }
if (@($r.degraded).Count -eq 0) { Ok "no degraded agent" } else { Bad ("degraded: {0}" -f (@($r.degraded) -join ',')) }
$bt = $r.book_target.target; $mt = $mix.target_allocation
$same = $true
foreach ($p in $bt.PSObject.Properties) { if ([math]::Abs([double]$p.Value - [double]$mt.($p.Name)) -gt 0.0005) { $same = $false } }
if ($same) { Ok "the book target on Today equals /mix (class targets untouched)" } else { Bad "the book target on Today and /mix disagree" }
if ($bandW.Count -gt 0) { Ok ("{0} band rule(s) answer with a plan target" -f $bandW.Count) } else { Skip "no band rule" }
foreach ($x in @(@($rules.rules) | Where-Object { $_.rule_type -eq 'max_weight' -and "$($_.strategy_id)" -ne 'sleeve' })) {
    Note ("hand-set cap (never re-levelled by this PR): {0} at {1:N1}%" -f $x.ticker, [double]$x.level)
}
Stop-Smoke
