# SMOKE - PR 134b: round 2 (D99) -- hot stocks on US115, diversifiers funded from the leveraged sleeve.
#
#   .\scripts\smoke\smoke-pr134b-round2.ps1 -Sha <short sha of the merge>
#
#   1  option 3 answers end to end for one quick candidate (D4 at 1x): taken from the leveraged sleeve, F1 + F2 reported
#   2  scale is validated (3 -> 422)
#   3  the D7 / D8 book effect now shares one window (PR 134a fix, checked on production)
#   4  nothing else changed: the catalogue lists the same strategies; endpoints behind the login
#
# READ-ONLY. The full round is scripts\measure-round2.ps1. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 134b"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  option 3, one candidate"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $r = Api GET "/api/v1/strategies/round2/funding/D4/measure?scale=1" 900
    if ($r -and $r.ok) {
        Ok ("D4 x1: moved {0}% from {1}; book {2}% -> {3}%/yr, QQQ {4}%; F1 {5} F2 {6}" -f $r.moved_pct, $r.from, $r.book_without.cagr_pct, $r.book_with.cagr_pct, $r.qqq_cagr_pct, $r.tests.F1.pass, $r.tests.F2.pass)
        if ($r.book_without.start -eq $r.book_with.start) { Ok "one window ($($r.book_with.start))" } else { Bad "windows differ" }
    } elseif ($r) { Bad ("not measured: {0} {1}" -f $r.reason, $r.detail) } else { Bad "funding measure did not answer" }
}

Sec "2  scale"
if (-not $haveKey) { Skip "no agent key" }
else {
    try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/strategies/round2/funding/D4/measure?scale=3" -Headers $script:ApiHeaders -TimeoutSec 60 | Out-Null; Bad "scale=3 accepted" }
    catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 422) { Ok "scale=3 refused (422)" } else { Bad "scale=3 answered $code" } }
}

Sec "3  young funds on one window"
if (-not $haveKey) { Skip "no agent key" }
else {
    $d8 = Api GET "/api/v1/strategies/diversifiers/D8/measure" 900
    if ($d8 -and $d8.ok -and $d8.book_effect) {
        if ($d8.book_effect.without.start -eq $d8.book_effect.with.start) { Ok ("D8 book effect on one window from {0}" -f $d8.book_effect.with.start) } else { Bad ("D8 windows differ: {0} vs {1}" -f $d8.book_effect.without.start, $d8.book_effect.with.start) }
    } else { Bad "D8 not measured" }
}

Sec "4  nothing else changed"
if ($haveKey) {
    $bt = Api GET "/api/v1/strategies/backtests"
    $ids = @($bt.strategies | ForEach-Object { $_.id })
    if (-not ($ids | Where-Object { $_ -like "div_*" -or $_ -like "r2_*" })) { Ok "no candidate in the catalogue ($($ids.Count) strategies)" } else { Bad "a candidate leaked into the catalogue" }
}
foreach ($p in @("/api/v1/strategies/round2/funding/D4/measure", "/api/v1/strategies/round2/stocks/S1/measure")) {
    try { Invoke-RestMethod -Uri "$BaseUrl$p" -TimeoutSec 60 | Out-Null; Bad "$p answered without a key" }
    catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "$p needs a key (401)" } else { Bad "$p answered $code" } }
}

Stop-Smoke
