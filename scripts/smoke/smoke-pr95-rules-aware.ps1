# SMOKE - PR 95: blended (prior = 5 years) is the default; the protective rules
# are replayed on the goal's paths (PR 92-94 checks + the rules + the stress case).
#
#   .\scripts\smoke\smoke-pr95-rules-aware.ps1 -Sha abc1234
#
#   0c the default confidence is blended (no plan override)
#   3d the verdict replays the ARMED rules on identical paths: off / as today /
#      the rebound fix; the effect is on-minus-off; the shrunk stress case is
#      present; cards state the rules and the stress case
#
#   3c every catalog core priced from its own tickers is scored by the goal's odds
#      (lower of model and history); unpriced cores are listed; a switch card
#      exists only for SWITCH; goal cards say how much the core can move the odds
#
#   3b the verdict carries the history check: odds, worst 5%, halving chance;
#      a trade card never lowers the odds on history; levers hold both ways
#      (deposit >= the model's, amount <= the model's); cards say so
#
#   0b goal mode is on: the goal picks the risk, Kelly is a ceiling (1.0x unless
#      the user set lower), 'on track' = a 70% chance
#   1  POST /plan/frontier/refresh solves the real book FOR THE GOAL
#      (writes ONE solver_verdicts row - a measurement, never the book)
#   2  the verdict's goal is /plan's goal; outcome consistent with its numbers
#   3  RE-DERIVED: today's goal probability from production's inputs with the
#      engine, and the deposit lever really reaches 70% (and 10 less does not)
#   4  the Today cards match the outcome; the older behind-your-goal card has
#      stood down (one goal story)
#   5  nothing was executed
#
# DOES NOT ACCEPT ANY CARD. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 95"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

Sec "0b goal mode"
$plan = Api GET "/api/v1/plan"
$f = $plan.frontier
if ($f.mode -eq 'goal') { Ok ("goal mode: ceiling {0}x ({1}), confidence {2}, on track = {3}% chance" -f $f.kelly_fraction, $f.kelly_source, $f.return_confidence, ([double]$f.goal_confidence * 100)) }
else { Bad "frontier mode is '$($f.mode)' although the plan has a target ($($plan.target_roi_pct)%)" }

$slBefore = Api GET "/api/v1/plan/sleeves"
$pfBefore = Api GET "/api/v1/portfolio"

Sec "1  solve for the goal"
$t0 = Get-Date
$v = Api POST "/api/v1/plan/frontier/refresh" 900
$secs = [int]((Get-Date) - $t0).TotalSeconds
if ($null -eq $v -or -not $v.ok) { Bad ("refresh failed: {0} {1}" -f $v.reason, $v.detail); Stop-Smoke }
if ($v.mode -ne 'goal') { Bad "verdict mode '$($v.mode)', expected goal"; Stop-Smoke }
Ok ("solved in {0}s: outcome {1}, material {2}, {3} draws, fx {4}" -f $secs, $v.outcome, $v.material, $v.draws, $v.fx_basis)
foreach ($s in @($v.sleeves)) {
    Note ("{0,-16} {1,6:N2}% -> {2,6:N2}%  band {3:N2}-{4:N2}%  act {5} {6}" -f $s.id, [double]$s.current_pct, [double]$s.proposed_pct, [double]$s.band_pct[0], [double]$s.band_pct[1], $s.act, $s.direction)
}
Note ("before: P(goal) {0}%  median {1}  growth {2}%  vol {3}%  kelly {4}x" -f $v.before.goal_probability_pct, $v.before.median_at_deadline, $v.before.growth_cagr_pct, $v.before.volatility_pct, $v.before.fraction_of_kelly)
Note ("after:  P(goal) {0}%  median {1}  growth {2}%  vol {3}%  kelly {4}x" -f $v.after.goal_probability_pct, $v.after.median_at_deadline, $v.after.growth_cagr_pct, $v.after.volatility_pct, $v.after.fraction_of_kelly)
Note ("levers: {0}" -f ($v.levers | ConvertTo-Json -Compress))

