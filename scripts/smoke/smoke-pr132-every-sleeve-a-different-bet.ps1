# SMOKE - PR 132: every sleeve must be a different bet (D97).
#
#   .\scripts\smoke\smoke-pr132-every-sleeve-a-different-bet.ps1 -Sha <short sha of the merge>
#
#   1  the correlation table: every catalogue pair, with shared market and (once measured) correlation
#   2  the sleeves view marks look-alikes (same_bet) and held pairs that are one bet (held_same_bets)
#   3  both limits are settings with their defaults (40% shared market, 0.80 correlation)
#   4  nothing else changed: shell iw-v65, the table is behind the login
#
# READ-ONLY (the table is filled by the 03:50 job or POST /strategies/correlations/refresh,
# which this script does not call). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 132"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the correlation table"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $t = Api GET "/api/v1/strategies/correlations"
    if ($null -eq $t) { Bad "/strategies/correlations did not answer" }
    else {
        $n = @($t.pairs).Count
        if ($n -ge 28) { Ok "$n catalogue pairs listed" } else { Bad "only $n pairs" }
        if ($t.ok) {
            Ok ("measured {0}; limits {1}% shared market, {2} correlation" -f $t.as_of, $t.overlap_limit_pct, $t.correlation_limit)
            foreach ($p in @($t.pairs)) {
                Note ("{0,6} corr  {1,5}% shared  {2,-5} {3} + {4}" -f $p.correlation, $p.overlap_pct, $(if ($p.same_bet) { "SAME" } else { "" }), $p.a_name, $p.b_name)
            }
        } else { Skip "not measured yet: the 03:50 job (or POST /strategies/correlations/refresh) fills it; shared-market test applies meanwhile" }
        $ndx = @($t.pairs) | Where-Object { ($_.a -eq "btm_trend_tqqq" -and $_.b -eq "btm_vol_target_tqqq") -or ($_.b -eq "btm_trend_tqqq" -and $_.a -eq "btm_vol_target_tqqq") }
        if ($ndx -and $ndx.same_bet) { Ok "the two TQQQ rules are one bet ($($ndx.overlap_pct)% Nasdaq-100)" } else { Bad "the two TQQQ rules are not flagged as one bet" }
    }
}

Sec "2  the sleeves view"
if (-not $haveKey) { Skip "no agent key" }
else {
    $sl = Api GET "/api/v1/plan/sleeves"
    if ($null -eq $sl) { Bad "/plan/sleeves did not answer" }
    else {
        if ($null -ne $sl.same_bet -and $null -ne $sl.held_same_bets) { Ok "same_bet and held_same_bets present" } else { Bad "same_bet / held_same_bets missing" }
        $marked = @($sl.same_bet.PSObject.Properties | ForEach-Object { "$($_.Name) (as $($_.Value[0].name))" })
        Note ("look-alikes of your sleeves: {0}" -f $(if ($marked) { $marked -join '; ' } else { 'none' }))
        $pairs = @($sl.held_same_bets)
        if ($pairs.Count -eq 0) { Ok "no two sleeves you hold are the same bet" }
        else { foreach ($p in $pairs) { Note ("HELD SAME BET: {0} + {1}: {2}" -f $p.a_name, $p.name, $p.why) }; Ok "$($pairs.Count) held pair(s) reported for the wave-4 card" }
    }
}

Sec "3  limits are settings"
if (-not $haveKey) { Skip "no agent key" }
else {
    $pl = Api GET "/api/v1/plan"
    $spec = $pl.thresholds_spec; $def = $pl.thresholds_defaults
    if ($spec.sleeve_overlap_alert_pct -and $spec.sleeve_correlation_limit) { Ok "both limits on the thresholds sheet" } else { Bad "a limit is missing from thresholds_spec" }
    if ([double]$def.sleeve_overlap_alert_pct -eq 40 -and [double]$def.sleeve_correlation_limit -eq 0.8) { Ok "defaults 40% / 0.80" } else { Bad ("defaults {0} / {1}" -f $def.sleeve_overlap_alert_pct, $def.sleeve_correlation_limit) }
}

Sec "4  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v65") { Ok "shell iw-v65" } else { Bad "sw.js is not iw-v65" } } catch { Bad "/app/sw.js unreachable" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/strategies/correlations" -TimeoutSec 60 | Out-Null; Bad "/strategies/correlations answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/strategies/correlations needs a key (401)" } else { Bad "/strategies/correlations answered $code" } }

Stop-Smoke
