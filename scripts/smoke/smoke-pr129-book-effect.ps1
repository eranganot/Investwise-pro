# SMOKE - PR 129: what a strategy does to the whole book (D93).
#
#   .\scripts\smoke\smoke-pr129-book-effect.ps1 -Sha <short sha of the merge>
#
#   1  the book effect stays behind the login (401 without a key)
#   2  with a key: one catalogue strategy's effect, then every candidate's (the gate record) -- SKIP without a key
#   3  nothing else changed: the catalogue still lists 9 strategies, no candidate among them
#
# READ-ONLY (both endpoints measure and save nothing). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 129"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  behind the login"
foreach ($p in @("/api/v1/strategies/btm_qqq_hold/book-effect", "/api/v1/strategies/candidates/book-effect")) {
    try { Invoke-RestMethod -Uri "$BaseUrl$p" -TimeoutSec 60 | Out-Null; Bad "$p answered without a key" }
    catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "$p needs a key (401)" } else { Bad "$p answered $code" } }
}

Sec "2  measured on your book"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY to record the book effect" }
else {
    $one = Api GET "/api/v1/strategies/btm_qld_trend/book-effect"
    if ($one -and $one.ok -and $null -ne $one.return_per_risk_gain) {
        Ok ("btm_qld_trend at {0}%: return/risk {1} -> {2}, worst drop -{3}% -> -{4}%, improves={5}" -f $one.sleeve_pct, $one.without.return_per_risk, $one.with.return_per_risk, $one.without.max_drawdown_pct, $one.with.max_drawdown_pct, $one.improves)
    } elseif ($one) { Bad ("not measured: {0} {1}" -f $one.reason, $one.detail) }
    $all = Api GET "/api/v1/strategies/candidates/book-effect" 900
    if ($all -and $all.ok) {
        $n = 0
        foreach ($c in @($all.candidates)) {
            if ($c.ok) { $n++; Note ("{0,-4} {1,-40} gain {2,7}  drop {3,6} pts  improves={4}" -f $c.candidate_code, $c.name, $c.return_per_risk_gain, $c.worst_drop_change_pts, $c.improves) }
            else { Note ("{0} not measured: {1}" -f $c.strategy_id, $c.detail) }
        }
        if ($n -eq 11) { Ok "all 11 candidates measured against your book" } else { Bad "$n of 11 candidates measured" }
    } else { Bad "candidates/book-effect did not answer" }
}

Sec "3  nothing else changed"
$bt = Api GET "/api/v1/strategies/backtests"
if ($bt) {
    $ids = @($bt.strategies | ForEach-Object { $_.id })
    if ($ids.Count -eq 9 -and -not ($ids -contains "btm_sector_leaders")) { Ok "9 catalogue entries, no failed candidate among them" } else { Bad "catalogue: $($ids -join ',')" }
} else { Bad "/strategies/backtests did not answer" }

Stop-Smoke
