# SMOKE - PR 88: the efficient-frontier engine. Pure math, nothing wired.
#
#   .\scripts\smoke\smoke-pr88-frontier-engine.ps1 -Sha 569b71f
#
# WHAT A SMOKE CAN PROVE FOR AN UNWIRED ENGINE: that the build serving is this
# one, that the deploy did not move anything it claims not to move, and that
# the engine runs on THIS book's numbers -- not only on the test fixtures.
#
#   1  the targets the frontier will one day replace are still the fixed ones
#   2  the surfaces PR 92 will change still answer (and a baseline is saved,
#      so PR 92's smoke can show the before and after)
#   3  the engine, run locally on the live book's measured volatility, gives a
#      sane Kelly position -- the first real reading of the over-Kelly question
#
# READ-ONLY. Accepts nothing.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 88"
Assert-Build -Sha $Sha -Schema '0025_rule_params' -Why "(unchanged - PR 88 has no migration)"

Sec "1  targets are still the fixed ones"
$plan = Api GET "/api/v1/plan"
if ($null -eq $plan) { Bad "/plan did not answer" }
else {
    $names = @($plan.PSObject.Properties.Name)
    if ($names | Where-Object { $_ -match 'frontier' }) { Bad "/plan mentions a frontier - PR 88 must not wire anything" }
    else { Ok "/plan carries no frontier field" }
}
$mix = Api GET "/api/v1/mix"
if ($null -eq $mix) { Bad "/mix did not answer" }
else {
    $t = $mix.target_allocation
    if ($t) { Ok ("/mix target: {0}" -f (($t.PSObject.Properties | ForEach-Object { "{0} {1:N3}" -f $_.Name, [double]$_.Value }) -join ', ')) }
    else { Skip "/mix has no target_allocation to read (empty book?)" }
    if ("$($mix.target_source)" -match 'frontier') { Bad "target_source is '$($mix.target_source)' - PR 88 must not wire the frontier" }
    else { Ok "target_source: $($mix.target_source)" }
}

Sec "2  the surfaces PR 92 will change still answer"
$recs = Api GET "/api/v1/recommendations"
$tgt  = Api GET "/api/v1/plan/target/accepted"
if ($recs) { Ok ("/recommendations: {0} cards" -f @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ }).Count) } else { Bad "/recommendations did not answer" }
if ($tgt) { Ok "/plan/target/accepted answers" } else { Bad "/plan/target/accepted did not answer" }
$out = Join-Path $PSScriptRoot "baseline-pr88.json"
try {
    @{ taken = (Get-Date).ToString('s'); commit = $Sha; plan = $plan; mix = $mix; plan_target = $tgt } |
        ConvertTo-Json -Depth 12 | Set-Content -Path $out -Encoding ASCII
    Ok "baseline saved to scripts\smoke\baseline-pr88.json (git-ignored scratch; PR 92 diffs against it)"
} catch { Skip "could not save the baseline: $($_.Exception.Message)" }

Sec "3  the engine on this book's numbers"
# Stated forward return (a CAGR) from /plan's measured assumptions; volatility
# is the REALISED covariance figure from the risk report -- the one
# portfolio_risk.py:213 says to treat as "the one about the book you hold now".
$risk = Api POST "/api/v1/portfolio/risk"
$vol = if ($risk -and $risk.ok) { $risk.annualized_volatility_pct } else { $null }
$ret = $null
if ($plan -and $plan.assumptions) { $ret = $plan.assumptions.expected_return_pct }
if ($null -eq $ret -and $risk -and $risk.goal) { $ret = $risk.goal.drift_pct }
if ($null -eq $vol -or $null -eq $ret) {
    Skip ("cannot feed the engine: volatility {0}, stated return {1}" -f $vol, $ret)
} else {
    Note ("book: stated return {0}%/yr (CAGR), measured volatility {1}%" -f $ret, $vol)
    $repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
    $py = @"
import sys; sys.path.insert(0, r'$repo')
from app.engines import frontier as fr
mu = fr.arithmetic_drift($ret, $vol); v = ($vol / 100.0) ** 2
p = fr.kelly_position([1.0], [mu], [[v]], 0.04)
f = p['fraction_of_kelly']
print('none' if f is None else round(f, 3), p['over_kelly'], round(p['growth_now']['cagr'] * 100, 2), round(p['growth_at_kelly']['cagr'] * 100, 2))
"@
    $res = ($py | python - 2>&1)
    if ($LASTEXITCODE -ne 0) { Bad "engine failed on live numbers: $res" }
    else {
        $parts = "$res".Trim().Split(' ')
        Ok ("engine ran: book is at {0}x full Kelly; growth now {2}%/yr vs {3}%/yr at Kelly" -f $parts)
        if ($parts[1] -eq 'True') { Note "OVER-KELLY: holding less of the same mix would grow faster. This is the PR 90 card's first reading - a fact, not a trade." }
        Note "Risk-free 4% assumed here; PR 89 reads the real ILS rate."
    }
}

Stop-Smoke
