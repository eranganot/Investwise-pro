# SMOKE - P17a (D133): three more stock rankings, each held against SPY, sized 15/25/30.
#
#   .\scripts\smoke\smoke-p17a-more-rankings-vs-spy.ps1 -Sha <short sha of the merge>
#
#   1  R1 measures on production with a vs-SPY test and a book effect at 25% (slow: ~1-3 minutes)
#   2  a size other than 15/25/30 is refused (422)
#
# READ-ONLY (GET only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "P17a"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  R1 measured against SPY"
$r = try { Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/v1/strategies/round2/stocks/R1/measure?sleeve_pct=25" -Headers $script:ApiHeaders -TimeoutSec 900 } catch { $null }
if (-not $r -or -not $r.ok) { Bad ("R1 did not measure: {0}" -f $(if ($r) { $r.reason } else { "no response" })) }
else {
    $v = $r.tests.vs_spy
    if ($null -ne $v.spy_cagr_pct) { Ok ("R1 {0}%/yr vs SPY {1}%/yr (beats: {2})" -f $v.cagr_pct, $v.spy_cagr_pct, $v.pass) } else { Bad "no SPY figure" }
    if ([double]$r.sleeve_pct -eq 25 -and $r.book_effect) { Ok ("book at 25%: {0} -> {1}%/yr" -f $r.book_effect.without.cagr_pct, $r.book_effect.with.cagr_pct) } else { Bad "no book effect at 25%" }
}

Sec "2  sizes are 15, 25 or 30"
$code = try { (Invoke-WebRequest -Method GET -Uri "$BaseUrl/api/v1/strategies/round2/stocks/R1/measure?sleeve_pct=40" -Headers $script:ApiHeaders -TimeoutSec 60).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
if ($code -eq 422) { Ok "40% refused (422)" } else { Bad ("40% answered {0}" -f $code) }
Stop-Smoke
