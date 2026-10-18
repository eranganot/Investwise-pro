# SMOKE - PR 135c: an overweight sleeve is a choice -- trim, or raise the size (D104).
#
#   .\scripts\smoke\smoke-pr135c-overweight-choice.ps1 -Sha <short sha of the merge>
#
#   1  any overweight sleeve card carries both options and a recommendation with its reason
#      (SKIP when no sleeve is over its size by more than the band -- the owner's book today)
#   2  the PR 135b sleeve cards still answer (not degraded)
#   3  nothing else changed: shell iw-v69; endpoints behind the login
#
# READ-ONLY (never accepts a card). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 135c"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1-2  Today"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $t = Api GET "/api/v1/recommendations"
    $all = @($t.recommendations) + @($t.more_recommendations)
    $ow = @($all | Where-Object { $_.apply.kind -eq "overweight_choice" })
    if (-not $ow.Count) {
        $plain = @($all | Where-Object { $_.apply.kind -eq "trim" -and $_.meta.ticker -and $_.title -match "drifted" })
        if ($plain.Count) { Skip "an overweight card without a scored raise (scores are about an earlier book, or not scored yet)" }
        else { Skip "no sleeve is over its size by more than the band" }
    }
    foreach ($c in $ow) {
        $a = $c.apply
        if ($a.options.trim -and $a.options.raise -and $a.recommended -and $a.recommended_why) {
            Ok ("{0}: trim to {1}% or raise to {2}%; recommended {3} -- {4}" -f $a.ticker, $a.trim_to_pct, $a.raise_to_pct, $a.recommended, $a.recommended_why)
        } else { Bad "overweight card incomplete: $($c.title)" }
    }
    if ($t.degraded -contains "sleeve_moves") { Bad "sleeve cards degraded" } else { Ok "sleeve cards not degraded" }
}

Sec "3  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v69") { Ok "shell iw-v69" } else { Bad "sw.js is not iw-v69" } } catch { Bad "/app/sw.js unreachable" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/recommendations" -TimeoutSec 60 | Out-Null; Bad "/recommendations answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/recommendations needs a key (401)" } else { Bad "/recommendations answered $code" } }

Stop-Smoke
