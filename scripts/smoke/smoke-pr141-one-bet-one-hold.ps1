# SMOKE - PR 141: a stop on one 2x/3x fund holds every 2x/3x fund (D116).
#
#   .\scripts\smoke\smoke-pr141-one-bet-one-hold.ps1 -Sha <short sha of the merge>
#
#   1  Fund my sleeves (DRY RUN) buys no 2x/3x fund while a stop holds one (10-23: SOXL held, TQQQ funded)
#   2  no card on Today buys a 2x/3x fund, or asks to top one up, while a stop holds one
#   3  the held-cash card offers no 2x/3x fund as a place for the money, and still names only the real hold
#   4  NOT CHANGED: a stop on a plain fund blocks only that fund; the PR 139 stop hold itself is untouched
#
# READ-ONLY: the only POST is the Fund-my-sleeves preview with dry_run=true, which writes nothing.
# Never accepts, never ignores, never ends a hold. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 141"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

# strategy_profile.STOP_GROUP (PR 154: the geared EQUITY funds; not UGL, TMF, UBT)
$geared = @("TQQQ", "SQQQ", "UPRO", "SPXL", "SOXL", "TECL", "UDOW", "TNA", "QLD", "FAS",
            "CURE", "ERX", "LABU", "DRN", "SSO")
$rules = @((Api GET "/api/v1/rules").rules)
$stops = @($rules | Where-Object { $_.rule_type -eq "buy_back" -and $_.active -and $_.params.kind -eq "stop" })
$stopTk = @($stops | ForEach-Object { "$($_.ticker)".ToUpper() })
$src = @($stopTk | Where-Object { $geared -contains $_ })
$blocked = @($stopTk)
if ($src.Count -gt 0) { $blocked = @($stopTk + $geared | Select-Object -Unique) }
Note ("stop holds: {0}; blocked: {1}" -f (($stopTk -join ", ") -replace "^$", "none"),
      $(if ($src.Count) { "every 2x/3x fund (via " + ($src -join ", ") + ")" } elseif ($stopTk.Count) { $stopTk -join ", " } else { "none" }))

Sec "1  Fund my sleeves (dry run) buys nothing blocked"
if ($blocked.Count -eq 0) { Skip "no stop hold on the book" }
else {
    $f = Api POST "/api/v1/plan/sleeves/fund?dry_run=true"
    if (-not $f) { Bad "the preview did not answer" }
    else {
        $hit = 0
        foreach ($s in @($f.sleeves)) {
            foreach ($b in @($s.buys)) {
                if ($b -and $blocked -contains "$($b.ticker)".ToUpper()) { Bad ("{0} would buy {1}" -f $s.strategy_id, $b.ticker); $hit++ }
            }
            if ($s.status -eq "held_back") { Ok ("{0}: held back ({1})" -f $s.strategy_id, (@($s.held_back) -join ", ")) }
        }
        if ($hit -eq 0) { Ok ("{0} sleeve(s) previewed, none buys a blocked fund" -f @($f.sleeves).Count) }
    }
}

Sec "2  Today buys and tops up nothing blocked"
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$cards = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | ForEach-Object { $_.card } | Where-Object { $_ }))
if ($blocked.Count -eq 0) { Skip "no stop hold on the book" }
else {
    $hits = 0
    foreach ($c in $cards) {
        $a = $c.apply; if (-not $a) { continue }
        $buys = @()
        if ($a.kind -eq "buy_funded" -and $a.ticker) { $buys += "$($a.ticker)".ToUpper() }
        if ($a.kind -eq "redeploy_cash") { $buys += @($a.legs | ForEach-Object { "$($_.ticker)".ToUpper() }) }
        if ($a.kind -eq "fee_swap" -and $a.buy) { $buys += "$($a.buy)".ToUpper() }
        foreach ($b in $buys) { if ($blocked -contains $b) { Bad ("card {0} buys {1}" -f $c.id, $b); $hits++ } }
        $mt = "$($c.meta.ticker)".ToUpper()
        if ($blocked -contains $mt -and ("$($c.action)" -match "^Add about" -or $c.meta.cold_start)) {
            Bad ("card {0} asks to top up {1}: {2}" -f $c.id, $mt, $c.title); $hits++
        }
    }
    if ($hits -eq 0) { Ok ("{0} card(s) checked, none buys or tops up a blocked fund" -f $cards.Count) }
}

Sec "3  the held-cash card offers only places that are not blocked"
$hc = @($cards | Where-Object { $_.apply.kind -eq "held_cash" })
if ($hc.Count -eq 0) { Skip "no held-cash card on Today right now" }
foreach ($c in $hc) {
    $dests = @($c.apply.options.redeploy.destinations | ForEach-Object { "$($_.ticker)".ToUpper() })
    $badD = @($dests | Where-Object { $blocked -contains $_ })
    if ($badD.Count) { Bad ("offers {0}" -f ($badD -join ", ")) } else { Ok ("places offered: {0}" -f (($dests -join ", ") -replace "^$", "none")) }
    $names = @($c.apply.tickers | ForEach-Object { "$_".ToUpper() })
    $extra = @($names | Where-Object { $stopTk -notcontains $_ -and $src.Count -gt 0 -and $geared -contains $_ })
    if ($extra.Count) { Bad ("names a blocked fund as a hold of its own: {0}" -f ($extra -join ", ")) } else { Ok ("waits for {0}" -f ($names -join ", ")) }
    if ($src.Count -gt 0) {
        if ("$($c.why)" -match "any other 2x/3x fund") { Ok "says the other 2x/3x funds wait too" } else { Bad ("does not say the 2x/3x funds wait: {0}" -f $c.why) }
    }
}

Sec "4  not changed"
foreach ($h in $stops) {
    if ($h.note -match "no re-buy until" -and -not $h.triggered) { Ok ("{0} stop hold intact: {1}" -f $h.ticker, $h.note) }
    else { Bad ("{0} stop hold changed: {1}" -f $h.ticker, $h.note) }
}
if ($stops.Count -eq 0) { Skip "no stop hold on the book" }
if ($src.Count -eq 0 -and $stopTk.Count -gt 0) { Ok "no stop on a 2x/3x fund, so only the stopped tickers are blocked" }
Stop-Smoke
