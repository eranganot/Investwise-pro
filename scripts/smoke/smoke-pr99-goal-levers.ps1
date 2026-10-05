# SMOKE - PR 99: the goal cards only promise what Accept delivers.
#
#   .\scripts\smoke\smoke-pr99-goal-levers.ps1 -Sha abc1234
#
#   1  POST /plan/frontier/refresh solves the goal (a MEASUREMENT: one
#      solver_verdicts row, never the book)
#   2  ONE headline: with rules = the rules replay as armed, rules-off beside it;
#      without a replay = the lower of model and history
#   3  suggestions are sized on the book after "Retune your protection" when that
#      card exists, else on the rules as they are
#   4  RE-DERIVED: a deposit / deadline lever, when offered, reaches 70% on the
#      goal AS IT WOULD BE after Accept (the goal grows with deposits and years);
#      when neither is offered on a goal the book cannot out-earn, the
#      money-and-time line says so
#   5  Today: no deposit / deadline card without its lever; every goal card quotes
#      the headline; the projection quotes the same headline
#   6  nothing was executed
#
# DOES NOT ACCEPT ANY CARD. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 99"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

$slBefore = Api GET "/api/v1/plan/sleeves"
$pfBefore = Api GET "/api/v1/portfolio"
$plan = Api GET "/api/v1/plan"

Sec "1  solve for the goal"
$t0 = Get-Date
$v = Api POST "/api/v1/plan/frontier/refresh" 900
if ($null -eq $v -or -not $v.ok -or $v.mode -ne 'goal') { Bad ("refresh failed: {0} {1} {2}" -f $v.mode, $v.reason, $v.detail); Stop-Smoke }
Ok ("solved in {0}s: outcome {1}, material {2}" -f [int]((Get-Date) - $t0).TotalSeconds, $v.outcome, $v.material)
$g = $v.goal; $lv = $v.levers; $hl = $v.headline; $rb = $v.rules
Note ("goal {0} by {1} ({2}%/yr on what you put in), nav {3}, monthly {4}" -f $g.target, $g.deadline, $g.roi_pct, $g.nav, $g.monthly)
Note ("levers: {0}" -f ($lv | ConvertTo-Json -Compress))

Sec "2  one headline"
if ($null -eq $hl) { Bad "the verdict carries no headline" }
elseif ($rb -and $rb.on) {
    if ([double]$hl.p_goal_pct -eq [double]$rb.on.p_goal_pct -and [double]$hl.without_rules_pct -eq [double]$rb.off.p_goal_pct -and $hl.basis -eq 'rules') { Ok ("headline {0}% with your rules ({1}% without) - the replay's own figures" -f $hl.p_goal_pct, $hl.without_rules_pct) }
    else { Bad ("headline {0}/{1} vs replay on {2} / off {3}" -f $hl.p_goal_pct, $hl.without_rules_pct, $rb.on.p_goal_pct, $rb.off.p_goal_pct) }
} else {
    $want = [math]::Min([double]$v.before.goal_probability_pct, [double]$v.tail.before.p_goal_pct)
    if ([double]$hl.p_goal_pct -eq $want) { Ok "no rules replay: headline = lower of model and history ($want%)" } else { Bad "headline $($hl.p_goal_pct)% vs $want%" }
}

Sec "3  what the suggestions are sized on"
$tu = if ($rb) { $rb.tuning } else { $null }
$wantSized = if ($tu -and $tu.material) { 'after_retune' } elseif ($rb -and $rb.on) { 'rules_as_they_are' } else { 'no_rules' }
if ($lv.sized_on -eq $wantSized) { Ok ("sized on {0}: {1}% chance" -f $lv.sized_on, $lv.sized_p_goal_pct) } else { Bad "sized on $($lv.sized_on), expected $wantSized" }
if ($wantSized -eq 'after_retune' -and [double]$lv.sized_p_goal_pct -ne [double]$tu.best.stats.p_goal_pct) { Bad "sized chance is not the retuned book's" }
if ($lv.amount_at_confidence -ne $null) {
    $amts = @($lv.amount_at_confidence_model, $lv.amount_at_confidence_history, $lv.amount_at_confidence_rules) | Where-Object { $_ -ne $null } | ForEach-Object { [double]$_ }
    if ([double]$lv.amount_at_confidence -eq ($amts | Measure-Object -Minimum).Minimum) { Ok ("target lever amount {0} = smallest of model / history / rules ({1})" -f $lv.amount_at_confidence, ($amts -join ' / ')) }
    else { Bad "target lever amount is not the smallest basis" }
}

Sec "4  re-derived: the goal after Accept"
$tmpV = Join-Path $env:TEMP "iw-pr99-verdict.json"
$v | ConvertTo-Json -Depth 12 | Set-Content -Path $tmpV -Encoding ASCII
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = @"
import json, sys; sys.path.insert(0, r'$repo')
from app.engines import frontier as fr
from app.services import frontier_service as fs
v = json.load(open(r'$tmpV')); g = v['goal']; lv = v['levers']
base = v['after'] if v['material'] else v['before']
roi = g['roi_pct'] / 100.0
vol = base['volatility_pct'] / 100.0
dep = lv.get('extra_monthly_ils')
if dep:
    t = g['target'] + fs._fv_monthly(dep, roi, g['years'])
    p = fr.goal_probability(g['nav'], t, g['years'], base['log_growth'], vol, g['monthly'] + dep) * 100
    print('DEP', round(t), lv.get('deposit_goal_after'), round(p, 1))
