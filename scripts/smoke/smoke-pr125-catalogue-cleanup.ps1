# SMOKE - PR 125: Beat the Market catalogue clean-up (D87 retire flag, D88 measured tiers, D89 drift band).
#
#   .\scripts\smoke\smoke-pr125-catalogue-cleanup.ps1 -Sha <short sha of the merge>
#
#   1  the two retired strategies say so, and where they move to
#   2  every strategy's tier is the band its measured worst drop falls in
#   3  /strategies cards carry the measured tier and the designed one
#   4  vol targeting trades far less once re-measured (SKIP until the 03:30 refresh)
#   5  nothing else changed: 8 entries, 6 still offered, same engine version, data stays gated
#
# Reads only the public catalogue endpoints, so it needs no agent key.
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 125"
$script:ApiHeaders = @{}
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

function Band([double]$dd) {
    $d = [math]::Abs($dd)
    if ($d -le 36) { return "Medium" }
    if ($d -le 45) { return "Medium-high" }
    if ($d -le 60) { return "High" }
    return "Very high"
}

$bt = Api GET "/api/v1/strategies/backtests"
if (-not $bt) { Bad "/strategies/backtests did not answer"; Stop-Smoke }
$byId = @{}
foreach ($s in @($bt.strategies)) { $byId[$s.id] = $s }

Sec "1  retired, not deleted"
foreach ($sid in @("btm_swing_dip", "btm_swing_breakout")) {
    $s = $byId[$sid]
    if (-not $s) { Bad "$sid is gone from the catalogue - it must stay so a sleeve on it still resolves"; continue }
    if ($s.retired -and $s.retired.decision -eq "D87" -and $s.retired.move_to -eq "btm_qqq_hold") {
        Ok ("{0} retired {1}, moves to {2}" -f $sid, $s.retired.on, $s.retired.move_to)
    } else { Bad "$sid is not marked retired (D87)" }
}

Sec "2  measured tiers"
$checked = 0
foreach ($s in @($bt.strategies)) {
    $m = $null
    if ($s.backtest) { $m = $s.backtest.metrics }
    if ($null -eq $m -or $null -eq $m.max_drawdown_pct) { Note ("{0}: not measured, designed tier {1}" -f $s.id, $s.risk); continue }
    $want = Band ([double]$m.max_drawdown_pct)
    if ($s.measured_risk -eq $want) { $checked++ } else { Bad ("{0}: worst drop {1}% reads {2}, band says {3}" -f $s.id, $m.max_drawdown_pct, $s.measured_risk, $want) }
}
if ($checked -gt 0) { Ok "$checked measured strategies carry the tier their worst drop falls in" } else { Skip "no measured strategy to check" }
$t = $byId["btm_trend_tqqq"]
if ($t -and $t.measured_risk -eq "Very high") { Ok "Trend-Filtered Leveraged Nasdaq: Very high" } else { Bad "Trend Nasdaq tier is $($t.measured_risk)" }

Sec "3  the Explore cards"
$st = Api GET "/api/v1/strategies"
$cards = @()
if ($st) { $cards = @($st.by_goal.'Beat the Market') }
if ($cards.Count -eq 0) { Bad "/strategies has no Beat the Market cards" }
else {
    $missing = @($cards | Where-Object { -not $_.risk_label -or -not $_.designed_risk })
    if ($missing.Count -eq 0) { Ok "$($cards.Count) cards carry risk_label and designed_risk" } else { Bad "$($missing.Count) cards lack a tier field" }
    $ret = @($cards | Where-Object { $_.retired })
    if ($ret.Count -eq 2) { Ok "2 cards carry their retirement record" } else { Bad "$($ret.Count) cards marked retired, expected 2" }
}

Sec "4  vol targeting stops re-truing every drift"
$v = $byId["btm_vol_target_tqqq"]
$tr = $null
if ($v -and $v.backtest -and $v.backtest.metrics) { $tr = $v.backtest.metrics.trades_per_year }
if ($null -eq $tr) { Skip "vol targeting not measured" }
elseif ([double]$tr -lt 60) { Ok "vol targeting re-measured: $tr trades a year (was 124 in production, 162 on today's data)" }
else { Skip "still the old row ($tr trades a year): re-measured at the 03:30 refresh; run again tomorrow" }

Sec "5  nothing else changed"
if (@($bt.strategies).Count -eq 8) { Ok "8 entries" } else { Bad "$(@($bt.strategies).Count) entries, expected 8" }
$offered = @($bt.strategies | Where-Object { -not $_.retired })
if ($offered.Count -eq 6) { Ok "6 still offered" } else { Bad "$($offered.Count) offered, expected 6" }
if ($bt.engine_version -eq "a6") { Ok "engine a6 (no version bump: stored rows stay current)" } else { Bad "engine $($bt.engine_version)" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan" -TimeoutSec 30 | Out-Null; Bad "/plan answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan still needs a key (401)" } else { Bad "/plan answered $code" } }

Stop-Smoke