Sec "2  the goal is the plan's goal"
$g = $v.goal
if ([math]::Abs([double]$g.target - [double]$plan.goal.target) -lt 1) { Ok ("target {0} by {1} ({2}%/yr), from {3} today: needs {4}%/yr" -f $g.target, $g.deadline, $g.roi_pct, $g.nav, $v.levers.required_cagr_pct) }
else { Bad ("verdict target {0} vs /plan {1}" -f $g.target, $plan.goal.target) }
$star = [double]$v.p_star * 100
$cap = [double]$v.kelly_ceiling
if ([math]::Abs($cap - [double]$f.kelly_fraction) -lt 1e-6) { Ok "solved under the plan's ceiling ($cap x)" } else { Bad "ceiling $cap vs plan $($f.kelly_fraction)" }
switch ($v.outcome) {
    'ON_TRACK'     { if ([double]$v.before.goal_probability_pct -ge $star) { Ok "ON_TRACK and today is >= $star%" } else { Bad "ON_TRACK but today is $($v.before.goal_probability_pct)%" } }
    'LESS_RISK'    { if ([double]$v.after.goal_probability_pct -ge $star -and [double]$v.after.volatility_pct -lt [double]$v.before.volatility_pct) { Ok "LESS_RISK keeps >= $star% with less volatility" } else { Bad "LESS_RISK inconsistent" } }
    'REBALANCE'    { if ([double]$v.after.goal_probability_pct -ge $star) { Ok "REBALANCE reaches >= $star%" } else { Bad "REBALANCE does not reach $star%" } }
    'IMPROVE'      { if ([double]$v.after.goal_probability_pct - [double]$v.before.goal_probability_pct -ge 5) { Ok "IMPROVE raises the odds >= 5 pts" } else { Bad "IMPROVE does not raise the odds" } }
    'OUT_OF_REACH' { if (-not $g.reachable_within_ceiling -or -not $v.material) { Ok "OUT_OF_REACH: no trade within the cap reaches or materially helps" } else { Bad "OUT_OF_REACH inconsistent" } }
    default        { Skip "outcome $($v.outcome)" }
}
foreach ($s in @($v.sleeves)) { if ([double]$s.proposed_pct -gt [math]::Max(40.0, [double]$s.current_pct) + 1e-6) { Bad "$($s.id) proposed above the concentration cap" } }

Sec "3  re-derived"
$fi = Api GET "/api/v1/plan/frontier/inputs" 900
$tmpI = Join-Path $env:TEMP "iw-pr92-inputs.json"; $tmpV = Join-Path $env:TEMP "iw-pr92-verdict.json"
$fi | ConvertTo-Json -Depth 12 | Set-Content -Path $tmpI -Encoding ASCII
$v | ConvertTo-Json -Depth 12 | Set-Content -Path $tmpV -Encoding ASCII
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = @"
import json, sys; sys.path.insert(0, r'$repo')
import numpy as np
from app.engines import frontier as fr
from app.services import frontier_service as fs
d = json.load(open(r'$tmpI')); v = json.load(open(r'$tmpV')); g = v['goal']
ids = [a['id'] for a in d['assets']]; mu = np.array(d['mu']); cov = np.array(d['cov']); rf = d['risk_free']['drift']
core = fs.core_vector(v['core_basket'], ids); cash = v['cash_pct'] / 100.0
w = np.zeros(len(ids)); tot = 0.0
for s in v['sleeves']:
    w[ids.index(s['id'])] = s['current_pct'] / 100.0; tot += s['current_pct'] / 100.0
w += core * (1.0 - cash - tot); w[ids.index('cash')] += cash
lg = fr.growth(w, mu, cov, rf)['log_growth']; vol = fr.portfolio_stats(w, mu, cov)['volatility']
p = fr.goal_probability(g['nav'], g['target'], g['years'], lg, vol, g['monthly']) * 100
print('P', round(p, 1), v['before']['goal_probability_pct'])
lv = v['levers']; base = v['after'] if v['material'] else v['before']
if lv.get('extra_monthly_model_ils'):
    m = g['monthly'] + lv['extra_monthly_model_ils']; bv = base['volatility_pct'] / 100.0
    hit = fr.goal_probability(g['nav'], g['target'], g['years'], base['log_growth'], bv, m) * 100
    miss = fr.goal_probability(g['nav'], g['target'], g['years'], base['log_growth'], bv, m - 10) * 100
    print('DEPOSIT', round(hit, 2), round(miss, 2), v['p_star'] * 100)
