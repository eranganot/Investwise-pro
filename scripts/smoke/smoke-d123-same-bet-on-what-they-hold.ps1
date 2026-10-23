# SMOKE - D123 (P14): two sleeves are the same bet when what they HOLD when invested moves together.
#
#   .\scripts\smoke\smoke-d123-same-bet-on-what-they-hold.ps1 -Sha <short sha of the merge>
#
#   1  the stored matrix carries invested correlations (measured by the 03:50 UTC job AFTER the deploy; SKIP before)
#   2  Trend-Filtered Semiconductors vs Volatility-Targeted Leverage: same bet when their invested correlation >= the limit
#   3  every pair marked same bet says why, and none is marked without a reason at or over a limit
#   4  NOT CHANGED: every pair that was same bet on the strategies' own correlation or shared market still is
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "D123"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$c = Api GET "/api/v1/strategies/correlations"
if (-not $c -or -not $c.ok) { Bad "no correlation matrix"; Stop-Smoke }
$lim = [double]$c.correlation_limit; $limO = [double]$c.overlap_limit_pct
$rows = @($c.pairs)
$measured = @($rows | Where-Object { $null -ne $_.invested_correlation })
Note ("matrix as of {0}; limit {1}; {2} pair(s), {3} with an invested correlation" -f $c.as_of, $lim, $rows.Count, $measured.Count)

Sec "1  invested correlations are measured"
if ($measured.Count -eq 0) { Skip "not measured yet (the 03:50 UTC job after the deploy measures them)" }
else { Ok ("{0} of {1} pairs carry an invested correlation" -f $measured.Count, $rows.Count) }

Sec "2  the SOXL and TQQQ sleeves"
$p = @($rows | Where-Object { @($_.a, $_.b) -contains "btm_trend_soxl" -and @($_.a, $_.b) -contains "btm_vol_target_tqqq" })[0]
if (-not $p) { Bad "pair not in the matrix" }
elseif ($null -eq $p.invested_correlation) { Skip ("strategies {0}; invested not measured yet" -f $p.correlation) }
elseif ([double]$p.invested_correlation -ge $lim) {
    if ($p.same_bet) { Ok ("same bet: strategies {0}, invested {1} - {2}" -f $p.correlation, $p.invested_correlation, $p.why) }
    else { Bad ("invested {0} >= {1} but not marked same bet" -f $p.invested_correlation, $lim) }
} else { Ok ("invested {0} is under {1}: still different bets" -f $p.invested_correlation, $lim) }

Sec "3  every same-bet pair has a reason over a limit"
$bad = 0
foreach ($r in @($rows | Where-Object { $_.same_bet })) {
    $over = ([double]$r.overlap_pct -ge $limO) -or ($null -ne $r.correlation -and [double]$r.correlation -ge $lim) -or
            ($null -ne $r.invested_correlation -and [double]$r.invested_correlation -ge $lim)
    if (-not $r.why -or -not $over) { Bad ("{0} / {1} marked same bet without a reason over a limit" -f $r.a, $r.b); $bad++ }
}
if ($bad -eq 0) { Ok ("{0} same-bet pair(s), each with a reason" -f @($rows | Where-Object { $_.same_bet }).Count) }

Sec "4  not changed: the old tests still mark their pairs"
$miss = @($rows | Where-Object { -not $_.same_bet -and (([double]$_.overlap_pct -ge $limO) -or
    ($null -ne $_.correlation -and [double]$_.correlation -ge $lim)) })
if ($miss.Count -eq 0) { Ok "every pair over the strategy-correlation or shared-market limit is still the same bet" }
else { foreach ($r in $miss) { Bad ("{0} / {1} over a limit but not marked" -f $r.a, $r.b) } }
Stop-Smoke