ext = lv.get('deadline_extra_years')
if ext:
    t = g['target'] * (1 + roi) ** ext
    p = fr.goal_probability(g['nav'], t, g['years'] + ext, base['log_growth'], vol, g['monthly']) * 100
    print('EXT', round(t), lv.get('deadline_goal_after'), round(p, 1))
frozen = fr.goal_probability(g['nav'], g['target'], g['years'], base['log_growth'], vol, g['monthly'] + 680) * 100
grown = fr.goal_probability(g['nav'], g['target'] + fs._fv_monthly(680, roi, g['years']), g['years'], base['log_growth'], vol, g['monthly'] + 680) * 100
print('V1', round(frozen, 1), round(grown, 1), round(g['target'] + fs._fv_monthly(680, roi, g['years'])))
"@
$res = @($py | python - 2>&1)
if ($LASTEXITCODE -ne 0) { Bad "re-derivation failed: $res" }
else {
    foreach ($line in $res) {
        $p = "$line".Trim().Split(' ')
        if ($p[0] -eq 'DEP') { if ([math]::Abs([double]$p[1] - [double]$p[2]) -le 1 -and [double]$p[3] -ge 69.9) { Ok ("deposit lever: goal becomes {0}, model chance {1}%" -f $p[1], $p[3]) } else { Bad "deposit lever: goal $($p[1]) vs card $($p[2]), chance $($p[3])%" } }
        if ($p[0] -eq 'EXT') { if ([math]::Abs([double]$p[1] - [double]$p[2]) -le 1 -and [double]$p[3] -ge 69.9) { Ok ("deadline lever: goal becomes {0}, model chance {1}%" -f $p[1], $p[3]) } else { Bad "deadline lever: goal $($p[1]) vs card $($p[2]), chance $($p[3])%" } }
        if ($p[0] -eq 'V1') { Note ("the v1 card's +680/month: {0}% with the goal frozen, {1}% on the grown goal ({2})" -f $p[1], $p[2], $p[3]) }
    }
}
Remove-Item $tmpV -ErrorAction SilentlyContinue
if (-not $lv.extra_monthly_ils -and -not $lv.deadline_extra_years -and [double]$lv.sized_p_goal_pct -lt 70) {
    if ($lv.money_time_note) { Ok ("no money/time lever; the note says: asks {0}%/yr, book expected ~{1}%" -f $lv.money_time_note.roi_pct, $lv.money_time_note.expected_cagr_pct) }
    elseif ([double]$lv.on_track_cagr_pct -ge [double]$g.roi_pct) { Skip "no lever although the book out-earns the goal's ROI (history or rules bind)" }
    else { Bad "no money/time lever and no note explaining why" }
}

Sec "5  Today and the projection"
$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$goalCards = @($all | Where-Object { $_.dimension -eq 'goal' })
foreach ($c in $goalCards) { Note ("[{0}] {1}" -f $c.id, $c.title) }
$dep = $goalCards | Where-Object { $_.meta.lever -eq 'deposit' }
$ddl = $goalCards | Where-Object { $_.meta.lever -eq 'deadline' }
if (($null -ne $dep) -eq [bool]$lv.extra_monthly_ils) { Ok ("deposit card {0}" -f $(if ($dep) { "present: $($dep.title)" } else { 'absent' })) } else { Bad "deposit card presence does not match the lever" }
if (($null -ne $ddl) -eq [bool]($lv.deadline_extra_years -and [double]$lv.deadline_extra_years -le 15)) { Ok ("deadline card {0}" -f $(if ($ddl) { "present: $($ddl.title)" } else { 'absent' })) } else { Bad "deadline card presence does not match the lever" }
$hlText = "Your chance today: {0}%" -f ([double]$hl.p_goal_pct).ToString('0.#', [Globalization.CultureInfo]::InvariantCulture)
$miss = @($goalCards | Where-Object { "$($_.why)" -notlike "*$hlText*" })
if ($goalCards.Count -eq 0) { Skip "no goal cards" } elseif ($miss.Count -eq 0) { Ok "every goal card quotes '$hlText'" } else { Bad ("{0} goal card(s) do not quote the headline" -f $miss.Count) }
if ($lv.money_time_note -and $goalCards.Count -gt 0) {
    if ("$($goalCards[0].why)" -match "Adding money or time doesn't help this goal") { Ok "goal summary carries the money-and-time line" } else { Bad "money-and-time line missing" }
}
$proj = Api GET "/api/v1/plan/projection"
if ($proj.goal_headline -and [double]$proj.goal_headline.p_goal_pct -eq [double]$hl.p_goal_pct) { Ok ("projection quotes the headline: {0}% (its own no-rules run: {1}%)" -f $proj.goal_headline.p_goal_pct, [math]::Round([double]$proj.probability_meets_target * 100, 1)) }
else { Bad ("projection headline {0} vs verdict {1}" -f $proj.goal_headline.p_goal_pct, $hl.p_goal_pct) }

Sec "6  nothing was executed"
$slAfter = Api GET "/api/v1/plan/sleeves"
$pfAfter = Api GET "/api/v1/portfolio"
$a1 = (@($slBefore.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
$a2 = (@($slAfter.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
if ($a1 -eq $a2) { Ok "sleeves unchanged" } else { Bad "sleeves moved: $a1 -> $a2" }
$q1 = (@($pfBefore.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
$q2 = (@($pfAfter.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
if ($q1 -eq $q2) { Ok "holdings unchanged" } else { Bad "holdings moved" }

Stop-Smoke
