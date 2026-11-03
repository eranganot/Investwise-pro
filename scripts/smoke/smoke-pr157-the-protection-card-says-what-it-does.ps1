# SMOKE - PR 157: the protection card says only what Accept does; cash-like funds get no stop (D141).
#
#   .\scripts\smoke\smoke-pr157-the-protection-card-says-what-it-does.ps1 -Sha <short sha of the merge>
#
#   1  no protection card puts an exit on a cash-like fund (BIL, SGOV, SHV)
#   2  if the card shows: no zero count in the title, no "several on one fund", a bounded width says so
#   3  NOT CHANGED: the rebalance bands and the book's drawdown guard are still armed
#
# Before the merge, on f926449, 1 FAILs (the card adds a 5% BIL trailing stop) and 2 FAILs
# ("remove 0 rules", "Today 0 exit rules ... several on one fund"): the card seen on 2026-11-03.
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 157"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$cashLike = @("BIL", "SGOV", "SHV")
$t = Api GET "/api/v1/recommendations"
if (-not $t) { Bad "Today did not answer"; Stop-Smoke }
$all = @($t.recommendations) + @($t.more_recommendations)
$card = $all | Where-Object { $_ -and $_.id -eq "rec_protection_one_exit" } | Select-Object -First 1

Sec "1  no exit on a cash-like fund"
if (-not $card) { Ok "no protection card on Today (nothing to change)" }
else {
    $targets = @($card.apply.create) + @($card.apply.edit) | Where-Object { $_ } | ForEach-Object { $_.ticker }
    $bad = @($targets | Where-Object { $cashLike -contains $_ })
    if ($bad.Count) { Bad ("the card sets a stop on {0}" -f ($bad -join ", ")) }
    else { Ok ("the card sets stops on {0}" -f (($targets -join ", ") -replace "^$", "nothing")) }
}

Sec "2  the card says only what it does"
if (-not $card) { Skip "no card to read" }
else {
    Note $card.title
    if ($card.title -match "\b0 (rules?|trailing stops?)") { Bad "zero count in the title" } else { Ok "no zero count in the title" }
    if ($card.why -match "several on one fund") { Bad "the fixed 'several on one fund' sentence is back" }
    else { Ok "first sentence: $(($card.why -split '\. ')[0])" }
    foreach ($h in @($card.how)) {
        if ($h -match "trailing stop 5% \((\d+(\.\d+)?)x" -and $h -notmatch "minimum") {
            Bad "a 5% stop does not say it is the minimum: $h"
        }
    }
}

Sec "3  NOT CHANGED"
$rl = Api GET "/api/v1/rules"
if (-not $rl) { Bad "rules did not answer"; Stop-Smoke }
$rules = @($rl.rules)
$bands = @($rules | Where-Object { $_.rule_type -eq "rebalance_band" -and $_.active })
if ($bands.Count -ge 1) { Ok ("{0} rebalance band(s) armed: {1}" -f $bands.Count, (($bands | ForEach-Object { $_.ticker }) -join ", ")) }
else { Bad "no rebalance band is armed" }
$guard = @($rules | Where-Object { $_.rule_type -eq "drawdown_guard" -and $_.active })
if ($guard.Count -eq 1) { Ok "book drawdown guard armed ($($guard[0].level)%)" } else { Bad "book drawdown guard missing" }
$cashExits = @($rules | Where-Object { $cashLike -contains $_.ticker -and
    @("stop_loss", "trailing_stop", "take_profit", "profit_protect") -contains $_.rule_type })
if ($cashExits.Count) { Note ("{0} exit(s) still on a cash-like fund (the card removes them)" -f $cashExits.Count) }

Stop-Smoke
