# SMOKE - C28 / C17 / C13 (small fixes).
#
#   .\scripts\smoke\smoke-c28-band-notes-and-refusals.ps1 -Sha <short sha of the merge>
#
#   1  every rebalance band's note quotes the live plan target its status measures against (C28)
#   2  C17 (a refused Accept says why and names its legs) and C13 (the unspent-leg refund) are proven by
#      tests/test_c13_c17_c28_small_fixes.py: a smoke never makes a refusal or a trade happen
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C28"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  band notes quote the live target"
$bands = @((Api GET "/api/v1/rules").rules | Where-Object { $_.rule_type -eq "rebalance_band" })
if (-not $bands.Count) { Skip "no rebalance bands" }
foreach ($b in $bands) {
    $live = if ($b.status -match "vs plan ([\d.]+)%") { [double]$Matches[1] } else { $null }
    $said = if ($b.note -match "^Your plan targets ([\d.]+)% of the book") { [double]$Matches[1] } else { $null }
    if ($null -eq $said) { Ok ("{0}: own note kept ({1})" -f $b.ticker, $b.note) }
    elseif ($null -eq $live) { Skip ("{0}: no live target in the status" -f $b.ticker) }
    elseif ([math]::Abs($said - $live) -le 0.1) { Ok ("{0}: note {1}% = live {2}%" -f $b.ticker, $said, $live) }
    else { Bad ("{0}: note {1}% vs live {2}%" -f $b.ticker, $said, $live) }
}

Sec "2  C17 and C13"
Skip "proven by tests/test_c13_c17_c28_small_fixes.py (a smoke makes no refusal or trade happen)"
Stop-Smoke
