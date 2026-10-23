# SMOKE - PR 148 (D125): no app buy takes the 2x/3x funds past leveraged_ceiling_pct (44%).
#
#   .\scripts\smoke\smoke-pr148-leveraged-ceiling.ps1 -Sha <short sha of the merge>
#
#   1  the setting is on the plan (Card-thresholds sheet): leveraged_ceiling_pct, default 44; the funds it counts
#   2  the book: the 2x/3x share, and an over-ceiling trim card on Today when (and only when) it is over
#   3  Fund my sleeves (dry run): its 2x/3x buys fit the headroom
#   4  Today: no buy card of a 2x/3x fund larger than the headroom (your own rule's card excepted, and it says so)
#   5  NOT CHANGED: PR 141 -- a stop hold on a 2x/3x fund still holds every 2x/3x fund
#
# READ-ONLY (the sleeve funding call is a dry run). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 148"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$plan = Api GET "/api/v1/plan"
$ceil = $plan.thresholds.leveraged_ceiling_pct
$funds = @($plan.leveraged_funds)

Sec "1  the setting"
if ($null -eq $ceil) { Bad "no leveraged_ceiling_pct on the plan (a build before PR 148)"; Stop-Smoke }
Ok ("leveraged_ceiling_pct = {0} (default {1})" -f $ceil, $plan.thresholds_defaults.leveraged_ceiling_pct)
if ($funds.Count -gt 0 -and ($funds -contains "TQQQ") -and -not ($funds -contains "PSQ")) { Ok ("counts {0} funds: {1}" -f $funds.Count, ($funds -join ", ")) }
else { Bad ("leveraged_funds wrong: {0}" -f ($funds -join ", ")) }
if ([double]$ceil -le 0) { Skip "ceiling set to 0 (none): nothing else to check"; Stop-Smoke }

Sec "2  the book"
$pos = @((Api GET "/api/v1/portfolio").positions)
$nav = ($pos | Measure-Object -Property value_ils -Sum).Sum
$g = ($pos | Where-Object { $funds -contains $_.ticker } | Measure-Object -Property value_ils -Sum).Sum
if (-not $g) { $g = 0 }
$share = if ($nav) { 100.0 * $g / $nav } else { 0 }
$room = [math]::Max(0, [double]$ceil / 100.0 * $nav - $g)
$tol = 0.005 * $nav
Note ("2x/3x {0:N0} of {1:N0} ILS = {2:N1}% (ceiling {3}%); headroom {4:N0} ILS" -f $g, $nav, $share, $ceil, $room)
$t = Api GET "/api/v1/recommendations"
if (@($t.degraded) -contains "leveraged_ceiling") { Bad "Today reports the ceiling pass degraded" }
$all = @(@($t.recommendations) + @($t.more_recommendations))
$trim = @($all | Where-Object { $_.meta.source -eq "leveraged_ceiling" })
if ($share -gt [double]$ceil + 0.5) {
    if ($trim.Count) { Ok ("over the ceiling and Today offers: {0}" -f $trim[0].title) } else { Bad "over the ceiling and no trim card" }
} else {
    if ($trim.Count) { Bad "a ceiling trim card while under the ceiling" } else { Ok "under the ceiling; no trim card" }
}

Sec "3  Fund my sleeves (dry run)"
$f = Api POST "/api/v1/plan/sleeves/fund?dry_run=true"
$gb = 0.0
foreach ($s in @($f.sleeves)) { foreach ($b in @($s.buys)) { if ($funds -contains $b.ticker) { $gb += [double]$b.buy_ils } } }
if ($gb -le $room + $tol) { Ok ("2x/3x buys {0:N0} ILS fit the headroom {1:N0}" -f $gb, $room) }
else { Bad ("2x/3x buys {0:N0} ILS over the headroom {1:N0}" -f $gb, $room) }
$cl = @($f.sleeves | Where-Object { $_.clipped_by_ceiling_ils -or $_.refused -eq "LEVERAGED_CEILING" })
if ($cl.Count) { Note ("cut by the ceiling: {0}" -f (($cl | ForEach-Object { $_.name }) -join ", ")) }

Sec "4  Today's buys"
$over = @()
foreach ($c in $all) {
    $k = $c.apply.kind
    if (($k -eq "buy_funded" -or $k -eq "buy") -and ($funds -contains $c.apply.ticker) -and [double]$c.est_amount -gt $room + $tol) {
        if ("$($c.id)".StartsWith("rule_") -and "$($c.why)" -match "ceiling") { Note ("your rule's card, over the ceiling and says so: {0}" -f $c.title) }
        else { $over += $c.title }
    }
    if ($k -eq "redeploy_cash") {
        $gl = (@($c.apply.legs) | Where-Object { $funds -contains $_.ticker } | Measure-Object -Property amount_ils -Sum).Sum
        if ($gl -and $gl -gt $room + $tol) { $over += $c.title }
    }
}
if ($over.Count) { foreach ($x in $over) { Bad ("buys 2x/3x past the headroom: {0}" -f $x) } } else { Ok "no Today buy of a 2x/3x fund past the headroom" }
$blk = @($t.hidden_cards | Where-Object { $_.how -eq "blocked" -and "$($_.reason)" -match "ceiling" })
if ($blk.Count) { Note ("{0} card(s) blocked by the ceiling, listed with the reason" -f $blk.Count) }

Sec "5  not changed: PR 141"
$rules = @((Api GET "/api/v1/rules").rules)
$holds = @($rules | Where-Object { $_.rule_type -eq "buy_back" -and $_.active -and $_.params.kind -eq "stop" -and ($funds -contains $_.ticker) })
if ($holds.Count -eq 0) { Skip "no stop hold on a 2x/3x fund right now" }
else {
    $leak = @($all | Where-Object { ($_.apply.kind -eq "buy_funded" -or $_.apply.kind -eq "buy") -and ($funds -contains $_.apply.ticker) })
    if ($leak.Count) { Bad ("a 2x/3x buy while {0} is on hold: {1}" -f $holds[0].ticker, $leak[0].title) } else { Ok ("{0} on hold; no 2x/3x buy on Today" -f $holds[0].ticker) }
}
Stop-Smoke
