# SMOKE - PR 161 (P9): which holdings moved the book, on the session each one had.
#
#   .\scripts\smoke\smoke-pr161-day-movers.ps1 -Sha <short sha>
#
#   1  /portfolio/day-movers answers; the rows add up to the total; largest first
#   2  every row names its session honestly: "today so far" only for today's date,
#      "last session" otherwise -- never "today" before a market opens (11-05)
#   3  the Today page has the What-moved block
#   4  NOT CHANGED: the recorded history (nav-history) still answers
#
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [string]$Schema = '0029_drop_legacy_tables')
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 161"
Assert-Build -Sha $Sha -Schema $Schema -Why "(no migration in this change)"

Sec "1  the movers"
$d = Api GET "/api/v1/portfolio/day-movers"
$rows = @($d.rows)
if ($d.ok -and $rows.Count -gt 0) { Ok ("{0} holding(s), total {1} ILS ({2}%)" -f $rows.Count, $d.total_ils, $d.total_pct) } else { Bad "no movers returned" }
foreach ($r in $rows) { Note ("{0,-6} {1,9} ILS {2,7}%  {3} {4}" -f $r.ticker, $r.move_ils, $r.move_pct, $r.basis, $r.session) }
if ($rows.Count) {
    $sum = ($rows | Measure-Object -Property move_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$d.total_ils) -le 0.05) { Ok "the rows add up to the total" } else { Bad ("rows sum {0} vs total {1}" -f $sum, $d.total_ils) }
    $abs = @($rows | ForEach-Object { [math]::Abs([double]$_.move_ils) })
    $sorted = @($abs | Sort-Object -Descending)
    if (($abs -join ',') -eq ($sorted -join ',')) { Ok "largest move first" } else { Bad "rows are not sorted by size" }
}
if (@($d.skipped).Count) { Note ("skipped: {0}" -f ((@($d.skipped) | ForEach-Object { "$($_.ticker) ($($_.reason))" }) -join ', ')) }

Sec "2  the session is named honestly"
$today = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
$bad = @($rows | Where-Object { ($_.basis -eq 'today so far' -and $_.session -ne $today) -or ($_.basis -eq 'last session' -and $_.session -ge $today) -or (@('today so far', 'last session') -notcontains $_.basis) })
if ($bad.Count -eq 0) { Ok ("every row's basis matches its date (today is {0})" -f $today) } else { Bad ("mislabelled: {0}" -f (($bad | ForEach-Object { "$($_.ticker) $($_.basis) $($_.session)" }) -join ', ')) }

Sec "3  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($html -and $html -match 'id="todayMovers"' -and $html -match 'async function loadDayMovers\(\)') { Ok "What-moved block present" } else { Bad "no What-moved block on the page" }

Sec "4  NOT CHANGED"
$h = Api GET "/api/v1/portfolio/nav-history?range=1M"
if ($h -and $h.ok) { Ok ("nav-history answers ({0} days)" -f @($h.dates).Count) } else { Bad "nav-history did not answer" }
Stop-Smoke
