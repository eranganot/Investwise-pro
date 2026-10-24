# SMOKE - A2r (D129): the protection retune tries wider stops (x1.5, x2, x3, never off) and ranks by the amount at the bar.
#
#   .\scripts\smoke\smoke-a2r-retune-tries-stop-widths.ps1 -Sha <short sha of the merge>
#
#   1  the stored solve was made on this build (widths_measured) -- else SKIP (nightly, or Plan > Goal > Re-solve now)
#   2  the candidates include widened stops at 1.5, 2 and 3; none retires an exit (D115)
#   3  the best is the candidate with the most at the bar inside the halving limit
#   4  the card, when there is one, lists each stop it widens and pauses no stop
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "A2r"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$f = Api GET "/api/v1/plan/frontier"
$tu = $f.rules.tuning

Sec "1  a solve on this build"
if (-not $tu -or $null -eq $tu.widths_measured) { Skip ("the stored solve ({0}) predates this build" -f $f.computed_at); Stop-Smoke }
Ok ("solved {0}; {1} setups tried" -f $f.computed_at, $tu.tried)

Sec "2  wider stops, never off"
$c = @($tu.candidates)
$fs = @($c | Where-Object { $_.widen } | ForEach-Object { $_.widen } | ForEach-Object { [double]$_[1] } | Sort-Object -Unique)
if (($fs -contains 1.5) -and ($fs -contains 2) -and ($fs -contains 3)) { Ok ("widths tried: {0}" -f ($fs -join ", ")) } else { Bad ("widths tried: {0}" -f ($fs -join ", ")) }
if (@($c | Where-Object { $_.retire_sleeve_exits }).Count) { Bad "a candidate retires the exits (D115)" } else { Ok "no candidate removes a stop" }

Sec "3  ranked by the amount at the bar"
$full = @($c | Where-Object { $null -eq $_.paths -and [double]$_.stats.p_halve_pct -le [double]$tu.halve_floor_pct })
$top = ($full | Measure-Object -Property { [double]$_.stats.amount_at_confidence } -Maximum).Maximum
if ($tu.best -and [double]$tu.best.stats.amount_at_confidence -ge $top - 0.5) { Ok ("best reaches {0:N0} at the bar (today {1:N0})" -f [double]$tu.best.stats.amount_at_confidence, [double]$tu.current.stats.amount_at_confidence) }
elseif (-not $tu.best) { Skip "no setup inside the halving limit" }
else { Bad "the best is not the one with the most at the bar" }

Sec "4  the card"
$t = Api GET "/api/v1/recommendations"
$card = @(@($t.recommendations) + @($t.more_recommendations) | Where-Object { $_.id -eq "rec_goal_protect_book" })
if (-not $card.Count) { Skip ("no retune card (material: {0})" -f $tu.material) }
else {
    $how = @($card[0].how)
    $nw = @($how | Where-Object { $_ -match "^Widen: " }).Count
    $ps = @($how | Where-Object { $_ -match "^Pause: " -and $_ -notmatch "take_profit" }).Count
    if ($ps) { Bad "the card pauses a stop" } else { Ok ("the card widens {0} stop(s) and pauses none" -f $nw) }
}
Stop-Smoke
