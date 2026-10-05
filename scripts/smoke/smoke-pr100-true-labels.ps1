# SMOKE - PR 100: every number says what it measures (no card gets a new sentence).
#
#   .\scripts\smoke\smoke-pr100-true-labels.ps1 -Sha abc1234
#
#   1  no card carries the old generic Why / Impact boilerplate
#   2  a gain card under its plan target suggests no trim (one line: gain + what
#      covers it); over target it is "Trim <ticker> to target", sized on the excess
#   3  "marked done ... still N ILS to move" only on a card that asked for a trade
#   4  the benchmark chart's payload carries both returns (the screen shows
#      "You +x% . SPY +y%"); the account history carries recorded_since + gaps
#   UI copy (title, 2 decimals, Details folds) is checked by eye: see the end.
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 100"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
Note ("{0} cards" -f $all.Count)

Sec "1  no generic Why / Impact"
$generic = @('This helps your portfolio track your plan.', 'Moves your portfolio closer to your plan.',
             'Realigns your mix with your plan.', 'Moves you closer to reaching your goal on time.',
             'A risk metric is outside your comfort band.', 'Brings a risk measure back into your comfort band.')
$hits = @($all | Where-Object { $generic -contains "$($_.why)" -or $generic -contains "$($_.impact)" })
if ($hits.Count -eq 0) { Ok "no card carries the boilerplate" } else { Bad ("boilerplate on: {0}" -f (($hits | ForEach-Object { $_.title }) -join '; ')) }

Sec "2  gain cards"
$gain = @($all | Where-Object { $_.dimension -eq 'holding' -and $_.meta.gain_pct -ne $null -and ("$($_.title)" -match 'since you bought' -or "$($_.title)" -match '^Trim .* to target') })
if ($gain.Count -eq 0) { Skip "no holding is up enough for a gain card" }
foreach ($c in $gain) {
    Note ("{0} | {1}" -f $c.title, $c.action)
    if ("$($c.title)" -match '^Trim (\S+) to target') {
        if ([double]$c.meta.weight_pct -gt [double]$c.meta.target_pct -and [math]::Abs([double]$c.est_amount - [double]$c.meta.over_target_ils) -lt 0.01) { Ok "$($Matches[1]): over target ($($c.meta.weight_pct)% vs $($c.meta.target_pct)%), trim sized on the excess" }
        else { Bad "trim-to-target card that is not over target" }
    } else {
        if ("$($c.title) $($c.action) $(@($c.how) -join ' ')" -notmatch '(?i)trim') { Ok "$($c.meta.ticker): one line, no trim suggestion (target $($c.meta.target_pct)%)" }
        else { Bad "$($c.meta.ticker): gain card under target still suggests a trim" }
    }
}

Sec "3  'still to move' only on trade cards"
$back = @($all | Where-Object { "$($_.refired_because)" -match 'to move|asked for about' })
$bad = @($back | Where-Object { -not $_.apply -or $_.apply.kind -eq 'none' })
if ($bad.Count -eq 0) { Ok ("{0} card(s) say money is still to move, all of them trades" -f $back.Count) } else { Bad ("non-trade cards claim money to move: {0}" -f (($bad | ForEach-Object { $_.title }) -join '; ')) }

Sec "4  the numbers the labels name"
$perf = Api POST "/api/v1/portfolio/performance"
if ($perf.ok -and $perf.benchmark_return_pct -ne $null -and $perf.total_return_pct -ne $null) {
    Ok ("chart: You {0}% . {1} {2}% (excess {3} pts) over {4} days" -f [math]::Round([double]$perf.total_return_pct, 1), $perf.benchmark, [math]::Round([double]$perf.benchmark_return_pct, 1), $perf.excess_return_pct, $perf.observations)
} else { Skip "performance payload has no benchmark" }
$nh = Api GET "/api/v1/portfolio/nav-history?range=1Q"
if ($nh.recorded_since) { Ok ("history: recorded since {0}, {1} gap(s): {2}" -f $nh.recorded_since, @($nh.gaps).Count, ((@($nh.gaps) | ForEach-Object { "$($_.from)..$($_.to)" }) -join ', ')) } else { Skip "no recorded history" }

Note "BY EYE on the phone: Explore > chart title 'If you had held today's holdings for the past year',"
Note "caption 'You +x% . SPY +y%'; Today > history: 'Recorded since <d Mon> (one gap: ...)', 2 decimals, dates behind Details."
Stop-Smoke