"@
$res = @($py | python - 2>&1)
if ($LASTEXITCODE -ne 0) { Bad "re-derivation failed: $res" }
else {
    foreach ($line in $res) {
        $p = "$line".Trim().Split(' ')
        if ($p[0] -eq 'P') {
            if ([math]::Abs([double]$p[1] - [double]$p[2]) -le 1.0) { Ok ("today's goal probability re-derived: {0}% vs verdict {1}%" -f $p[1], $p[2]) }
            else { Bad ("goal probability does not re-derive: {0}% vs verdict {1}%" -f $p[1], $p[2]) }
        }
        if ($p[0] -eq 'DEPOSIT') {
            if ([double]$p[1] -ge [double]$p[3] - 0.01 -and [double]$p[2] -lt [double]$p[3]) { Ok ("deposit lever: {0}% with it, {1}% with 10 less (bar {2}%)" -f $p[1], $p[2], $p[3]) }
            else { Bad ("deposit lever wrong: {0}% with it, {1}% with 10 less" -f $p[1], $p[2]) }
        }
    }
}
Remove-Item $tmpI, $tmpV -ErrorAction SilentlyContinue

Sec "3b the history check"
$tl = $v.tail
if ($null -eq $tl -or $null -eq $tl.before) { Bad "the verdict carries no history check" }
else {
    $tb = $tl.before; $ta = $tl.after
    Note ("history before: {0}% chance, median {1}, worst 5% {2}, halving {3}%" -f $tb.p_goal_pct, $tb.median, $tb.worst5_mean, $tb.p_halve_pct)
    Note ("history after:  {0}% chance, median {1}, worst 5% {2}, halving {3}%" -f $ta.p_goal_pct, $ta.median, $ta.worst5_mean, $ta.p_halve_pct)
    $okRange = @($tb.p_goal_pct, $ta.p_goal_pct, $tb.p_halve_pct, $ta.p_halve_pct) | Where-Object { [double]$_ -lt 0 -or [double]$_ -gt 100 }
    if (@($okRange).Count -eq 0) { Ok ("{0} paths, {1}-day blocks: {2}" -f $tl.paths, $tl.block_days, $tl.basis) } else { Bad "history probabilities out of range" }
    if ($v.material -and [double]$ta.p_goal_pct -lt [double]$tb.p_goal_pct - 1.0) { Bad "a material trade lowers the odds on history" } else { Ok ("no trade card lowers the odds on history (gate: {0})" -f $(if ($tl.gate) { $tl.gate } else { 'none' })) }
    if (@('ON_TRACK', 'LESS_RISK') -contains $v.outcome -and [double]$tb.p_goal_pct -lt $star) { Bad "ON_TRACK although history says $($tb.p_goal_pct)%" } else { Ok "on-track status agrees with history" }
    $lv = $v.levers
    if ($lv.extra_monthly_ils -ne $null) {
        if ([double]$lv.extra_monthly_ils -ge [double]$lv.extra_monthly_model_ils -and [double]$lv.extra_monthly_ils -ge [double]$lv.extra_monthly_history_ils) { Ok ("deposit {0} holds both ways (model {1}, history {2})" -f $lv.extra_monthly_ils, $lv.extra_monthly_model_ils, $lv.extra_monthly_history_ils) }
        else { Bad "deposit lever is below one of its two requirements" }
        if ([double]$lv.amount_at_confidence -le [double]$lv.amount_at_confidence_model -and [double]$lv.amount_at_confidence -le [double]$lv.amount_at_confidence_history) { Ok ("reachable amount {0} is the smaller of model {1} / history {2}" -f $lv.amount_at_confidence, $lv.amount_at_confidence_model, $lv.amount_at_confidence_history) }
        else { Bad "reachable amount is not the smaller of the two" }
    }
}

