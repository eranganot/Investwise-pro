# SMOKE - PR 103: shorter cards.
#
#   .\scripts\smoke\smoke-pr103-short-cards.ps1 -Sha abc1234
#
#   1  every card carries a one-or-two-sentence summary and a chips list; none
#      grows past ~2x the shape (title + summary + Accept <= 150 words)
#   2  the goal group: "Your goal" said ONCE (goal_summary on the payload), no goal
#      card's summary repeats it, every goal card has before -> after chips
#   3  the protection card follows the plan's example: "Retune your protection:
#      goal odds X% -> Y%", odds / halving / median / worst 5% chips
#   UI (Details fold, chip row, "Your goal" box) by eye on the phone.
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 103"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

$recs = Api GET "/api/v1/recommendations"
$cards = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
function W($t) { return @("$t".Split(' ', [StringSplitOptions]::RemoveEmptyEntries)).Count }

Sec "1  the shape"
foreach ($c in $cards) {
    $n = (W $c.title) + (W $c.summary) + (W $c.action)
    Note ("{0,3} words | {1} chip(s) | {2}" -f $n, @($c.chips).Count, $c.title)
    if ($n -gt 150) { Bad "$($c.title): $n words, far past the shape" }
}
if (@($cards | Where-Object { $null -eq $_.chips }).Count -eq 0) { Ok "every card carries a chips list" } else { Bad "a card has no chips field" }
if (@($cards | Where-Object { ((W $_.title) + (W $_.summary) + (W $_.action)) -gt 150 }).Count -eq 0) { Ok "no card grows past ~2x the shape" }

Sec "2  the goal group"
$goal = @($cards | Where-Object { $_.dimension -eq 'goal' })
if ($goal.Count -eq 0) { Skip "no goal cards" }
else {
    if ("$($recs.goal_summary)".StartsWith('Your goal:')) { Ok ("'Your goal' said once ({0} words)" -f (W $recs.goal_summary)) } else { Bad "no goal_summary on the payload" }
    $rep = @($goal | Where-Object { "$($_.summary)" -match 'Your goal:' })
    if ($rep.Count -eq 0) { Ok "no goal card repeats it" } else { Bad "goal card(s) repeat the goal summary" }
    $nochips = @($goal | Where-Object { @($_.chips).Count -eq 0 })
    if ($nochips.Count -eq 0) { Ok "every goal card has before -> after chips" } else { Bad ("no chips: {0}" -f (($nochips | ForEach-Object { $_.title }) -join '; ')) }
    foreach ($g in $goal) { Note ("{0} | {1} | {2}" -f $g.title, $g.summary, ((@($g.chips) | ForEach-Object { "$($_.label) $($_.before)->$($_.after)" }) -join ', ')) }
}

Sec "3  the protection card"
$p = $goal | Where-Object { "$($_.id)".StartsWith('rec_goal_protect_') } | Select-Object -First 1
if (-not $p) { Skip "no protection card today" }
elseif ($p.title -match '^Retune your protection: goal odds [\d.]+% -> [\d.]+%$' -and (@($p.chips) | ForEach-Object { $_.label }) -join ',' -eq 'odds,halving,median,worst 5%') { Ok "$($p.title)" }
else { Bad "protection card off the example: $($p.title)" }

Note "BY EYE: Today > goal group opens with 'Your goal'; each card: title, 1-2 sentences, 'Accept will...', chips, Details."
Stop-Smoke
