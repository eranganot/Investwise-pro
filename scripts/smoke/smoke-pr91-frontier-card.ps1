# SMOKE - PR 91: the frontier card. Resampled, shrink-only, Accept executes;
# the user's Kelly fraction and return confidence; measured against his target.
#
#   .\scripts\smoke\smoke-pr91-frontier-card.ps1 -Sha abc1234
#
#   0b GET /plan reports the effective dials
#   1  POST /plan/frontier/refresh solves the real book and stores a verdict
#      (writes ONE solver_verdicts row - a measurement, never the book)
#   2  GET /plan/frontier serves it back with its age
#   3  the Today card matches the verdict: same sleeves, sells only the tickers
#      of the sleeves being shrunk, buys only the core basket, no `sells` leg
#   4  THE CLAIM, RE-DERIVED: the card's before/after Kelly and volatility are
#      recomputed here from production's own inputs with the engine
#   5  nothing was executed: sleeves, holdings and target are unchanged
#
# DOES NOT ACCEPT THE CARD. Accepting sells real positions; that is Eran's tap.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 91"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(PR 90's migration, which this PR reads)"

Sec "0b the dials"
$plan = Api GET "/api/v1/plan"
$dials = $plan.frontier
if ($dials -and $dials.kelly_fraction) { Ok ("Kelly {0}x ({1}; default {2}x), confidence {3} ({4}), range {5}" -f $dials.kelly_fraction, $dials.kelly_source, $dials.kelly_default, $dials.return_confidence, $dials.confidence_source, ($dials.kelly_range -join '-')) }
else { Bad "GET /plan carries no frontier dials" }

$slBefore = Api GET "/api/v1/plan/sleeves"
$mixBefore = Api GET "/api/v1/mix"
$pfBefore = Api GET "/api/v1/portfolio"

Sec "1  solve the real book"
$t0 = Get-Date
$v = Api POST "/api/v1/plan/frontier/refresh" 600
$secs = [int]((Get-Date) - $t0).TotalSeconds
if ($null -eq $v) { Bad "/plan/frontier/refresh did not answer"; Stop-Smoke }
if (-not $v.ok) { Bad ("not ok: {0} {1}" -f $v.reason, $v.detail); Stop-Smoke }
Ok ("solved in {0}s: outcome {1}, {2} draws, {3} sessions, fx {4}" -f $secs, $v.outcome, $v.draws, $v.window.sessions, $v.fx_basis)
foreach ($s in @($v.sleeves)) {
    Note ("{0,-16} today {1,6:N2}%  proposed {2,6:N2}%  band {3:N2}-{4:N2}%  act {5}" -f $s.id, [double]$s.current_pct, [double]$s.proposed_pct, [double]$s.band_pct[0], [double]$s.band_pct[1], $s.act)
    if ([double]$s.proposed_pct -gt [double]$s.current_pct + 1e-9) { Bad "$($s.id): proposes GROWING a sleeve" }
    if ($s.act -and [double]$s.current_pct -le [double]$s.band_pct[1]) { Bad "$($s.id): acts although today is inside its band" }
}
Note ("before: {0}x Kelly, vol {1}%, growth {2}%/yr" -f $v.before.fraction_of_kelly, $v.before.volatility_pct, $v.before.growth_cagr_pct)
Note ("after:  {0}x Kelly, vol {1}%, growth {2}%/yr  (target {3}x)" -f $v.after.fraction_of_kelly, $v.after.volatility_pct, $v.after.growth_cagr_pct, $v.kelly_target)
if ($v.outcome -eq 'RESIZE') {
    if ([math]::Abs([double]$v.after.fraction_of_kelly - [double]$v.kelly_target) -lt [math]::Abs([double]$v.before.fraction_of_kelly - [double]$v.kelly_target)) { Ok "the resize moves the book toward its Kelly target" }
    else { Bad "the resize does not move the book toward its Kelly target" }
    if ($v.materiality.material) { Ok ("material: closes {0} of the gap, vol -{1} pts" -f $v.materiality.gap_closed, $v.materiality.vol_drop_pct) } else { Bad "RESIZE without materiality" }
}
if ([double]$v.kelly_fraction -eq [double]$dials.kelly_fraction -and $v.confidence -eq $dials.return_confidence) { Ok ("solved under your dials: {0}x, {1}" -f $v.kelly_fraction, $v.confidence) }
else { Bad ("solved under {0}x/{1}, plan says {2}x/{3}" -f $v.kelly_fraction, $v.confidence, $dials.kelly_fraction, $dials.return_confidence) }
$scn = @($v.scenarios.PSObject.Properties.Name)
if ($scn.Count -eq 3) { foreach ($n in $scn) { $s = $v.scenarios.$n; Note ("{0,-9} {1}% -> {2}%/yr (max {3}%)" -f $n, $s.before_growth_pct, $s.after_growth_pct, $s.max_growth_pct) }; Ok "all three confidences scored" }
else { Bad "scenarios: $($scn -join ',')" }
if ($plan.target_roi_pct) {
    if ($v.target -and [double]$v.target.roi_pct -eq [double]$plan.target_roi_pct) { Ok ("measured against your {0}%/yr target: reachable on {1}? {2}" -f $v.target.roi_pct, $v.target.confidence, $v.target.reachable) }
    else { Bad "the verdict ignores the plan's ROI target" }
}