Sec "3c the core choice"
$cc = $v.core_choice
if ($null -eq $cc) { Bad "the verdict carries no core choice" }
elseif ($cc.outcome -eq 'NOT_MEASURABLE') { Skip "core choice not measurable: $($cc.reason)" }
else {
    foreach ($r in @($cc.candidates)) {
        Note ("{0,-20} model {1,5}%  history {2,5}%  score {3,5}  wins {4}  kelly {5}x  eligible {6}" -f $r.id, $r.model_p_pct, $r.tail_p_pct, $r.score_pct, $r.win_share, $r.fraction_of_kelly, $r.eligible)
        if ([math]::Abs([double]$r.score_pct - [math]::Min([double]$r.model_p_pct, [double]$r.tail_p_pct)) -gt 0.05) { Bad "$($r.id): score is not the lower of model and history" }
    }
    Ok ("{0} cores scored, outcome {1}; current {2} ({3}%), best {4} ({5}%)" -f @($cc.candidates).Count, $cc.outcome, $cc.current.id, $cc.current.score_pct, $cc.best.id, $cc.best.score_pct)
    Note ("not modelled (tickers not priced): {0}" -f (@($cc.not_modelled) -join ', '))
    if ($cc.outcome -eq 'SWITCH') {
        if ([double]$cc.best.score_pct - [double]$cc.current.score_pct -ge 5 -and [double]$cc.best.win_share -ge 0.5) { Ok "SWITCH clears the materiality and stability bars" } else { Bad "SWITCH without a material, stable win" }
    } else { Ok "KEEP: no core beats the current one materially and robustly" }
}

Sec "0c the default confidence"
if ($f.confidence_source -eq 'default') {
    if ($f.return_confidence -eq 'blended') { Ok "no plan override: the default is blended (prior = 5 years)" } else { Bad "default confidence is '$($f.return_confidence)', expected blended" }
} else { Skip "the plan sets its own confidence ($($f.return_confidence))" }

Sec "3d the protective rules on the goal's paths"
$rb = $v.rules
if ($null -eq $rb) { Skip "no armed rules on this book" }
else {
    foreach ($kname in @('off', 'on', 'rebound')) {
        $x = $rb.$kname
        Note ("{0,-8} {1,5}% chance  halving {2,5}%  median {3}  exits/yr {4}  out% {5}  guard/yr {6}" -f $kname, $x.p_goal_pct, $x.p_halve_pct, $x.median, (@($x.exits_per_year) -join '/'), (@($x.pct_time_out) -join '/'), $x.guard_fires_per_year)
    }
    if ((@($rb.off.exits_per_year) | Where-Object { [double]$_ -ne 0 }).Count -eq 0 -and [double]$rb.off.guard_fires_per_year -eq 0) { Ok "rules-off run has no rule acting" } else { Bad "rules-off run shows rule activity" }
    if ([math]::Abs([double]$rb.effect_pts - ([double]$rb.on.p_goal_pct - [double]$rb.off.p_goal_pct)) -le 0.11) { Ok ("rules effect {0} pts = on {1}% - off {2}%; the rebound fix adds {3} pts" -f $rb.effect_pts, $rb.on.p_goal_pct, $rb.off.p_goal_pct, $rb.fix_effect_pts) } else { Bad "effect is not on minus off" }
    Note ("guard aims at {0}; not simulated: {1}" -f $rb.guard_target, (@($rb.not_simulated) -join ', '))
}
$st = $v.stress
if ($v.confidence -ne 'shrunk') {
    if ($st -and $st.confidence -eq 'shrunk' -and $st.before_p_pct -ne $null) { Ok ("stress case (shrunk): {0}% today" -f $st.before_p_pct) } else { Bad "no shrunk stress case on a non-shrunk verdict" }
}

