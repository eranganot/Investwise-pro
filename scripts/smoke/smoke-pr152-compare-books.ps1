# SMOKE - PR 152: POST /plan/optimizer/compare scores hypothetical sleeve sets, read-only.
#
#   .\scripts\smoke\smoke-pr152-compare-books.ps1 -Sha <short sha of the merge>
#
#   1  the held book scores: ok, one row, a goal chance and an amount at the confidence bar
#   2  an unknown strategy is refused (400)
#   3  NOT CHANGED: the sleeve set is the same after both calls
#
# READ-ONLY (the endpoint stores nothing). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 152"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$before = @((Api GET "/api/v1/plan/sleeves").sleeves | ForEach-Object { "{0}:{1}" -f $_.strategy_id, $_.sleeve_pct }) -join ","
$book = @{}
foreach ($s in @((Api GET "/api/v1/plan/sleeves").sleeves)) { $book[$s.strategy_id] = [double]$s.sleeve_pct }

Sec "1  the held book scores"
$body = @{ books = @($book) } | ConvertTo-Json -Depth 5
$r = Invoke-RestMethod -Method POST -Uri ($BaseUrl + "/api/v1/plan/optimizer/compare") -Headers $script:ApiHeaders -ContentType "application/json" -Body $body -TimeoutSec 600
if ($r.ok -and @($r.books).Count -eq 1 -and $null -ne $r.books[0].p_goal_pct) {
    Ok ("held book: goal chance {0}%, {1} at the bar" -f $r.books[0].p_goal_pct, $r.books[0].amount_at_confidence)
} else { Bad ("compare did not score the held book: {0}" -f ($r | ConvertTo-Json -Depth 4 -Compress)) }

Sec "2  an unknown strategy is refused"
$code = 0
try { Invoke-RestMethod -Method POST -Uri ($BaseUrl + "/api/v1/plan/optimizer/compare") -Headers $script:ApiHeaders -ContentType "application/json" -Body '{"books":[{"no_such_strategy":10}]}' | Out-Null; $code = 200 }
catch { $code = [int]$_.Exception.Response.StatusCode }
if ($code -eq 400) { Ok "400 for an unknown strategy" } else { Bad ("got {0}" -f $code) }

Sec "3  NOT CHANGED"
$after = @((Api GET "/api/v1/plan/sleeves").sleeves | ForEach-Object { "{0}:{1}" -f $_.strategy_id, $_.sleeve_pct }) -join ","
if ($after -eq $before) { Ok ("sleeves unchanged: {0}" -f $after) } else { Bad ("sleeves changed: {0} -> {1}" -f $before, $after) }
Stop-Smoke
