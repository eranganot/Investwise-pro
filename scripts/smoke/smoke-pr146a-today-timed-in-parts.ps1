# SMOKE - PR 146a: Today's first stage is timed in four parts (measure before changing anything).
#
#   .\scripts\smoke\smoke-pr146a-today-timed-in-parts.ps1 -Sha <short sha of the merge>
#
#   1  timings_ms carries load, book_target, allocation_cards, goal_projection, holding_verdicts
#   2  five warm reads: the median of each stage, and the slowest (recorded in STATUS, not judged)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 146a"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }
$want = @("load", "book_target", "allocation_cards", "goal_projection", "holding_verdicts")

Sec "1  the split stages"
$t = (Api GET "/api/v1/recommendations").timings_ms
$miss = @($want | Where-Object { $null -eq $t.$_ })
if ($miss.Count) { Bad ("missing: {0}" -f ($miss -join ", ")) } else { Ok "all five stages reported" }

Sec "2  five warm reads"
$runs = @()
for ($i = 0; $i -lt 5; $i++) { $runs += ,((Api GET "/api/v1/recommendations").timings_ms) }
$names = @($runs[0].PSObject.Properties | Where-Object { $_.Value -is [int] -or $_.Value -is [long] -or $_.Value -is [double] } | ForEach-Object { $_.Name })
$med = foreach ($n in $names) { $v = @($runs | ForEach-Object { [double]$_.$n } | Sort-Object); [pscustomobject]@{ stage = $n; ms = $v[2] } }
$top = @($med | Sort-Object ms -Descending | Select-Object -First 6)
Ok ("median ms: {0}" -f (($top | ForEach-Object { "{0} {1}" -f $_.stage, $_.ms }) -join "; "))
Stop-Smoke
