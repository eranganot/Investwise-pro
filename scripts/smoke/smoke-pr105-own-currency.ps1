# SMOKE - PR 105: every share count is sized in the price's own currency.
#
#   .\scripts\smoke\smoke-pr105-own-currency.ps1 -Sha <short sha of the merge>
#
#   1  Today builds with nothing degraded
#   2  every trim card on Today sells what it says: shares x the row's ILS
#      price = the card's est_amount (+-5%), and no card trims CASH
#   3  "Replace book" is refused while sleeves exist (D-e), dry run
#   4  /portfolio/add pays from cash: an amount far above cash is refused with
#      "not enough cash" (ONLY with -Sha proven: the old build has no cash check
#      and would write)
#   5  non-change: NAV equals the sum of the rows
#
# READ-ONLY: the only POSTs are a dry-run replace and an add that is refused
# before it writes. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 105"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 105 has no migration)"

$pf = Api GET "/api/v1/portfolio"
$rows = @($pf.positions)

Sec "1  Today builds"
$r = Api GET "/api/v1/recommendations"
if ($null -eq $r) { Bad "/recommendations failed" }
elseif (@($r.degraded).Count -eq 0) { Ok "built, degraded = []" }
else { Bad ("degraded: {0}" -f (@($r.degraded) -join ', ')) }

Sec "2  trims sell what they say"
$cards = @($r.recommendations) + @($r.more_recommendations) + @(@($r.headsups) | Where-Object { $_.card } | ForEach-Object { $_.card })
$trims = @($cards | Where-Object { $_.apply -and $_.apply.kind -eq 'trim' })
$cashTrim = @($trims | Where-Object { "$($_.apply.ticker)".ToUpper() -eq 'CASH' })
if ($cashTrim.Count -eq 0) { Ok "no card trims CASH" } else { Bad "a card trims CASH: $($cashTrim[0].title)" }
$checked = 0
foreach ($t in $trims) {
    if (-not $t.est_amount) { continue }
    $row = $rows | Where-Object { "$($_.ticker)".ToUpper() -eq "$($t.apply.ticker)".ToUpper() } | Select-Object -First 1
    if (-not $row -or [double]$row.quantity -le 0) { Bad "$($t.title): trims a ticker the book doesn't hold"; continue }
    $pxIls = [double]$row.value_ils / [double]$row.quantity
    $sold = [double]$t.apply.shares * $pxIls
    $claim = [double]$t.est_amount
    $checked++
    if ([math]::Abs($sold - $claim) -le 0.05 * $claim + 1) { Ok ("{0}: sells ILS {1:N0}, card says ILS {2:N0}" -f $t.title, $sold, $claim) }
    else { Bad ("{0}: sells ILS {1:N0}, card says ILS {2:N0}" -f $t.title, $sold, $claim) }
}
if ($checked -eq 0) { Skip "no sized trim card on Today right now (0 actionable is normal for this book)" }

Sec "3  Replace book refused while sleeves exist (D-e)"
$sl = Api GET "/api/v1/plan/sleeves"
$ids = @(@($sl.sleeves) | ForEach-Object { $_.strategy_id }) | Where-Object { $_ }
if ($ids.Count -eq 0) { Skip "no sleeves on this book: nothing to refuse" }
else {
    $body = @{ mode = 'replace'; dry_run = $true } | ConvertTo-Json
    $rep = $null
    try { $rep = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/strategies/grow_quality/load-basket" -Headers $script:ApiHeaders -ContentType 'application/json' -Body $body -TimeoutSec 120 } catch {}
    if ($rep -and $rep.ok -eq $false -and @($rep.sleeves).Count -gt 0) { Ok ("refused: {0}" -f $rep.error) }
    else { Bad ("not refused: {0}" -f ($rep | ConvertTo-Json -Compress -Depth 3)) }
}

Sec "4  /portfolio/add pays from cash"
if (-not $Sha) { Skip "needs -Sha: on an old build this POST would WRITE a holding" }
else {
    $big = [math]::Ceiling(([double]$pf.cash_ils + 1000000) / 3)
    $body = @{ ticker = 'IAU'; amount = $big; asset_class = 'Commodities'; market = 'NYSE' } | ConvertTo-Json
    $add = $null
    try { $add = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/portfolio/add" -Headers $script:ApiHeaders -ContentType 'application/json' -Body $body -TimeoutSec 120 } catch {}
    if ($add -and $add.ok -eq $false -and "$($add.error)" -match 'not enough cash') { Ok ("refused: {0}" -f $add.error) }
    else { Bad ("not refused: {0}" -f ($add | ConvertTo-Json -Compress)) }
    $pf2 = Api GET "/api/v1/portfolio"
    if ([math]::Abs([double]$pf2.cash_ils - [double]$pf.cash_ils) -lt 0.01 -and @($pf2.positions).Count -eq $rows.Count) { Ok "book untouched" }
    else { Bad "the book changed during the smoke" }
}

Sec "5  non-change: NAV is the sum of the rows"
$sum = ($rows | Measure-Object -Property value_ils -Sum).Sum
if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("nav_ils {0:N2} = sum {1:N2}" -f [double]$pf.nav_ils, $sum) }
else { Bad ("nav_ils {0:N2} vs sum {1:N2}" -f [double]$pf.nav_ils, $sum) }
Stop-Smoke
