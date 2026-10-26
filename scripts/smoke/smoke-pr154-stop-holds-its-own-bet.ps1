# SMOKE - PR 154: a stop on a geared equity fund does not hold 2x gold or the Treasury funds (D116 amended, D138).
#
#   .\scripts\smoke\smoke-pr154-stop-holds-its-own-bet.ps1 -Sha <short sha of the merge>
#
#   1  no sleeve that owns UGL / TMF / UBT shows it "on hold" because of an equity stop
#   2  Fund my sleeves (DRY RUN) holds back no gold or Treasury fund because of an equity stop
#   3  NOT CHANGED: an equity stop still holds the geared equity funds (PR 141): its own sleeve
#      is on hold, and no Today card buys a geared equity fund
#
# Before this PR, 1 and 2 can only FAIL on a day the 2x Gold Trend signal is in (UGL), so on a
# day gold's signal is out they SKIP; the unit tests carry the proof.
# READ-ONLY: the only POST is the Fund-my-sleeves preview with dry_run=true, which writes nothing.
# ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 154"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

# strategy_profile.STOP_GROUP (GEARED without 2x gold and the Treasury funds)
$group = @("TQQQ", "SQQQ", "UPRO", "SPXL", "SOXL", "TECL", "UDOW", "TNA", "QLD", "FAS",
           "CURE", "ERX", "LABU", "DRN", "SSO")
$free = @("UGL", "TMF", "UBT")

$rules = @((Api GET "/api/v1/rules").rules)
$stops = @($rules | Where-Object { $_.rule_type -eq "buy_back" -and $_.active -and $_.params.kind -eq "stop" })
$stopTk = @($stops | ForEach-Object { "$($_.ticker)".ToUpper() })
$eqSrc = @($stopTk | Where-Object { $group -contains $_ })
Note ("stop holds: {0}; equity stop: {1}" -f (($stopTk -join ", ") -replace "^$", "none"),
      (($eqSrc -join ", ") -replace "^$", "none"))

$sl = Api GET "/api/v1/plan/sleeves"
if (-not $sl) { Bad "the sleeves view did not answer"; Stop-Smoke }
$sleeves = @($sl.sleeves)

Sec "1  gold and Treasury funds are not 'on hold' because of an equity stop"
$owning = @($sleeves | Where-Object { @($_.tickers | Where-Object { $free -contains "$_".ToUpper() }).Count -gt 0 })
if ($eqSrc.Count -eq 0) { Skip "no equity stop on the book" }
elseif ($owning.Count -eq 0) { Skip "no sleeve owns UGL / TMF / UBT today (gold's signal is out)" }
else {
    foreach ($s in $owning) {
        $h = @($s.on_stop_hold | Where-Object { $free -contains "$_".ToUpper() -and $stopTk -notcontains "$_".ToUpper() })
        if ($h.Count) { Bad ("{0} shows {1} on hold" -f $s.strategy_id, ($h -join ", ")) }
        else { Ok ("{0} ({1}): not on hold" -f $s.strategy_id, (@($s.tickers) -join ", ")) }
    }
}

Sec "2  Fund my sleeves (dry run) holds back no gold or Treasury fund"
if ($eqSrc.Count -eq 0) { Skip "no equity stop on the book" }
else {
    $f = Api POST "/api/v1/plan/sleeves/fund?dry_run=true"
    if (-not $f) { Bad "the preview did not answer" }
    else {
        $hit = 0
        foreach ($s in @($f.sleeves)) {
            $hb = @($s.held_back | ForEach-Object { "$_".ToUpper() } | Where-Object { $free -contains $_ -and $stopTk -notcontains $_ })
            if ($hb.Count) { Bad ("{0} holds back {1}" -f $s.strategy_id, ($hb -join ", ")); $hit++ }
        }
        if ($hit -eq 0) { Ok ("{0} sleeve(s) previewed, no gold or Treasury fund held back" -f @($f.sleeves).Count) }
    }
}

Sec "3  NOT CHANGED: an equity stop still holds the geared equity funds"
if ($eqSrc.Count -eq 0) { Skip "no equity stop on the book" }
else {
    $own = @($sleeves | Where-Object { @($_.tickers | Where-Object { $eqSrc -contains "$_".ToUpper() }).Count -gt 0 })
    foreach ($s in $own) {
        $h = @($s.on_stop_hold | ForEach-Object { "$_".ToUpper() } | Where-Object { $eqSrc -contains $_ })
        if ($h.Count) { Ok ("{0}: {1} still on hold" -f $s.strategy_id, ($h -join ", ")) }
        else { Bad ("{0}: its stopped fund is no longer on hold" -f $s.strategy_id) }
    }
    $t = Api GET "/api/v1/recommendations"
    if (-not $t) { Bad "Today did not answer" }
    else {
        $cards = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | ForEach-Object { $_.card } | Where-Object { $_ }))
        $hits = 0
        foreach ($c in $cards) {
            $a = $c.apply; if (-not $a) { continue }
            $buys = @()
            if ($a.kind -eq "buy_funded" -and $a.ticker) { $buys += "$($a.ticker)".ToUpper() }
            if ($a.kind -eq "redeploy_cash") { $buys += @($a.legs | ForEach-Object { "$($_.ticker)".ToUpper() }) }
            if ($a.kind -eq "fee_swap" -and $a.buy) { $buys += "$($a.buy)".ToUpper() }
            foreach ($b in $buys) { if ($group -contains $b) { Bad ("card {0} buys {1}" -f $c.id, $b); $hits++ } }
        }
        if ($hits -eq 0) { Ok ("{0} card(s) checked, none buys a geared equity fund" -f $cards.Count) }
    }
}

Stop-Smoke