Sec "4  the Today cards"
$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$goalCards = @($all | Where-Object { $_.dimension -eq 'goal' })
foreach ($c in $goalCards) { Note ("[{0}] {1}" -f $(if ($c.meta.lever) { $c.meta.lever } else { 'mix' }), $c.title) }
$mix = $goalCards | Where-Object { "$($_.id)".StartsWith('rec_goal_mix_') } | Select-Object -First 1
$wantMix = $v.material -and @('LESS_RISK', 'REBALANCE', 'IMPROVE') -contains $v.outcome
if ($wantMix -eq ($null -ne $mix)) { Ok ("mix card {0} (material {1}, outcome {2})" -f $(if ($mix) { 'present' } else { 'absent' }), $v.material, $v.outcome) }
else { Bad ("mix card presence wrong: material {0}, outcome {1}, card {2}" -f $v.material, $v.outcome, ($null -ne $mix)) }
$base = if ($v.material) { $v.after } else { $v.before }
$tailBase = if ($v.material) { $v.tail.after } else { $v.tail.before }
$effective = [math]::Min([double]$base.goal_probability_pct, [double]$tailBase.p_goal_pct)
if ($effective -lt $star) {
    $dep = $goalCards | Where-Object { $_.meta.lever -eq 'deposit' } | Select-Object -First 1
    if ($dep -and "$($dep.why)" -notmatch 'real market days') { Bad "deposit card does not state the history check" }
    if ($dep -and $dep.apply.kind -eq 'set_standing_order' -and [math]::Abs([double]$dep.apply.monthly_total_ils - ([double]$g.monthly + [double]$v.levers.extra_monthly_ils)) -lt 0.01) { Ok "deposit lever card: $($dep.title)" }
    else { Bad "no correct deposit lever card under $star%" }
    if ($v.levers.roi_at_confidence_pct -ne $null -and [double]$v.levers.roi_at_confidence_pct -lt [double]$g.roi_pct) {
        $tg = $goalCards | Where-Object { $_.meta.lever -eq 'target' } | Select-Object -First 1
        if ($tg -and [double]$tg.apply.fields.target_roi_pct -eq [double]$v.levers.roi_at_confidence_pct) { Ok "target lever card: $($tg.title)" } else { Bad "target lever card missing or wrong" }
    }
} else { if (@($goalCards | Where-Object { $_.meta.lever }).Count -eq 0) { Ok "no lever cards - the goal is on track" } else { Bad "lever cards although the goal is on track" } }
$old = @($goalCards | Where-Object { -not $_.meta.lever -and -not "$($_.id)".StartsWith('rec_goal_mix_') })
if ($old.Count -eq 0) { Ok "the older behind-your-goal card has stood down (one goal story)" } else { Bad ("older goal card still showing: {0}" -f (($old | ForEach-Object { $_.title }) -join '; ')) }
$coreCard = $goalCards | Where-Object { "$($_.id)".StartsWith('rec_goal_core_') } | Select-Object -First 1
if (($cc.outcome -eq 'SWITCH') -eq ($null -ne $coreCard)) { Ok ("core card {0} (core outcome {1})" -f $(if ($coreCard) { 'present' } else { 'absent' }), $cc.outcome) }
else { Bad ("core card presence wrong for outcome {0}" -f $cc.outcome) }
$anyGoal = $goalCards | Select-Object -First 1
if ($anyGoal -and $cc.candidates) {
    if ("$($anyGoal.why)" -match 'Changing your core') { Ok "goal cards say how much the core can move the odds" } else { Bad "goal card does not mention the core's effect" }
}
if ($anyGoal) {
    if ($rb -and "$($anyGoal.why)" -notmatch 'With your protective rules as they work today') { Bad "goal card does not state the rules replay" }
    elseif ($rb) { Ok "goal cards state the rules replay (on / off / rebound fix)" }
    if ($st -and "$($anyGoal.why)" -notmatch 'Stress case \(shrunk estimate') { Bad "goal card does not state the stress case" }
    elseif ($st) { Ok "goal cards state the shrunk stress case" }
}
$risk = @($all | Where-Object { $_.dimension -eq 'frontier' })
if ($risk.Count -eq 0) { Ok "no risk-mode (PR 91) card while a goal is set" } else { Bad "a risk-mode frontier card is still showing" }

Sec "5  nothing was executed"
$slAfter = Api GET "/api/v1/plan/sleeves"
$pfAfter = Api GET "/api/v1/portfolio"
$a1 = (@($slBefore.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
$a2 = (@($slAfter.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
if ($a1 -eq $a2) { Ok "sleeves unchanged: $a2" } else { Bad "sleeves moved: $a1 -> $a2" }
$q1 = (@($pfBefore.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
$q2 = (@($pfAfter.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
if ($q1 -eq $q2) { Ok "holdings unchanged" } else { Bad "holdings moved" }

Stop-Smoke