Sec "2  the stored verdict"
$lv = Api GET "/api/v1/plan/frontier"
if ($lv -and $lv.ok -and $lv.outcome -eq $v.outcome -and [double]$lv.age_hours -lt 1) { Ok ("served back: {0}, {1}h old, source {2}" -f $lv.outcome, $lv.age_hours, $lv.source) }
else { Bad "GET /plan/frontier did not serve the fresh verdict" }

Sec "3  the Today card"
$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$card = $all | Where-Object { $_.apply.kind -eq 'frontier_resize' } | Select-Object -First 1
if ($v.outcome -ne 'RESIZE') {
    if ($null -eq $card) { Ok "no card - the verdict is $($v.outcome)" } else { Bad "a card exists for a $($v.outcome) verdict" }
} elseif ($null -eq $card) { Bad "RESIZE verdict but no Today card" }
else {
    Ok "card: $($card.title)"
    Note "action: $($card.action)"
    Note "impact: $($card.impact)"
    Note "why: $($card.why)"
    if ($plan.target_roi_pct) {
        # Compared as NUMBERS: PS 5.1 reads JSON 30.0 as a decimal and prints
        # "30.0", the card prints "30" - a text match failed on the first run.
        if ("$($card.why)" -match 'Your target is ([0-9.]+)%/yr' -and [double]$Matches[1] -eq [double]$plan.target_roi_pct) { Ok ("the card names your ROI target ({0}%/yr)" -f $Matches[1]) }
        else { Bad "the card does not mention your ROI target" }
    }
    # $null-guarded: in PS 5.1 @($null).Count is 1, so a MISSING key read as
    # one sell leg on the first run.
    if ($null -ne $card.apply.sells -and @($card.apply.sells).Count -gt 0) { Bad 'card carries a sells funding leg' } else { Ok "no funding raid: the card resizes, it does not fund itself from a sleeve" }
    $acting = @(@($v.sleeves) | Where-Object { $_.act } | ForEach-Object { $_.id })
    $cardIds = @(@($card.apply.resizes) | ForEach-Object { $_.strategy_id })
    if ((@(Compare-Object $acting $cardIds)).Count -eq 0) { Ok ("card resizes exactly the acting sleeves: {0}" -f ($cardIds -join ', ')) }
    else { Bad ("card resizes {0}, verdict acts on {1}" -f ($cardIds -join ','), ($acting -join ',')) }
    $allowed = @(@($card.apply.resizes) | ForEach-Object { @($_.tickers) }) | ForEach-Object { "$_".ToUpper() }
    $badSell = @(@($card.apply.estimate.sells) | Where-Object { $allowed -notcontains "$($_.ticker)".ToUpper() })
    if ($badSell.Count -eq 0) { Ok ("sells only the shrinking sleeve's tickers: {0}" -f ((@($card.apply.estimate.sells) | ForEach-Object { "$($_.shares) $($_.ticker)" }) -join ', ')) }
    else { Bad ("sells outside the resized sleeve: {0}" -f (($badSell | ForEach-Object { $_.ticker }) -join ',')) }
    $basket = @($v.core_basket.PSObject.Properties.Name | ForEach-Object { "$_".ToUpper() })
    $badBuy = @(@($card.apply.estimate.buys) | Where-Object { $basket -notcontains "$($_.ticker)".ToUpper() })
    if ($badBuy.Count -eq 0) { Ok ("buys only the core basket: {0}" -f ((@($card.apply.estimate.buys) | ForEach-Object { "$($_.ticker) $($_.amount_ils)" }) -join ', ')) }
    else { Bad "buys outside the core basket" }
    Note ("estimated tax {0}, net raised {1}" -f $card.apply.estimate.tax_ils, $card.apply.estimate.net_ils)
}

