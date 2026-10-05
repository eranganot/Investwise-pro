# SMOKE - PR 92: the goal is the objective; the frontier and Kelly are how.
#
#   .\scripts\smoke\smoke-pr92-goal-seeking.ps1 -Sha abc1234
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
Start-Smoke "PR 92"
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
if lv.get('extra_monthly_ils'):
    m = g['monthly'] + lv['extra_monthly_ils']; bv = base['volatility_pct'] / 100.0
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
if ([double]$base.goal_probability_pct -lt $star) {
    $dep = $goalCards | Where-Object { $_.meta.lever -eq 'deposit' } | Select-Object -First 1
    if ($dep -and $dep.apply.kind -eq 'set_standing_order' -and [math]::Abs([double]$dep.apply.monthly_total_ils - ([double]$g.monthly + [double]$v.levers.extra_monthly_ils)) -lt 0.01) { Ok "deposit lever card: $($dep.title)" }
    else { Bad "no correct deposit lever card under $star%" }
    if ($v.levers.roi_at_confidence_pct -ne $null -and [double]$v.levers.roi_at_confidence_pct -lt [double]$g.roi_pct) {
        $tg = $goalCards | Where-Object { $_.meta.lever -eq 'target' } | Select-Object -First 1
        if ($tg -and [double]$tg.apply.fields.target_roi_pct -eq [double]$v.levers.roi_at_confidence_pct) { Ok "target lever card: $($tg.title)" } else { Bad "target lever card missing or wrong" }
    }
} else { if (@($goalCards | Where-Object { $_.meta.lever }).Count -eq 0) { Ok "no lever cards - the goal is on track" } else { Bad "lever cards although the goal is on track" } }
$old = @($goalCards | Where-Object { -not $_.meta.lever -and -not "$($_.id)".StartsWith('rec_goal_mix_') })
if ($old.Count -eq 0) { Ok "the older behind-your-goal card has stood down (one goal story)" } else { Bad ("older goal card still showing: {0}" -f (($old | ForEach-Object { $_.title }) -join '; ')) }
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
