# SMOKE - PR 155: the cash card does not fill an unfunded sleeve's slot with something else (D139).
#
#   pwsh ./scripts/smoke/smoke-pr155-an-empty-sleeve-slot-is-the-sleeves.ps1 -Sha <short sha of the merge>
#
#   1  Today's cash card buys no new name while a sleeve you hold none of is short (2026-11-02: BND in BIL's place)
#   2  the read-only preview with more cash (?simulate_cash=) keeps the slot's cash back and says so
#   3  a Today card scoped to the core quotes the core the cards manage to (/plan/sleeves core_target_pct)
#   4  NOT CHANGED: a stop-held sleeve keeps nothing back, and the "fund this sleeve" heads-up is still there
#
# Before this PR, 1 and 2 FAIL on the live book of 2026-11-02 (2x Gold Trend unfunded, leg BIL).
# READ-ONLY: GETs only. ?simulate_cash is a preview that writes nothing.
# ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot/_smoke-common.ps1"
Start-Smoke "PR 155"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$sl = Api GET "/api/v1/plan/sleeves"
if (-not $sl) { Bad "the sleeves view did not answer"; Stop-Smoke }
$empty = @($sl.sleeves | Where-Object { -not $_.is_core -and [double]$_.held_pct -eq 0 -and @($_.on_stop_hold).Count -eq 0 })
$emptyTk = @($empty | ForEach-Object { @($_.tickers) } | ForEach-Object { "$_".ToUpper() })
$stopTk = @($sl.sleeves | ForEach-Object { @($_.on_stop_hold) } | ForEach-Object { "$_".ToUpper() })
Note ("unfunded sleeves: {0}; stop-held: {1}" -f (($emptyTk -join ", ") -replace "^$", "none"),
      (($stopTk -join ", ") -replace "^$", "none"))

function Get-CashCards($t) {
    @(@($t.recommendations) + @($t.more_recommendations) |
      Where-Object { $_ -and "$($_.apply.kind)" -eq "redeploy_cash" -or "$($_.title)" -like "Put *of cash back to work" })
}

Sec "1  Today's cash card buys no new name in an unfunded sleeve's place"
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer" }
elseif ($emptyTk.Count -eq 0) { Skip "every sleeve holds something (or is stop-held) today" }
else {
    $cc = Get-CashCards $t
    if ($cc.Count -eq 0) { Ok "no cash card: the slot's cash is left to Fund this sleeve" }
    foreach ($c in $cc) {
        $new = @($c.apply.legs | Where-Object { $_.new_position })
        if ($new.Count) { Bad ("'{0}' buys new name(s) {1}" -f $c.title, (@($new | ForEach-Object { $_.ticker }) -join ", ")) }
        else { Ok ("'{0}': no new name" -f $c.title) }
    }
}

Sec "2  the preview with more cash keeps the slot's cash back"
if ($emptyTk.Count -eq 0) { Skip "no unfunded sleeve today" }
else {
    $nav = [double]$sl.nav_ils
    $sim = [math]::Round($nav * 0.6, 0)
    $p = Api GET ("/api/v1/recommendations?simulate_cash={0}" -f $sim)
    if (-not $p) { Bad "the preview did not answer" }
    elseif (@($p.recommendations).Count -eq 0) { Skip ("no cash card at a simulated {0:N0}" -f $sim) }
    else {
        $c = @($p.recommendations)[0]
        $legs = @($c.apply.legs | ForEach-Object { "$($_.ticker)".ToUpper() })
        $kept = [double]$c.meta.sleeve_slot_kept_ils
        $keptTk = @($c.meta.sleeve_slot_tickers | ForEach-Object { "$_".ToUpper() })
        Note ("simulated cash {0:N0}: legs {1}; kept {2:N0} for {3}" -f $sim, ($legs -join ", "), $kept, ($keptTk -join ", "))
        if ($kept -ge 1 -and @($emptyTk | Where-Object { $keptTk -contains $_ }).Count) { Ok "the slot's cash is kept back" }
        else { Bad "no cash kept back for the unfunded sleeve" }
        if (@($c.apply.legs | Where-Object { $_.new_position }).Count) { Bad "the preview buys a new name" }
        else { Ok "no new name bought" }
        if (@($c.how | Where-Object { "$_" -like "*Fund this sleeve*" }).Count) { Ok "the card says where that cash goes" }
        else { Bad "the card does not say why cash is kept" }
    }
}

Sec "3  a core card quotes the core the cards manage to"
$want = [math]::Round(100.0 * [double]$sl.core_target_pct, 1)
$core = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | ForEach-Object { $_.card }) |
          Where-Object { $_ -and $_.scope -and "$($_.scope.kind)" -eq "core" -and "$($_.scope.label)" -like "*% of book" })
if ($core.Count -eq 0) { Skip "no core-scoped card on Today" }
foreach ($c in $core) {
    if ("$($c.scope.label)" -like ("*{0}% of book" -f $want.ToString([Globalization.CultureInfo]::InvariantCulture))) { Ok ("'{0}': {1}" -f $c.title, $c.scope.label) }
    else { Bad ("'{0}': '{1}', the core is managed to {2}%" -f $c.title, $c.scope.label, $want) }
}

Sec "4  NOT CHANGED: stop-held keeps nothing back; the fund-this-sleeve heads-up stays"
if ($p -and @($p.recommendations).Count) {
    $keptTk = @(@($p.recommendations)[0].meta.sleeve_slot_tickers | ForEach-Object { "$_".ToUpper() })
    $bad = @($keptTk | Where-Object { $stopTk -contains $_ })
    if ($bad.Count) { Bad ("cash kept back for stop-held {0}" -f ($bad -join ", ")) } else { Ok "nothing kept back for a stop-held leg" }
} else { Skip "no preview card to read" }
if ($emptyTk.Count -eq 0) { Skip "no unfunded sleeve" }
else {
    $hu = @(@($t.headsups) | Where-Object { "$($_.title)" -like "*sleeve and hold none of it*" })
    if ($hu.Count) { Ok ("heads-up present: {0}" -f $hu[0].title) } else { Bad "the fund-this-sleeve heads-up is gone" }
}

Stop-Smoke
