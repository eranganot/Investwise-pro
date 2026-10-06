# SMOKE - PR 104c: the Risk-dials sheet; the crash-risk limit and on-track bar are settings.
#
#   .\scripts\smoke\smoke-pr104c-dials.ps1 -Sha <sha serving now>
#
#   1  /plan's frontier block carries both limits and where each came from
#      (plan or default: 1% / 70%)
#   2  the stored goal verdict was solved under those limits (p_star, the retune's
#      halve_floor_pct) -- else the goal cards stand down until the re-solve
#   3  the page: Risk-dials sheet present; the old form (Years invested, a second
#      objective/risk, "Save my plan") gone
#
# READ-ONLY: changes no setting. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 104c"
Assert-Build -Sha $Sha -Schema '0027_plan_goal_limits' -Why "(unchanged since 104c.0)"

Sec "1  the limits are settings"
$f = (Api GET "/api/v1/plan").frontier
if ($null -ne $f.halve_limit_pct -and $null -ne $f.goal_confidence) { Ok ("crash-risk limit {0}% ({1}); on track at {2}% ({3})" -f $f.halve_limit_pct, $f.halve_limit_source, [math]::Round([double]$f.goal_confidence * 100), $f.on_track_source) }
else { Bad "the frontier block does not carry the limits" }

Sec "2  the verdict is solved under them"
$v = Api GET "/api/v1/plan/frontier"
if ($v.ok) {
    $pstar = [double]$v.p_star; $floor = $v.rules.tuning.halve_floor_pct
    if ([math]::Abs($pstar - [double]$f.goal_confidence) -lt 1e-9) { Ok "on-track bar matches ($([math]::Round($pstar * 100))%)" } else { Skip "verdict at $pstar vs plan $($f.goal_confidence): goal cards wait for the re-solve" }
    if ($null -eq $floor) { Skip "no retune measured (no armed protection to tune)" }
    elseif ([math]::Abs([double]$floor - [double]$f.halve_limit_pct) -lt 1e-9) { Ok "crash-risk limit matches ($floor%)" } else { Skip "verdict at $floor% vs plan $($f.halve_limit_pct)%: goal cards wait for the re-solve" }
} else { Skip "no verdict yet" }

Sec "3  the page"
$html = $null; try { $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if (-not $html) { Bad "could not load the page" }
else {
    if ($html -match "function openDialsSheet\(\)") { Ok "Risk-dials sheet present" } else { Bad "no Risk-dials sheet" }
    $gone = @('id="p_hor"', 'Years invested', 'id="p_obj"', 'onclick="savePlan()"') | Where-Object { $html.Contains($_) }
    if ($gone.Count -eq 0) { Ok "the old Goal settings form is gone" } else { Bad ("still on the page: {0}" -f ($gone -join ', ')) }
}
Stop-Smoke
