# SMOKE - PR 134d: every option-3 pass joins the catalogue; Quality Momentum 5 is Explore only (D101).
#
#   .\scripts\smoke\smoke-pr134d-more-hedges.ps1 -Sha <short sha of the merge>
#
#   1  the catalogue lists all ten hedges, badged; GTAA-5 badged borderline; KMLM short history
#   2  Quality Momentum 5 is explore_only (not added here -- the refusal is covered by tests)
#   3  the failures are absent (S1, S3, D5, D6, D10, D13)
#   4  nothing else changed: shell iw-v68; endpoints behind the login
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 134d"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

$hedges = @("div_dbmf", "div_2x_gold_trend", "div_2x_trend_risk_parity", "div_gtaa5", "div_trend_risk_parity",
            "div_gold_trend", "div_treasury_trend", "div_kmlm", "div_qqq_trend_short", "r2_quality_momentum_5")
$failed = @("r2_momentum_10", "r2_52w_high_leaders", "div_intl_trend", "div_gem", "div_spy_trend_short", "div_3x_treasury_trend")

Sec "1-3  the catalogue"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $st = Api GET "/api/v1/strategies"
    $flat = @(); foreach ($g in $st.by_goal.PSObject.Properties) { $flat += @($g.Value) }
    $byId = @{}; foreach ($c in $flat) { $byId[$c.id] = $c }
    foreach ($h in $hedges) {
        $c = $byId[$h]
        if (-not $c) { Bad "$h missing"; continue }
        if (@($c.badges) -contains "Hedge for a leveraged sleeve") { Ok ("{0}: {1}; risk {2}" -f $c.name, (@($c.badges) -join ' / '), $c.risk_label) } else { Bad "$h not badged" }
    }
    if (@($byId["div_gtaa5"].badges) -match "Borderline") { Ok "GTAA-5 badged borderline" } else { Bad "GTAA-5 not badged borderline" }
    if ($byId["r2_quality_momentum_5"].explore_only) { Ok "Quality Momentum 5 is Explore only" } else { Bad "Quality Momentum 5 is addable" }
    $leak = @($failed | Where-Object { $byId.ContainsKey($_) })
    if (-not $leak) { Ok "no failed candidate in the catalogue ($($flat.Count) strategies listed)" } else { Bad "failed candidates listed: $($leak -join ',')" }
}

Sec "4  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v68") { Ok "shell iw-v68" } else { Bad "sw.js is not iw-v68" } } catch { Bad "/app/sw.js unreachable" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeves" -TimeoutSec 60 | Out-Null; Bad "/plan/sleeves answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/sleeves needs a key (401)" } else { Bad "/plan/sleeves answered $code" } }

Stop-Smoke
