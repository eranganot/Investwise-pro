# SMOKE - A3r (D132): one exit per holding -- the protection manager.
#
#   .\scripts\smoke\smoke-a3r-one-exit-per-holding.ps1 -Sha <short sha of the merge>
#
#   1  no Today card and no rule suggestion arms a stop-loss, trailing stop, take-profit or profit-protect
#   2  the stored plan exists (nightly 05:40 UTC, or POST /plan/protection/refresh) -- else SKIP
#   3  core funds keep no exit; each sleeve / 2x-3x holding ends with at most one trailing stop
#   4  k = 2, 3, 4 measured, each with an amount at the bar
#   5  the card, when there is one, is the only card about exits (the retune card is not beside it)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "A3r"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }
$exitTypes = @("stop_loss", "trailing_stop", "take_profit", "profit_protect")

Sec "1  nothing generated arms an exit"
$t = Api GET "/api/v1/recommendations"
$all = @(@($t.recommendations) + @($t.more_recommendations))
$arming = @($all | Where-Object { $_.apply -and $_.apply.kind -eq "create_rules" -and @($_.apply.rules | Where-Object { $exitTypes -contains $_.rule_type }).Count })
if ($arming.Count) { Bad ("{0} card(s) arm an exit: {1}" -f $arming.Count, (($arming | ForEach-Object { $_.id }) -join ", ")) } else { Ok "no Today card arms an exit" }
$sg = Api GET "/api/v1/rules/suggestions"
$sx = @(@($sg.suggestions) | ForEach-Object { @($_.rules) } | Where-Object { $exitTypes -contains $_.rule_type })
if ($sx.Count) { Bad ("{0} suggested exit(s)" -f $sx.Count) } else { Ok "no suggested exit" }

Sec "2  the stored plan"
$p = Api GET "/api/v1/plan/protection"
if (-not $p.ok) { Skip ("not run on this build yet ({0})" -f $p.reason); Stop-Smoke }
$roles = $p.roles.PSObject.Properties
Ok ("measured {0}: {1} core, {2} sleeve/2x-3x" -f $p.as_of, @($roles | Where-Object { $_.Value -eq "core" }).Count, @($roles | Where-Object { $_.Value -eq "sleeve" }).Count)

Sec "3  what remains after the change"
if (-not $p.changes) { Ok "nothing to change: the rules already follow the policy" }
else {
    $rules = @((Api GET "/api/v1/rules").rules)
    $gone = @($p.changes.delete | ForEach-Object { $_.rule_id })
    $bad = 0
    foreach ($r in $roles) {
        $left = @($rules | Where-Object { $_.ticker -eq $r.Name -and ($exitTypes -contains $_.rule_type) -and ($gone -notcontains [string]$_.id) })
        if ($r.Value -eq "core" -and $left.Count) { $bad++; Bad ("{0} (core) keeps {1} exit(s)" -f $r.Name, $left.Count) }
        if ($r.Value -eq "sleeve" -and @($left | Where-Object { $_.rule_type -ne "trailing_stop" }).Count) { $bad++; Bad ("{0} keeps a non-trailing exit" -f $r.Name) }
        if ($r.Value -eq "sleeve" -and $left.Count -gt 1) { $bad++; Bad ("{0} keeps {1} exits" -f $r.Name, $left.Count) }
    }
    if (-not $bad) { Ok ("after Accept: {0} removed, {1} set, {2} added" -f @($p.changes.delete).Count, @($p.changes.edit).Count, @($p.changes.create).Count) }
}

Sec "4  widths measured"
$ks = @($p.tried | ForEach-Object { [double]$_.k })
if (-not $p.changes) { Skip "nothing to measure" }
elseif (($ks -contains 2) -and ($ks -contains 3) -and ($ks -contains 4) -and -not @($p.tried | Where-Object { -not $_.stats.amount_at_confidence }).Count) {
    Ok ("chosen {0}x: {1:N0} -> {2:N0} at the bar" -f $p.k, [double]$p.current.amount_at_confidence, [double]$p.best.amount_at_confidence)
} else { Bad ("widths measured: {0}" -f ($ks -join ", ")) }

Sec "5  the card"
$c = @($all | Where-Object { $_.id -eq "rec_protection_one_exit" })
if (-not $c.Count) { Skip "no card (nothing to change, or the rules moved since the measurement)" }
else {
    Ok $c[0].title
    if (@($all | Where-Object { $_.id -eq "rec_goal_protect_book" }).Count) { Bad "the retune card is beside it" } else { Ok "the only card about exits" }
}
Stop-Smoke
