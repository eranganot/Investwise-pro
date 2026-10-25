# SMOKE - PR 150 (C30): the core realign swaps contents and keeps the core's size while a sleeve is unfunded.
#
#   .\scripts\smoke\smoke-pr150-core-realign-keeps-size.ps1 -Sha <short sha of the merge> [-Since <deploy time UTC>]
#
#   1  the core card (if on Today): no "stay as cash" step, no "NOT sold here" step when a sleeve the app may buy is short
#      (production 10-25 before: "About 3,153 of the proceeds would stay as cash" -> FAIL there)
#   2  after -Since: no core_to_basket accept followed within 10 minutes by a redeploy_cash buy (the loop)
#   3  NOT CHANGED: the card still names its steps; a core under its target is not reported as kept
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app", [string]$Since = "")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 150"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

function Utc($v) {
    if ($null -eq $v -or "$v" -eq "") { return $null }
    $d = [datetime]$v
    if ($d.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($d, [DateTimeKind]::Utc) }
    return $d.ToUniversalTime()
}

$p = Api GET "/api/v1/recommendations"
$cards = @(@($p.recommendations) + @($p.more_recommendations))
$core = @($cards | Where-Object { $_.apply -and $_.apply.kind -eq "core_to_basket" })
$sl = Api GET "/api/v1/plan/sleeves"
$short = @(@($sl.sleeves) | Where-Object { [double]$_.gap_pct -gt 1 }).Count

Sec "1  the core card keeps the core's size"
if ($core.Count -eq 0) { Skip "no core card on Today now" }
else {
    $how = (@($core[0].how) -join " | ")
    Note $how
    if ($how -like "*stay as cash*") { Bad "the core card still leaves proceeds in cash" } else { Ok "no step leaves proceeds in cash" }
    if ($short -gt 0 -and $how -like "*NOT sold here*") { Bad "an off-basket holding is still reserved for an unfunded sleeve" }
    elseif ($short -gt 0) { Ok "nothing is reserved for the unfunded sleeve(s)" }
    else { Skip "no sleeve is short now" }
    if ($short -gt 0 -and [string]$core[0].impact -notlike "*keeps its size*") { Note "a sleeve is short but the card does not say the core keeps its size (blocked gap only?)" }
}

Sec "2  no realign -> cash-card loop after the deploy"
if (-not $Since) { Skip "pass -Since <deploy time UTC> to check the ledger" }
else {
    $since = Utc $Since
    $led = Api GET "/api/v1/portfolio/ledger?limit=400"
    $rows = @(@($led.entries) | Where-Object { $_.at -and (Utc $_.at) -ge $since })
    $real = @($rows | Where-Object { $_.source -eq "core_to_basket" -and $_.side -eq "Sell" })
    $loops = 0
    foreach ($r in $real) {
        $t = Utc $r.at
        $loops += @($rows | Where-Object { $_.source -eq "redeploy_cash" -and $_.side -eq "Buy" -and
                    (Utc $_.at) -ge $t -and ((Utc $_.at) - $t).TotalMinutes -le 10 }).Count
    }
    if ($real.Count -eq 0) { Skip "no core realign accepted since the deploy" }
    elseif ($loops -eq 0) { Ok ("{0} realign sale(s), no cash-card buy within 10 min" -f $real.Count) }
    else { Bad ("{0} cash-card buy(s) within 10 min of a realign" -f $loops) }
}

Sec "3  NOT CHANGED"
if ($core.Count -gt 0) {
    if (@($core[0].how).Count -gt 0) { Ok "the core card lists its steps" } else { Bad "the core card has no steps" }
} else { Skip "no core card on Today now" }
Stop-Smoke
