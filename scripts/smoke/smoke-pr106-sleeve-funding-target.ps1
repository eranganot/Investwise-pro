# SMOKE - PR 106: sleeve funding sells only what the plan says is overweight.
#
#   .\scripts\smoke\smoke-pr106-sleeve-funding-target.ps1 -Sha <short sha of the merge>
#
#   1  "Fund sleeves" dry run: no sell leg from a class whose /mix held weight is
#      at or under its book target while any class is over (before PR 106 the
#      live book sold BND, Fixed Income 12.11% held vs 12.14% target)
#   2  a refusal, if any, names the class it protects
#   3  non-change: Today builds with nothing degraded; NAV = sum of rows
#
# 106.1: a refused plan counts as selling nothing (its `funding` is what it
# rejected); the refusal must name the class it protects.
#
# READ-ONLY: the only POST is ?dry_run=true. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 106"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 106 has no migration)"

$mix = Api GET "/api/v1/mix"
$held = $mix.current_allocation
$tgt = $mix.target_allocation
$over = @($held.PSObject.Properties | Where-Object { [double]$_.Value -gt [double]($tgt.($_.Name)) + 1e-9 } | ForEach-Object { $_.Name })
Note ("over target: {0}" -f ($over -join ', '))

Sec "1  Fund sleeves (dry run) sells only overweight classes"
$f = $null
try { $f = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/plan/sleeves/fund?dry_run=true" -Headers $script:ApiHeaders -TimeoutSec 300 } catch {}
if ($null -eq $f) { Bad "/plan/sleeves/fund?dry_run=true failed" }
elseif ($f.ok -eq $false) {
    # 106.1: a refused plan sells nothing; its `funding` is only what it rejected.
    Ok ("refused, nothing would be sold: {0}" -f $f.error)
}
else {
    $sells = @($f.funding.sells)
    if ($sells.Count -eq 0) { Ok ("no sell legs (ok={0}; {1})" -f $f.ok, $(if ($f.reason) { $f.reason } else { $f.message })) }
    foreach ($s in $sells) {
        $c = $s.asset_class
        if ($over.Count -eq 0 -or $over -contains $c) { Ok ("sells {0} ({1}): over target" -f $s.ticker, $c) }
        else { Bad ("sells {0} ({1}): {2:P2} held vs {3:P2} target, while {4} is over" -f $s.ticker, $c, [double]$held.$c, [double]$tgt.$c, ($over -join ', ')) }
    }
}

Sec "2  a refusal names the class"
if ($f -and $f.ok -eq $false -and $f.drift) {
    if ("$($f.reason)" -match 'target\)') { Ok "refused: $($f.reason)" } else { Bad "refusal names no class: $($f.reason)" }
} else { Skip "no drift refusal on this book" }

Sec "3  non-change"
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
$pf = Api GET "/api/v1/portfolio"
$sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("nav_ils {0:N2} = sum of rows" -f [double]$pf.nav_ils) } else { Bad "nav_ils != sum of rows" }
Stop-Smoke
