# SMOKE - C15: the core realign trims only sleeves that are over their size, and never a basket ticker (D119).
#
#   .\scripts\smoke\smoke-c15-core-trims-only-what-is-over.ps1 -Sha <short sha of the merge> [-Since <ISO time of the deploy>]
#
#   1  the core card, if it trims sleeves, names where from -- and every ticker named belongs to a sleeve that is OVER
#   2  the ledger since -Since has no churn: no core_to_basket SELL of a ticker that "Fund my sleeves" bought back within 60 min
#   3  no core_to_basket accept sold and bought the same ticker in the same moment (10-20 03:59: QQQ sold 1,358, bought 2,311)
#   4  NOT CHANGED: a core card that trims nothing still offers its buys
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$Since = "", [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C15"
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
$occ = @((Api GET "/api/v1/plan/sleeves").sleeves)
$overTk = @()
foreach ($s in $occ) { if ([double]$s.gap_pct -lt 0) { $overTk += @($s.tickers | ForEach-Object { "$_".ToUpper() }) } }
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$core = @(@($t.recommendations) + @($t.more_recommendations) | Where-Object { $_.apply.kind -eq "core_to_basket" })

Sec "1  a sleeve trim comes only from sleeves that are over"
if ($core.Count -eq 0) { Skip "no core realign card on Today right now" }
foreach ($c in $core) {
    $p = $c.meta.plan
    if ([double]$p.sleeve_trim_ils -lt 1) { Skip ("{0} trims no sleeve (sleeves short or at size)" -f $c.id); continue }
    $from = @($p.sleeve_trim_from_ils.PSObject.Properties | ForEach-Object { $_.Name.ToUpper() })
    if ($from.Count -eq 0) { Bad ("{0} trims {1} from sleeves and does not say where from" -f $c.id, $p.sleeve_trim_ils); continue }
    $bad = @($from | Where-Object { $overTk -notcontains $_ })
    if ($bad.Count) { Bad ("{0} trims {1}, whose sleeve is not over" -f $c.id, ($bad -join ", ")) }
    else { Ok ("{0} trims {1} from {2}" -f $c.id, $p.sleeve_trim_ils, ($from -join ", ")) }
}

$led = @((Api GET "/api/v1/portfolio/ledger?limit=500").entries)
$sinceUtc = Utc $Since
if (-not $Since) { Note "no -Since: the whole visible ledger, so pre-fix churn (10-19 .. 10-21) shows as NOTE" }
$win = @($led | Where-Object { (-not $sinceUtc) -or ((Utc $_.at) -ge $sinceUtc) })

Sec "2  no core sell that sleeve funding bought straight back"
$coreSells = @($win | Where-Object { $_.source -eq "core_to_basket" -and $_.side -eq "Sell" -and [double]$_.value_ils -gt 0 })
$hits = 0
foreach ($s in $coreSells) {
    $at = Utc $s.at
    $back = @($led | Where-Object { $_.source -eq "sleeve_fund" -and $_.side -eq "Buy" -and $_.ticker -eq $s.ticker -and
        (Utc $_.at) -gt $at -and ((Utc $_.at) - $at).TotalMinutes -le 60 })
    if ($back.Count) {
        $msg = ("{0} sold {1} at {2}; sleeve funding bought it back at {3}" -f $s.source, $s.ticker, $s.at, $back[0].at)
        if ($Since) { Bad $msg } else { Note $msg }
        $hits++
    }
}
if ($coreSells.Count -eq 0) { Skip "no core realign sale in the window" }
elseif ($hits -eq 0) { Ok ("{0} core sale(s), none bought back by sleeve funding" -f $coreSells.Count) }

Sec "3  no core accept sold and bought the same ticker"
$groups = @($win | Where-Object { $_.source -eq "core_to_basket" } | Group-Object { "$($_.at)".Substring(0, 19) })
$same = 0
foreach ($g in $groups) {
    $s = @($g.Group | Where-Object { $_.side -eq "Sell" -and [double]$_.value_ils -gt 0 } | ForEach-Object { $_.ticker })
    $b = @($g.Group | Where-Object { $_.side -eq "Buy" } | ForEach-Object { $_.ticker })
    $both = @($s | Where-Object { $b -contains $_ } | Select-Object -Unique)
    if ($both.Count) {
        $msg = ("{0}: sold and bought {1}" -f $g.Name, ($both -join ", "))
        if ($Since) { Bad $msg } else { Note $msg }
        $same++
    }
}
if ($groups.Count -eq 0) { Skip "no core realign accept in the window" }
elseif ($same -eq 0) { Ok ("{0} core accept(s), none bought what it sold" -f $groups.Count) }

Sec "4  not changed: the core card still offers its buys"
if ($core.Count -eq 0) { Skip "no core realign card on Today right now" }
foreach ($c in $core) {
    if (@($c.how) -match "^Buy ") { Ok ("{0}: {1}" -f $c.id, (@($c.how) -match "^Buy ")[0]) } else { Note ("{0} offers no buy: {1}" -f $c.id, $c.action) }
}
Stop-Smoke
