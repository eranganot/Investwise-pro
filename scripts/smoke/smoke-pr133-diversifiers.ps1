# SMOKE - PR 133: wave-3 candidates and gate; the book-effect verdict says which test failed (D98).
#
#   .\scripts\smoke\smoke-pr133-diversifiers.ps1 -Sha <short sha of the merge>
#
#   1  the 13 approved candidates are served, each with an expected risk tier; none is in the catalogue
#   2  one candidate measures end to end (D4, the quickest) with every gate test reported
#   3  the book-effect line carries `failed` and the limits in force; both limits are settings (defaults 0.02 / 2)
#   4  nothing else changed: shell iw-v66; the catalogue still lists the same strategies; endpoints behind the login
#
# READ-ONLY. The full measurement of all 13 is scripts\measure-diversifiers.ps1. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 133"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the candidates"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $d = Api GET "/api/v1/strategies/diversifiers"
    $codes = @($d.candidates | ForEach-Object { $_.code })
    if ($codes.Count -eq 13 -and $codes[0] -eq "D1" -and $codes[12] -eq "D13") { Ok "13 candidates D1-D13" } else { Bad "candidates: $($codes -join ',')" }
    if (@($d.candidates | Where-Object { -not $_.expected_risk }).Count -eq 0) { Ok "each carries an expected risk tier" } else { Bad "a candidate has no risk tier" }
    $bt = Api GET "/api/v1/strategies/backtests"
    $ids = @($bt.strategies | ForEach-Object { $_.id })
    if (-not ($ids | Where-Object { $_ -like "div_*" })) { Ok "none is in the catalogue yet ($($ids.Count) strategies)" } else { Bad "a candidate leaked into the catalogue" }
}

Sec "2  one candidate measured"
if (-not $haveKey) { Skip "no agent key" }
else {
    $r = Api GET "/api/v1/strategies/diversifiers/D4/measure" 900
    if ($r -and $r.ok) {
        Ok ("D4 {0}: {1}%/yr, worst drop -{2}%, risk {3}; QQQ {4}%/yr" -f $r.name, $r.window.cagr_pct, $r.window.max_drawdown_pct, $r.measured_risk, $r.qqq.cagr_pct)
        $missing = @("B1", "G2", "G3", "G4", "G5", "G1") | Where-Object { $null -eq $r.tests.$_ }
        if (-not $missing) { Ok "every gate test reported (passes before G6: $($r.passes_before_g6))" } else { Bad "missing tests: $($missing -join ',')" }
    } elseif ($r) { Bad ("D4 not measured: {0} {1}" -f $r.reason, $r.detail) } else { Bad "measure did not answer" }
}

Sec "3  the book-effect verdict"
if (-not $haveKey) { Skip "no agent key" }
else {
    $e = Api GET "/api/v1/strategies/btm_trend_soxl/book-effect" 600
    if ($e -and $e.ok) {
        if ($null -ne $e.failed -and $e.limits) { Ok ("failed + limits present (improves={0}; {1})" -f $e.improves, (($e.failed | ForEach-Object { $_.text }) -join '; ')) } else { Bad "book-effect has no failed/limits" }
    } elseif ($e) { Skip ("book effect not measurable now: {0}" -f $e.detail) } else { Bad "book-effect did not answer" }
    $pl = Api GET "/api/v1/plan"
    if ([double]$pl.thresholds_defaults.book_effect_ratio_gain -eq 0.02 -and [double]$pl.thresholds_defaults.book_effect_drop_tolerance_pts -eq 2) { Ok "both limits are settings (0.02 / 2)" } else { Bad "book-effect limits missing from the thresholds sheet" }
}

Sec "4  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v66") { Ok "shell iw-v66" } else { Bad "sw.js is not iw-v66" } } catch { Bad "/app/sw.js unreachable" }
foreach ($p in @("/api/v1/strategies/diversifiers", "/api/v1/strategies/diversifiers/D4/measure")) {
    try { Invoke-RestMethod -Uri "$BaseUrl$p" -TimeoutSec 60 | Out-Null; Bad "$p answered without a key" }
    catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "$p needs a key (401)" } else { Bad "$p answered $code" } }
}

Stop-Smoke
