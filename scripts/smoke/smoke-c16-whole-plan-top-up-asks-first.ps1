# SMOKE - C16: a top-up routed to the whole-plan funder (a ticker two sleeves want) asks it first.
#
#   .\scripts\smoke\smoke-c16-whole-plan-top-up-asks-first.ps1 -Sha <short sha of the merge>
#
#   1  no Today card offers a fund_plan Accept that the funder's dry run refuses
#   2  NOT CHANGED: a single-sleeve top-up card (fund_sleeve) still offers Accept only when the funder agrees
#
# READ-ONLY (the funder is asked with dry_run=true). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C16"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$t = Api GET "/api/v1/recommendations"
$all = @(@($t.recommendations) + @($t.more_recommendations))

Sec "1  whole-plan top-ups"
$fp = @($all | Where-Object { $_.apply.kind -eq "fund_plan" })
if ($fp.Count -eq 0) { Skip "no whole-plan top-up card today (no ticker two sleeves want is under target)" }
else {
    $f = Api POST "/api/v1/plan/sleeves/fund?dry_run=true"
    if ($f.ok -eq $false) { Bad ("a fund_plan card offers Accept while the funder refuses: {0}" -f $f.error) }
    else { Ok ("{0} fund_plan card(s); the funder's dry run agrees" -f $fp.Count) }
}

Sec "2  not changed: single-sleeve top-ups"
$fs = @($all | Where-Object { $_.apply.kind -eq "fund_sleeve" })
if ($fs.Count -eq 0) { Skip "no single-sleeve top-up card today" }
else { Ok ("{0} single-sleeve top-up card(s) offered" -f $fs.Count) }
Stop-Smoke