Sec "4  the claim, re-derived from production's inputs"
if ($v.outcome -eq 'RESIZE') {
    $fi = Api GET "/api/v1/plan/frontier/inputs" 600
    $tmpI = Join-Path $env:TEMP "iw-pr90-inputs.json"; $tmpV = Join-Path $env:TEMP "iw-pr90-verdict.json"
    $fi | ConvertTo-Json -Depth 12 | Set-Content -Path $tmpI -Encoding ASCII
    $v | ConvertTo-Json -Depth 12 | Set-Content -Path $tmpV -Encoding ASCII
    $repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
    $py = @"
import json, sys; sys.path.insert(0, r'$repo')
import numpy as np
from app.engines import frontier as fr
from app.services import frontier_service as fs
d = json.load(open(r'$tmpI')); v = json.load(open(r'$tmpV'))
ids = [a['id'] for a in d['assets']]; mu = np.array(d['mu']); cov = np.array(d['cov']); rf = d['risk_free']['drift']
core = fs.core_vector(v['core_basket'], ids); cash = v['cash_pct'] / 100.0
def book(key):
    w = np.zeros(len(ids)); tot = 0.0
    for s in v['sleeves']:
        x = (s['proposed_pct'] if (key == 'after' and s['act']) else s['current_pct']) / 100.0
        w[ids.index(s['id'])] = x; tot += x
    w += core * (1.0 - cash - tot); w[ids.index('cash')] += cash
    return w
for key in ('before', 'after'):
    w = book(key); k = fr.kelly_position(w, mu, cov, rf)['fraction_of_kelly']; vol = fr.portfolio_stats(w, mu, cov)['volatility'] * 100
    print(key, round(k, 3), round(vol, 2), v[key]['fraction_of_kelly'], v[key]['volatility_pct'])
"@
    $res = @($py | python - 2>&1)
    if ($LASTEXITCODE -ne 0) { Bad "re-derivation failed: $res" }
    else {
        foreach ($line in $res) {
            $p = "$line".Trim().Split(' ')
            Note ("{0}: recomputed {1}x / {2}%  vs card {3}x / {4}%" -f $p)
            if ([math]::Abs([double]$p[1] - [double]$p[3]) -le 0.03 -and [math]::Abs([double]$p[2] - [double]$p[4]) -le 0.5) { Ok "$($p[0]) claim re-derived from the post-trade weights" }
            else { Bad ("{0} claim does not re-derive (card {1}x/{2}%, recomputed {3}x/{4}%)" -f $p[0], $p[3], $p[4], $p[1], $p[2]) }
        }
    }
    Remove-Item $tmpI, $tmpV -ErrorAction SilentlyContinue
} else { Skip "no RESIZE verdict - no claim to re-derive" }

Sec "5  nothing was executed"
$slAfter = Api GET "/api/v1/plan/sleeves"
$mixAfter = Api GET "/api/v1/mix"
$pfAfter = Api GET "/api/v1/portfolio"
$a1 = (@($slBefore.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
$a2 = (@($slAfter.sleeves) | ForEach-Object { "$($_.strategy_id)=$($_.sleeve_pct)" }) -join ','
if ($a1 -eq $a2) { Ok "sleeves unchanged: $a2" } else { Bad "sleeves moved: $a1 -> $a2" }
$q1 = (@($pfBefore.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
$q2 = (@($pfAfter.positions) | ForEach-Object { "$($_.ticker)=$($_.quantity)" } | Sort-Object) -join ','
if ($q1 -eq $q2) { Ok "holdings unchanged" } else { Bad "holdings moved: $q1 -> $q2" }
if (($mixBefore.target_allocation | ConvertTo-Json -Compress) -eq ($mixAfter.target_allocation | ConvertTo-Json -Compress)) { Ok "target unchanged" } else { Bad "target moved" }

Stop-Smoke
