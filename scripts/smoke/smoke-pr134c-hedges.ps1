# SMOKE - PR 134c: three hedges join the catalogue (D100); a missing universe member no longer aborts round 2.
#
#   .\scripts\smoke\smoke-pr134c-hedges.ps1 -Sha <short sha of the merge>
#
#   1  the catalogue lists the three hedges, each badged, each with a measured risk tier once measured
#   2  the hedges are not "the same bet" as the owner's Volatility-Targeted Leverage sleeve
#   3  option 2 now runs: S1 measures (MMC dropped and reported) -- slow
#   4  nothing else changed: shell iw-v67; endpoints behind the login
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app", [switch]$SkipS1)
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 134c"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
$hedges = @("div_dbmf", "div_2x_gold_trend", "div_2x_trend_risk_parity")

Sec "1  the hedges in the catalogue"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $st = Api GET "/api/v1/strategies"
    $flat = @(); foreach ($g in $st.by_goal.PSObject.Properties) { $flat += @($g.Value) }
    foreach ($h in $hedges) {
        $c = $flat | Where-Object { $_.id -eq $h } | Select-Object -First 1
        if (-not $c) { Bad "$h missing from the catalogue"; continue }
        $tier = $c.risk_label; $bt = $c.backtest
        if (@($c.badges) -contains "Hedge for a leveraged sleeve") { Ok ("{0}: badged ({1}); risk {2}; {3}" -f $c.name, (@($c.badges) -join ' / '), $tier, $(if ($bt) { "measured $($bt.cagr_pct)%/yr" } else { "not measured yet (03:30 job)" })) }
        else { Bad "$h has no hedge badge" }
    }
}

Sec "2  a different bet from your Nasdaq sleeve"
if (-not $haveKey) { Skip "no agent key" }
else {
    $sl = Api GET "/api/v1/plan/sleeves"
    foreach ($h in $hedges) {
        $c = $sl.same_bet.$h
        if ($null -eq $c) { Ok "$h is not the same bet as any sleeve you hold" } else { Note ("{0}: {1}" -f $h, $c[0].why); Skip "$h flagged same bet by measured correlation (setting decides)" }
    }
}

Sec "3  option 2 runs"
if (-not $haveKey -or $SkipS1) { Skip "skipped" }
else {
    $r = Api GET "/api/v1/strategies/round2/stocks/S1/measure" 1800
    if ($r -and $r.ok) { Ok ("S1 measured: {0}%/yr vs QQQ {1}%; dropped {2}" -f $r.window.cagr_pct, $r.tests.S1.qqq_cagr_pct, (@($r.universe_dropped) -join ',')) }
    elseif ($r) { Bad ("S1 not measured: {0} {1}" -f $r.reason, $r.detail) } else { Bad "S1 did not answer" }
}

Sec "4  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v67") { Ok "shell iw-v67" } else { Bad "sw.js is not iw-v67" } } catch { Bad "/app/sw.js unreachable" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeves" -TimeoutSec 60 | Out-Null; Bad "/plan/sleeves answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/sleeves needs a key (401)" } else { Bad "/plan/sleeves answered $code" } }

Stop-Smoke
