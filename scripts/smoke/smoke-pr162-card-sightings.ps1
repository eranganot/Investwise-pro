# SMOKE - PR 162 (C34b): Today stamps its cards; the summary counts cards no push named.
#
#   .\scripts\smoke\smoke-pr162-card-sightings.ps1 -Sha <short sha>
#
#   1  a Today request stamps every card it shows (GET /recommendations, then
#      /cards/sightings lists each of those ids, seen just now)
#   2  a simulated build stamps nothing new (?simulate_cash=)
#   3  the summary answers: never-pushed and short-lived counts present
#   4  NOT CHANGED: Today builds; the push job still reports its last run
#
# READS ONLY, with one exception it exists to check: GET /recommendations now
# writes the card_sightings bookkeeping rows (no portfolio data). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 162"
Assert-Build -Sha $Sha -Schema '0030_card_sightings' -Why "(PR 160's table; no migration here)"

Sec "1  Today stamps its cards"
$t0 = (Get-Date).ToUniversalTime().AddSeconds(-5)
$r = Api GET "/api/v1/recommendations"
$ids = @(@($r.recommendations) | Where-Object { $_.id } | ForEach-Object { "$($_.id)" })
$s = Api GET "/api/v1/cards/sightings?days=1"
$seen = @{}; foreach ($c in @($s.cards)) { $seen["$($c.card_id)"] = $c }
$missing = @($ids | Where-Object { -not $seen.ContainsKey($_) })
$stale = @($ids | Where-Object { $seen.ContainsKey($_) -and ([datetime]$seen[$_].last_seen).ToUniversalTime() -lt $t0 })
Note ("Today: {0} card(s): {1}" -f $ids.Count, ($ids -join ','))
if ($ids.Count -eq 0) { Skip "Today has no card right now" }
elseif ($missing.Count -eq 0 -and $stale.Count -eq 0) { Ok "every card on Today was stamped just now" }
else { Bad ("not stamped: {0}; not refreshed: {1}" -f ($missing -join ','), ($stale -join ',')) }

Sec "2  a simulated build stamps nothing"
$before = @($s.cards).Count
$null = Api GET "/api/v1/recommendations?simulate_cash=2000"
$s2 = Api GET "/api/v1/cards/sightings?days=1"
if (@($s2.cards).Count -eq $before) { Ok "no new sighting from a simulation" } else { Bad ("{0} -> {1} rows after a simulation" -f $before, @($s2.cards).Count) }

Sec "3  the summary"
$s14 = Api GET "/api/v1/cards/sightings?days=14"
if ($null -ne $s14.never_pushed -and $null -ne $s14.never_pushed_short_lived) {
    Ok ("{0} card(s) in 14 days; never pushed {1}; of those short-lived {2}" -f $s14.count, $s14.never_pushed, $s14.never_pushed_short_lived)
} else { Bad "summary fields missing" }

Sec "4  NOT CHANGED"
if ($r -and $null -ne $r.recommendations) { Ok "Today builds" } else { Bad "Today did not build" }
$p = Api GET "/api/v1/push/status"
if ($p) { Ok "push status answers" } else { Bad "push status did not answer" }
Stop-Smoke
