# SMOKE - PR 147.1: the ledger repair previews the unpriced trades, and writes nothing until the owner confirms.
#
#   .\scripts\smoke\smoke-pr147-1-reprice-preview.ps1 -Sha <short sha of the merge>
#
#   1  the repair preview answers, says read_only, and lists unpriced_trades with a confidence each
#   2  every MEASURED row has a price and a value; every row says where its value came from
#   3  the preview wrote nothing: the ledger's count of unpriced trades is unchanged by reading it
#   4  NOT CHANGED: the older sections (duplicates, blind sells) are still in the preview
#
# READ-ONLY (never POSTs the repair; the owner applies it). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 147.1"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

function Holes {
    $rows = @((Api GET "/api/v1/portfolio/ledger?limit=2000").entries)
    return @($rows | Where-Object { $_.side -in @("Buy", "Sell") -and [double]$_.quantity -gt 0 -and
        ([double]$_.price -le 0 -or [double]$_.value_ils -le 0) }).Count
}
$before = Holes
$p = Api GET "/api/v1/portfolio/ledger/repair" 300

Sec "1  the preview lists the unpriced trades"
if (-not $p) { Bad "the repair preview did not answer"; Stop-Smoke }
if ($p.read_only) { Ok "read_only: true" } else { Bad "the preview does not say read_only" }
if ($null -eq $p.unpriced_trades) { Bad "no unpriced_trades section (a build before PR 147.1)"; Stop-Smoke }
$u = @($p.unpriced_trades)
$by = $u | Group-Object confidence | ForEach-Object { "{0} {1}" -f $_.Count, $_.Name }
if ($u.Count -eq 0) { Note "no unpriced trade left (already repaired?)"; Ok "nothing to list" }
else { Ok ("{0} unpriced trade(s): {1}" -f $u.Count, ($by -join ", ")) }

Sec "2  every row says where its numbers come from"
$bad = 0
foreach ($r in $u) {
    if (-not $r.method) { Bad ("{0} {1} {2}: no method" -f $r.at, $r.ticker, $r.side); $bad++ }
    if ($r.confidence -eq "measured" -and (-not ([double]$r.price -gt 0) -or -not ([double]$r.value_ils -gt 0))) {
        Bad ("{0} {1}: measured with no price or value" -f $r.at, $r.ticker); $bad++ }
}
if ($u.Count -and $bad -eq 0) { Ok "every row has a method; every measured row has a price and a value" }
foreach ($r in @($u | Where-Object { $_.ticker -in @("SOXL", "XOM") } | Select-Object -First 4)) {
    Note ("{0} {1} {2} x{3}: price {4}, value {5} [{6}] {7}" -f $r.at, $r.side, $r.ticker, $r.shares, $r.price, $r.value_ils, $r.confidence, $r.method)
}

Sec "3  reading the preview wrote nothing"
$after = Holes
if ($after -eq $before) { Ok ("{0} unpriced trade rows before and after the preview" -f $after) } else { Bad ("{0} before, {1} after the preview" -f $before, $after) }

Sec "4  not changed: the older sections are still there"
foreach ($k in @("duplicates", "blind_sells", "episodes", "truncated_funded_sells", "unrecorded_rebalances")) {
    if ($null -ne $p.$k) { Ok ("{0} present" -f $k) } else { Bad ("{0} missing" -f $k) }
}
Stop-Smoke
