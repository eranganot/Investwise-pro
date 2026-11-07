# SMOKE - PR 158: the cash rule (redeploy trigger + cash kept) is editable from the Plan page;
# the daily smoke respects the trigger.
#
#   .\scripts\smoke\smoke-pr158-cash-rule-on-the-plan-page.ps1 -Sha <short sha of the merge>
#
#   1  the served app has the cash rule in the Plan tab (Cards & cash sheet)
#   2  the plan still returns the trigger the sheet edits (cash_redeploy_at_ils, cash_target_ils)
#   3  NOT CHANGED: the Card thresholds and Risk dials sheets are still served; no card appears
#      below the trigger
# READ-ONLY (GETs only). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 158"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the Plan page has the Cash rule"
$html = ""
try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch { Bad "app shell did not load: $_" }
if ($html -match 'id="cs_at"') { Ok "Cards & cash sheet has the cash rule fields" } else { Bad "cash rule fields missing from the Plan page" }
if ($html -match 'cash_redeploy_at_ils=at') { Ok "cash rule save served" } else { Bad "save handler missing" }

Sec "2  the plan returns what the sheet edits"
if (-not $haveKey) { Skip "no agent key: set IW_SMOKE_KEY" } else {
    $p = Api GET "/api/v1/plan"
    if (-not $p) { Bad "plan did not answer" } else {
        if ($p.PSObject.Properties.Name -contains 'cash_redeploy_at_ils') { Ok "cash_redeploy_at_ils = $($p.cash_redeploy_at_ils)" } else { Bad "cash_redeploy_at_ils not in the plan" }
        if ($p.PSObject.Properties.Name -contains 'cash_target_ils') { Ok "cash_target_ils = $($p.cash_target_ils)" } else { Bad "cash_target_ils not in the plan" }
    }
}

Sec "3  NOT CHANGED"
if ($html -match 'function openThresholdsSheet' -and $html -match 'function openDialsSheet' -and $html -match 'row("Goal"') { Ok "Card thresholds and Risk dials sheets still served" } else { Bad "an existing Plan sheet disappeared" }
if ($haveKey) {
    $t = Api GET "/api/v1/recommendations"; $port = Api GET "/api/v1/portfolio"
    if ($t -and $port -and $p -and $null -ne $p.cash_redeploy_at_ils) {
        $card = @(@($t.recommendations) | Where-Object { $_.apply.kind -eq 'redeploy_cash' })[0]
        if ([double]$port.cash_ils -le [double]$p.cash_redeploy_at_ils) {
            if ($card) { Bad "a redeploy card shows at or below the trigger" } else { Ok "no redeploy card at or below the trigger" }
        } else { Skip "cash is above the trigger: card presence is covered by smoke-all" }
    } else { Skip "recommendations/portfolio/plan unavailable" }
}
Stop-Smoke
