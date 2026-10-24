# SMOKE - D134: commodities live inside the core; the fixed carve-out is a setting, default 0.
#
#   .\scripts\smoke\smoke-d134-commodities-inside-the-core.ps1 -Sha <short sha of the merge>
#
#   1  the setting exists (Card thresholds) and its value is what the book target uses
#   2  with 0: /plan/sleeves has no commodities slice and /mix's target holds no carved Commodities
#   3  the shell is iw-v76 or later (the My mix row folds commodity holdings into the core)
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "D134"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

Sec "1  the setting"
$p = Api GET "/api/v1/plan"
if ($p.thresholds_spec.commodities_carve_out_pct) { Ok "commodities_carve_out_pct is a Card-thresholds setting" } else { Bad "no commodities_carve_out_pct setting" }
$v = if ($p.thresholds -and $null -ne $p.thresholds.commodities_carve_out_pct) { [double]$p.thresholds.commodities_carve_out_pct } else { 0.0 }

Sec "2  no stand-alone slice"
$s = Api GET "/api/v1/plan/sleeves"
$got = [double]$s.commodities_pct * 100
if ([math]::Abs($got - $v) -le 0.05) { Ok ("commodities slice {0}% = the setting ({1}%)" -f $got, $v) } else { Bad ("commodities slice {0}% vs setting {1}%" -f $got, $v) }

Sec "3  the shell"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -TimeoutSec 30
      if ($sw.Content -match "iw-v(\d+)" -and [int]$Matches[1] -ge 76) { Ok ("shell iw-v{0}" -f $Matches[1]) } else { Bad "shell older than iw-v76" } } catch { Bad "/app/sw.js unreachable" }
Stop-Smoke
