# SMOKE - PR 89: the frontier's inputs (ILS returns, shrunk covariance,
# Black-Litterman). One new read-only endpoint; nothing reads it yet.
#
#   .\scripts\smoke\smoke-pr89-frontier-inputs.ps1 -Sha abc1234
#
#   1  GET /plan/frontier/inputs answers on the real book, in SHEKELS
#   2  every sleeve the plan runs is an asset or excluded BY NAME; every view
#      is used or carries its reason
#   3  cash is riskless at the table's rate; the covariance is sane
#   4  THE PRIOR ADDS NO TILT: optimising the prior at its own risk aversion,
#      on production's real covariance, hands back the plan's fixed target
#   5  the book did not move
#
# READ-ONLY. Accepts nothing. The endpoint fetches ~10y of closes (slow).
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 89"
Assert-Build -Sha $Sha -Schema '0025_rule_params' -Why "(unchanged - PR 89 has no migration)"

$mixBefore = Api GET "/api/v1/mix"

Sec "1  the inputs, on the real book"
$t0 = Get-Date
$fi = Api GET "/api/v1/plan/frontier/inputs" 600
$secs = [int]((Get-Date) - $t0).TotalSeconds
if ($null -eq $fi) { Bad "/plan/frontier/inputs did not answer"; Stop-Smoke }
if (-not $fi.ok) { Bad ("not ok: {0} {1}" -f $fi.reason, $fi.detail); Stop-Smoke }
Ok ("answered in {0}s: {1} assets over {2} sessions ({3} .. {4}), objective {5}" -f $secs, @($fi.assets).Count, $fi.window.sessions, $fi.window.start, $fi.window.end, $fi.objective)
if ($fi.fx_basis -eq 'historical') { Ok "returns are in ILS (USD/ILS history compounded in)" }
else { Bad "fx_basis '$($fi.fx_basis)' - the currency risk was NOT measured; degraded: $(@($fi.degraded) -join ',')" }
foreach ($a in @($fi.assets)) {
    Note ("{0,-16} {1,-6} prior {2,7:N2}%  post {3,7:N2}%  cagr {4,7:N2}%  vol {5,6:N2}%" -f $a.id, $a.kind, ([double]$a.prior_drift * 100), ([double]$a.drift * 100), [double]$a.cagr_pct, ([double]$a.volatility * 100))
}

Sec "2  sleeves and views, each accounted for"
$sl = Api GET "/api/v1/plan/sleeves"
$planned = @(@($sl.sleeves) | ForEach-Object { $_.strategy_id } | Where-Object { $_ })
$assetIds = @(@($fi.assets) | ForEach-Object { $_.id })
$excludedIds = @(@($fi.excluded) | ForEach-Object { $_.id })
foreach ($sid in $planned) {
    if ($assetIds -contains $sid) { Ok "$sid is in the covariance" }
    elseif ($excludedIds -contains $sid) { $x = @($fi.excluded) | Where-Object { $_.id -eq $sid } | Select-Object -First 1; Skip "$sid excluded: $($x.reason) $(@($x.tickers) -join ',')" }
    else { Bad "$sid is neither an asset nor excluded - dropped silently" }
}
foreach ($v in @($fi.views)) {
    if ($v.used) { Ok ("view on {0}: OOS CAGR {1}% over {2}y" -f $v.asset, $v.cagr_pct, $v.years) }
    elseif ($v.reason) { Skip ("no view on {0}: {1}" -f $v.asset, $v.reason) }
    else { Bad "view on $($v.asset) unused with no reason" }
}

Sec "3  cash and the covariance"
$cash = @($fi.assets)[-1]
if ($cash.id -eq 'cash' -and [double]$cash.volatility -eq 0 -and [math]::Abs([double]$cash.cagr_pct - [double]$fi.risk_free.cagr_pct) -lt 0.01) { Ok ("cash last, riskless, {0}%/yr ({1})" -f $fi.risk_free.cagr_pct, $fi.risk_free.source) }
else { Bad "cash row wrong: $($cash | ConvertTo-Json -Compress)" }
$sh = [double]$fi.shrinkage
if ($sh -ge 0 -and $sh -le 1) { Ok "shrinkage $sh" } else { Bad "shrinkage $sh outside [0,1]" }

Sec "4  the prior adds no tilt (engine on production's real covariance)"
$tmp = Join-Path $env:TEMP "iw-pr89-inputs.json"
$fi | ConvertTo-Json -Depth 12 | Set-Content -Path $tmp -Encoding ASCII
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = @"
import json, sys; sys.path.insert(0, r'$repo')
import numpy as np
from app.engines import frontier as fr
d = json.load(open(r'$tmp'))
ids = [a['id'] for a in d['assets']]
cov = np.array(d['cov']); prior = np.array([a['prior_drift'] for a in d['assets']])
assert np.allclose(cov, cov.T, atol=1e-9), 'cov not symmetric'
assert np.linalg.eigvalsh(cov[:-1, :-1]).min() > 0, 'risky block not positive definite'
ref = d['prior']['reference']; lam = d['prior']['risk_aversion']
w = fr.optimize(prior, cov, risk_aversion=lam, free_total=1.0)
gap = max(abs(w[i] - ref.get(a, 0.0)) for i, a in enumerate(ids))
print(round(gap, 5), ' '.join(f'{a}={w[i]:.3f}' for i, a in enumerate(ids)))
"@
$res = ($py | python - 2>&1)
if ($LASTEXITCODE -ne 0) { Bad "engine check failed: $res" }
else {
    $gap = [double]("$res".Trim().Split(' ')[0])
    Note "$res"
    if ($gap -lt 0.002) { Ok ("prior optimises back to the plan's target (max gap {0})" -f $gap) }
    else { Bad ("prior tilts the book by up to {0} - the reverse optimisation is wrong" -f $gap) }
}
Remove-Item $tmp -ErrorAction SilentlyContinue

Sec "5  the book did not move"
$mixAfter = Api GET "/api/v1/mix"
$a = ($mixBefore.target_allocation | ConvertTo-Json -Compress); $b = ($mixAfter.target_allocation | ConvertTo-Json -Compress)
if ($a -eq $b -and $mixAfter.target_source -eq $mixBefore.target_source) { Ok "target unchanged ($($mixAfter.target_source)): $b" }
else { Bad "target moved: $a -> $b" }

Stop-Smoke
