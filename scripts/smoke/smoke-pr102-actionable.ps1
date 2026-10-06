# SMOKE - PR 102: every card can be acted on; the rest is a heads-up.
#
#   .\scripts\smoke\smoke-pr102-actionable.ps1 -Sha abc1234
#
#   1  every card on Today (first 12 and the rest) has an Accept that does
#      something: no apply kind "none"
#   2  heads-ups: one line each; a near-cap one may carry ONE act (raise the cap
#      to target + band, an edit_rule) and only when the cap sits within a band
#   3  D4 sizes: tax cards save >= 50 ILS; trade cards >= max(100, 0.5% of book)
#      (triggered-rule cards exempt); no "Put idle cash to work"
#   4  the rule banner: every triggered rule is carded (a card or a heads-up) or
#      blocked; none was healed because it became a heads-up
#
# READ-ONLY: accepts nothing, dismisses nothing. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 102"
Assert-Build -Sha $Sha -Schema '0026_plan_frontier_settings' -Why "(unchanged - no migration)"

$recs = Api GET "/api/v1/recommendations"
$cards = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$heads = @($recs.headsups | Where-Object { $_ })
Note ("{0} cards, {1} heads-ups, dropped: {2}" -f $cards.Count, $heads.Count, ((@($recs.headsups_dropped) | ForEach-Object { "$($_.id) ($($_.reason))" }) -join ', '))

Sec "1  every card has an Accept"
foreach ($c in $cards) { Note ("[{0}] {1}" -f $c.apply.kind, $c.title) }
$none = @($cards | Where-Object { -not $_.apply -or -not $_.apply.kind -or $_.apply.kind -eq 'none' })
if ($none.Count -eq 0) { Ok "no card is advice-with-a-button" } else { Bad ("advice cards: {0}" -f (($none | ForEach-Object { $_.title }) -join '; ')) }

Sec "2  heads-ups"
foreach ($h in $heads) { Note ("{0} | {1}{2}" -f $h.title, $h.line, $(if ($h.act) { "  [act: $($h.act.label)]" } else { '' })) }
$bad = @($heads | Where-Object { -not $_.id -or -not $_.title })
if ($bad.Count -eq 0) { Ok "$($heads.Count) heads-up(s), each with an id and a title" } else { Bad "malformed heads-up" }
foreach ($h in @($heads | Where-Object { $_.apply })) {
    if ($h.apply.kind -eq 'edit_rule' -and $h.apply.target_rule_id -and $h.act.label -match 'Raise the cap') { Ok "$($h.title): act raises the cap to $($h.apply.level)%" }
    else { Bad "$($h.title): act is not a cap edit ($($h.apply.kind))" }
}

Sec "3  sizes (D4)"
$pf = Api GET "/api/v1/portfolio"
$nav = [double]$pf.nav_ils; if (-not $nav) { $nav = [double]$pf.nav }
$floor = [math]::Max(100.0, 0.005 * $nav)
$tax = @($cards | Where-Object { $_.dimension -eq 'tax' -and [double]$_.est_amount -lt 50 })
if ($tax.Count -eq 0) { Ok "no tax card saves under 50 ILS" } else { Bad ("small tax cards: {0}" -f (($tax | ForEach-Object { "$($_.title) $($_.est_amount)" }) -join '; ')) }
$trade = @('trim', 'sell_position', 'buy_funded', 'redeploy_cash', 'fund_sleeve', 'fund_plan', 'core_to_basket')
$small = @($cards | Where-Object { $trade -contains $_.apply.kind -and -not "$($_.id)".StartsWith('rule_') -and $_.est_amount -ne $null -and [math]::Abs([double]$_.est_amount) -lt $floor })
if ($small.Count -eq 0) { Ok ("no trade card under {0:N0} ILS (book {1:N0})" -f $floor, $nav) } else { Bad ("small trade cards: {0}" -f (($small | ForEach-Object { "$($_.title) $($_.est_amount)" }) -join '; ')) }
if (@($cards + $heads | Where-Object { "$($_.title)" -eq 'Put idle cash to work' }).Count -eq 0) { Ok "no 'Put idle cash to work'" } else { Bad "'Put idle cash to work' still shows" }

Sec "4  the rule banner"
$rb = $recs.rule_banner
Note ("triggered {0} | carded {1} | blocked {2} | healed {3}" -f (@($rb.triggered) -join ','), (@($rb.carded) -join ','), (@($rb.blocked) -join ','), (@($rb.healed) -join ','))
$orphan = @(@($rb.triggered) | Where-Object { @($rb.carded) -notcontains $_ -and @($rb.blocked) -notcontains $_ -and @($rb.healed) -notcontains $_ })
if (@($rb.healed).Count -eq 0 -and $orphan.Count -eq 0) { Ok "every triggered rule is carded or blocked; none healed" } else { Bad ("healed {0}, orphaned {1}" -f (@($rb.healed) -join ','), ($orphan -join ',')) }

Note "BY EYE on the phone: Today > bottom, above Holding review: 'Heads-up . N' collapsed; tap to expand; swipe left or x dismisses for good."
Stop-Smoke
