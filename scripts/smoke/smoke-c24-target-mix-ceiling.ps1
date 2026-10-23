# SMOKE - C24 (D125): the target mix manages the 2x/3x sleeves to the leveraged ceiling; the excess is in the core.
#
#   .\scripts\smoke\smoke-c24-target-mix-ceiling.ps1 -Sha <short sha of the merge>
#
#   1  /plan/sleeves says what the 2x/3x sleeves are set to and managed to (managed <= ceiling)
#   2  the book target: ceiling_cut = chosen - managed; it still sums to 1
#   3  the shell is iw-v74 or later (the Plan tab note)
#   4  NOT CHANGED: only 2x/3x tickers are cut; PR 148's ceiling is 44 by default
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C24"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$sl = Api GET "/api/v1/plan/sleeves"
$lc = $sl.leveraged_ceiling

Sec "1  the sleeves say what they are managed to"
if (-not $lc) { Bad "no leveraged_ceiling on /plan/sleeves (a build before C24)"; Stop-Smoke }
Note ("2x/3x sleeves set to {0}%, managed to {1}% (ceiling {2}%)" -f $lc.chosen_pct, $lc.managed_pct, $lc.ceiling_pct)
if ([double]$lc.ceiling_pct -le 0) { Skip "ceiling set to 0: managed as chosen" }
elseif ([double]$lc.managed_pct -le [double]$lc.ceiling_pct + 0.05) { Ok "managed at or under the ceiling" }
else { Bad "managed above the ceiling" }

Sec "2  the book target"
$bt = (Api GET "/api/v1/recommendations").book_target
$cut = 0.0
if ($bt.ceiling_cut) { foreach ($p in $bt.ceiling_cut.PSObject.Properties) { $cut += [double]$p.Value } }
$want = ([double]$lc.chosen_pct - [double]$lc.managed_pct) / 100.0
if ([math]::Abs($cut - $want) -le 0.002) { Ok ("ceiling_cut {0:N3} = chosen - managed" -f $cut) } else { Bad ("ceiling_cut {0:N3}, expected {1:N3}" -f $cut, $want) }
$sum = 0.0; foreach ($p in $bt.target.PSObject.Properties) { $sum += [double]$p.Value }
if ([math]::Abs($sum - 1.0) -le 0.005) { Ok ("target sums to {0:N4}" -f $sum) } else { Bad ("target sums to {0:N4}" -f $sum) }

Sec "3  the shell"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v(\d+)" -and [int]$Matches[1] -ge 74) { Ok ("shell iw-v{0}" -f $Matches[1]) } else { Bad "shell older than iw-v74" } } catch { Bad "/app/sw.js unreachable" }

Sec "4  not changed"
$funds = @((Api GET "/api/v1/plan").leveraged_funds)
$cutPlain = @($bt.ceiling_cut.PSObject.Properties | Where-Object { $funds -notcontains $_.Name })
if ($cutPlain.Count) { Bad ("the ceiling cut a plain ticker: {0}" -f (($cutPlain | ForEach-Object { $_.Name }) -join ", ")) } else { Ok "the ceiling cut 2x/3x tickers only" }
$d = (Api GET "/api/v1/plan").thresholds_defaults.leveraged_ceiling_pct
if ([double]$d -eq 44) { Ok "default ceiling 44" } else { Bad ("default ceiling {0}" -f $d) }
Stop-Smoke
